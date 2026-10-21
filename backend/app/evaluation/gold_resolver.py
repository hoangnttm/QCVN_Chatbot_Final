"""Resolvers answer "does this chunk / rule / row exist and what is its exact label?" for the
gold validator and the authoring helpers. `DerivedResolver` reads `data/derived/*` (no database,
works in CI after `ingest.py all`); `DbResolver` reads PostgreSQL."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as m
from app.evaluation.gold_source import Derived


class Resolver(Protocol):
    def chunk(self, chunk_id: str) -> dict[str, Any] | None: ...
    def chunks_by_label_prefix(self, prefix: str) -> list[dict[str, Any]]: ...
    def rule(self, rule_code: str) -> dict[str, Any] | None: ...
    def rules_for_row(self, row_id: str) -> list[dict[str, Any]]: ...
    def rules_matching(
        self, table_code: str, parameter_id: str | None, **axes: Any
    ) -> list[dict[str, Any]]: ...
    def row(self, row_id: str) -> dict[str, Any] | None: ...
    def rows(self, table_code: str) -> list[dict[str, Any]]: ...
    def table(self, table_code: str) -> dict[str, Any] | None: ...
    def transition(self, rule_id: str) -> dict[str, Any] | None: ...
    def monitoring(self, requirement_id: str) -> dict[str, Any] | None: ...
    def label_exists(self, label: str) -> bool: ...


RECEIVING_BODY_KEYWORDS = {
    "lake": "hồ",
    "river": "sông",
    "sea": "biển",
    "saline_brackish": "mặn",
}


def _norm40(text: str) -> str:
    import re as _re
    import unicodedata as _ud

    t = _ud.normalize("NFC", text).lower()
    t = _re.sub(r"\s+", " ", t).strip()
    return t[:40]


def _cond_value(conds: list[dict[str, Any]], ctype: str) -> Any:
    for c in conds or []:
        if c.get("type") == ctype:
            return c.get("value")
    return None


def rule_matches_axes(rule: dict[str, Any], axes: dict[str, Any]) -> bool:
    """Axis semantics shared by both resolvers (gold `expected_conditions` → rule conditions)."""
    conds = rule.get("conditions") or []
    for k, v in axes.items():
        if v in (None, "", []):
            continue
        if k == "zone":
            if str(rule.get("zone")) != str(v):
                return False
        elif k == "flow_band":
            fv = _cond_value(conds, "flow_regime")
            op = next((c.get("op") for c in conds if c.get("type") == "flow_regime"), None)
            want = "F<=2000" if v == "F<=2000" else "F>2000"
            have = None
            if fv is not None:
                have = "F<=2000" if op in ("<=", "<") else "F>2000"
            if have != want:
                return False
        elif k == "receiving_body":
            rv = _cond_value(conds, "receiving_body")
            if rv is None:
                return False
            kw = RECEIVING_BODY_KEYWORDS.get(str(v), str(v)).lower()
            if kw not in str(rv).lower():
                return False
        elif k == "sector_scope_text":
            sv = _cond_value(conds, "sector_scope")
            if sv is None:
                return False
            a = _norm40(str(v))
            b = _norm40(str(sv) if not isinstance(sv, list) else " ".join(map(str, sv)))
            if a not in b and b not in a:
                return False
        elif k == "sector_id":
            sv = _cond_value(conds, "sector_scope")
            if isinstance(sv, list):
                if v not in sv:
                    return False
            elif sv is None or (sv != v and str(v).lower() not in str(sv).lower()):
                return False
        elif k == "equipment_path":
            ep = _cond_value(conds, "equipment_path")
            if isinstance(ep, list):
                ep = ep[-1] if ep else None
            if ep != v:
                return False
        elif k == "equipment_id":
            if rule.get("equipment_type_id") != v:
                return False
        elif k == "fuel":
            if _cond_value(conds, "fuel_type") != v:
                return False
        elif k == "o2_ref":
            if rule.get("ref_o2") is None or float(rule["ref_o2"]) != float(v):
                return False
        elif k in ("capacity_text", "footnote_rule", "transition_date", "document_code"):
            continue  # free text axes are not used for matching
    return True


class DerivedResolver:
    def __init__(self, settings: Settings | None = None) -> None:
        self.d = Derived(settings)
        self._chunks: dict[str, dict[str, Any]] = {}
        self._rules: dict[str, dict[str, Any]] = {}
        self._rows: dict[str, dict[str, Any]] = {}
        self._tables: dict[str, dict[str, Any]] = {}
        self._transitions: dict[str, dict[str, Any]] = {}
        self._monitoring: dict[str, dict[str, Any]] = {}
        self._labels: set[str] = set()
        for doc in self.d.documents():
            for c in self.d.chunks(doc):
                self._chunks[c["chunk_id"]] = c
                self._labels.add(c["citation_label"])
            for r in self.d.rules(doc):
                self._rules[r["rule_code"]] = r
                self._labels.add(r["citation_label"])
            for t in self.d.tables(doc):
                self._tables[t["table_code"]] = t
                for row in t.get("rows", []):
                    self._rows[row["row_id"]] = row
            rel = self.d._doc(doc, "relations")
            if isinstance(rel, dict):
                for tr in rel.get("transition_rules", []):
                    self._transitions[tr["rule_id"]] = tr
            for mr in self.d.monitoring(doc):
                if isinstance(mr, dict) and mr.get("requirement_id"):
                    self._monitoring[mr["requirement_id"]] = mr

    def chunk(self, chunk_id: str) -> dict[str, Any] | None:
        return self._chunks.get(chunk_id)

    def all_chunks(self) -> list[dict[str, Any]]:
        return list(self._chunks.values())

    def chunks_by_label_prefix(self, prefix: str) -> list[dict[str, Any]]:
        return [c for c in self._chunks.values() if c["citation_label"].startswith(prefix)]

    def rule(self, rule_code: str) -> dict[str, Any] | None:
        return self._rules.get(rule_code)

    def all_rules(self) -> list[dict[str, Any]]:
        return list(self._rules.values())

    def rules_for_row(self, row_id: str) -> list[dict[str, Any]]:
        return [r for r in self._rules.values() if r.get("row_id") == row_id]

    def rules_matching(
        self, table_code: str, parameter_id: str | None, **axes: Any
    ) -> list[dict[str, Any]]:
        out = []
        for r in self._rules.values():
            if r.get("table_code") != table_code:
                continue
            if parameter_id and r.get("parameter_id") != parameter_id:
                continue
            if rule_matches_axes(r, axes):
                out.append(r)
        return out

    def row(self, row_id: str) -> dict[str, Any] | None:
        return self._rows.get(row_id)

    def rows(self, table_code: str) -> list[dict[str, Any]]:
        t = self._tables.get(table_code)
        return list(t.get("rows", [])) if t else []

    def table(self, table_code: str) -> dict[str, Any] | None:
        return self._tables.get(table_code)

    def transition(self, rule_id: str) -> dict[str, Any] | None:
        return self._transitions.get(rule_id)

    def monitoring(self, requirement_id: str) -> dict[str, Any] | None:
        return self._monitoring.get(requirement_id)

    def label_exists(self, label: str) -> bool:
        return label in self._labels


def _row(obj: m.TableRow) -> dict[str, Any]:
    return {
        "row_id": obj.row_id,
        "table_code": obj.table_code,
        "row_no": obj.row_no,
        "row_path": obj.row_path,
        "is_group_header": obj.is_group_header,
        "inherits_from": obj.inherits_from,
        "cells": obj.cells,
        "raw_cells": obj.raw_cells,
        "entities": obj.entities,
        "values": obj.values,
        "conditions": obj.conditions,
        "exceptions": obj.exceptions,
        "footnote_refs": obj.footnote_refs,
        "citation_label": obj.citation_label,
        "parse_warnings": obj.parse_warnings,
        "span": {
            "file_hash": obj.file_hash,
            "line_start": obj.line_start,
            "line_end": obj.line_end,
            "pdf_page_start": obj.pdf_page_start,
            "pdf_page_end": obj.pdf_page_end,
            "printed_page": obj.printed_page,
        },
    }


def _rule(obj: m.LimitRule) -> dict[str, Any]:
    return {
        "rule_code": obj.rule_code,
        "document_id": obj.document_id,
        "table_code": obj.table_code,
        "row_id": obj.row_id,
        "parameter_raw": obj.parameter_raw,
        "parameter_id": obj.parameter_id,
        "unit_raw": obj.unit_raw,
        "unit_id": obj.unit_id,
        "zone": obj.zone,
        "op": obj.op,
        "value": obj.value,
        "min": obj.min,
        "max": obj.max,
        "ref_o2": obj.ref_o2,
        "conditions": obj.conditions,
        "exceptions": obj.exceptions,
        "equipment_type_id": obj.equipment_type_id,
        "citation_label": obj.citation_label,
        "footnote_refs": obj.footnote_refs,
        "review_status": obj.review_status,
    }


def _chunk(obj: m.EvidenceChunk) -> dict[str, Any]:
    return {
        "chunk_id": obj.chunk_id,
        "document_id": obj.document_id,
        "chunk_type": obj.chunk_type,
        "domain": obj.domain,
        "source_id": obj.source_id,
        "text": obj.text,
        "context_text": obj.context_text,
        "citation_label": obj.citation_label,
        "text_status": obj.text_status,
        "span": {
            "file_hash": obj.file_hash,
            "line_start": obj.line_start,
            "line_end": obj.line_end,
            "pdf_page_start": obj.pdf_page_start,
            "pdf_page_end": obj.pdf_page_end,
            "printed_page": obj.printed_page,
        },
    }


class DbResolver:
    def __init__(self, session: Session) -> None:
        self.s = session

    def chunk(self, chunk_id: str) -> dict[str, Any] | None:
        c = self.s.get(m.EvidenceChunk, chunk_id)
        return _chunk(c) if c else None

    def chunks_by_label_prefix(self, prefix: str) -> list[dict[str, Any]]:
        q = select(m.EvidenceChunk).where(m.EvidenceChunk.citation_label.like(prefix + "%"))
        return [_chunk(c) for c in self.s.execute(q).scalars()]

    def rule(self, rule_code: str) -> dict[str, Any] | None:
        r = self.s.get(m.LimitRule, rule_code)
        return _rule(r) if r else None

    def rules_for_row(self, row_id: str) -> list[dict[str, Any]]:
        q = select(m.LimitRule).where(m.LimitRule.row_id == row_id)
        return [_rule(r) for r in self.s.execute(q).scalars()]

    def rules_matching(
        self, table_code: str, parameter_id: str | None, **axes: Any
    ) -> list[dict[str, Any]]:
        q = select(m.LimitRule).where(m.LimitRule.table_code == table_code)
        if parameter_id:
            q = q.where(m.LimitRule.parameter_id == parameter_id)
        return [_rule(r) for r in self.s.execute(q).scalars() if rule_matches_axes(_rule(r), axes)]

    def row(self, row_id: str) -> dict[str, Any] | None:
        r = self.s.get(m.TableRow, row_id)
        return _row(r) if r else None

    def rows(self, table_code: str) -> list[dict[str, Any]]:
        q = select(m.TableRow).where(m.TableRow.table_code == table_code)
        return [_row(r) for r in self.s.execute(q).scalars()]

    def table(self, table_code: str) -> dict[str, Any] | None:
        t = self.s.get(m.TableDef, table_code)
        if not t:
            return None
        return {
            "table_code": t.table_code,
            "document_id": t.document_id,
            "title": t.title,
            "columns": t.columns,
            "header_rows": t.header_rows,
            "config": t.config,
            "rows": self.rows(table_code),
        }

    def transition(self, rule_id: str) -> dict[str, Any] | None:
        t = self.s.get(m.TransitionRule, rule_id)
        if not t:
            return None
        return {
            "rule_id": t.rule_id,
            "document_id": t.document_id,
            "kind": t.kind,
            "text": t.text,
            "citation_label": t.citation_label,
            "source_node_id": t.source_node_id,
        }

    def monitoring(self, requirement_id: str) -> dict[str, Any] | None:
        r = self.s.get(m.MonitoringRequirement, requirement_id)
        if not r:
            return None
        return {
            "requirement_id": r.requirement_id,
            "document_id": r.document_id,
            "requirement_type": r.requirement_type,
            "summary": r.summary,
            "text": r.text,
            "citation_label": r.citation_label,
            "evidence_chunk_id": r.evidence_chunk_id,
        }

    def label_exists(self, label: str) -> bool:
        c = self.s.execute(
            select(m.EvidenceChunk.chunk_id).where(m.EvidenceChunk.citation_label == label).limit(1)
        ).first()
        if c:
            return True
        r = self.s.execute(
            select(m.LimitRule.rule_code).where(m.LimitRule.citation_label == label).limit(1)
        ).first()
        return r is not None
