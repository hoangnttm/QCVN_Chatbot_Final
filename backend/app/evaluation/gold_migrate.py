"""Upgrade the 13 v1 draft items to schema v2: resolve `expected_citation_labels` to chunks
(separator-aware prefix), derive `source_ref` from the chunk span (table/row/column from the
rule label), pre-fill `expected_numbers` from the expected answer for the author to confirm.
Migrated items are `tier=B`, `split=dev`, `source=v1_migrated`, `check_level=authored`."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.evaluation.gold_resolver import Resolver
from app.evaluation.gold_schema import (
    ExpectedBehaviour,
    ExpectedConditions,
    ExpectedNumber,
    GoldItem,
    RelevantChunk,
    SourceRef,
)
from app.evaluation.gold_validate import dates_in, label_matches
from app.qa.validator import numbers

_ZONE_RE = re.compile(r", Cột ([ABC])$")
_FLOW_RE = re.compile(r", F (<=|>) 2000")

STRATUM_BY_ID = {
    "G-WW-001": "WW-B1-FLOWZONE",
    "G-WW-002": "WW-B2-COND",
    "G-WW-003": "WW-B2-PLAIN",
    "G-WW-004": "TRANSITION",
    "G-WW-005": "METHOD",
    "G-WW-006": "FREQ-M01",
    "G-EM-001": "EM-B1-EQUIP",
    "G-EM-002": "EM-B1-EQUIP",
    "G-EM-003": "SAMPLING",
    "G-EFF-001": "EFFECTIVITY",
    "G-EFF-002": "EFFECTIVITY",
    "G-OOS-001": "NEG-OOS",
    "G-OOS-002": "NEG-OOS",
}


def resolve_label(
    resolver: Resolver, label: str, all_chunks: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (chunk, rule) for a gold label: exact chunk label, else the row chunk of a
    rule-level label (`…, TT 1, F > 2000, Cột B`), else the longest chunk label that is a
    ', '-boundary prefix of the gold label."""
    exact = [c for c in all_chunks if c["citation_label"] == label]
    if len(exact) == 1:
        return exact[0], None
    if exact:
        return sorted(exact, key=lambda c: c["chunk_id"])[0], None
    rule = None
    for r in getattr(resolver, "all_rules", lambda: [])():
        if r["citation_label"] == label:
            rule = r
            break
    if rule:
        row_chunk = resolver.chunk(f"chunk:{rule['row_id']}")
        if row_chunk:
            return row_chunk, rule
    cands = [c for c in all_chunks if label_matches(label, c["citation_label"])]
    if cands:
        best = max(cands, key=lambda c: len(c["citation_label"]))
        return best, None
    # registry facts: v1 omitted the audit suffix " (do quản trị viên khai báo ...)"
    reg = [c for c in all_chunks if c["citation_label"].startswith(label + " (")]
    if len(reg) == 1:
        return reg[0], None
    return None, None


def source_ref_of(chunk: dict[str, Any], rule: dict[str, Any] | None, label: str) -> SourceRef:
    sp = chunk["span"]
    ref = SourceRef(
        document_id=chunk["document_id"],
        file_sha256=sp["file_hash"],
        kind="registry" if chunk.get("chunk_type") == "registry_fact" else "text",
        line_start=sp["line_start"],
        line_end=sp["line_end"],
        pdf_page=sp.get("pdf_page_start"),
        printed_page=sp.get("printed_page"),
    )
    if chunk.get("chunk_type") == "table_row":
        src = chunk.get("source_id", "")
        table_code = src.rsplit(".r", 1)[0] if ".r" in src else None
        row_no = src.rsplit(".r", 1)[1] if ".r" in src else None
        col: list[str] = []
        mf = _FLOW_RE.search(label)
        if mf:
            col.append("F<=2000" if mf.group(1) == "<=" else "F>2000")
        mz = _ZONE_RE.search(label)
        if mz:
            col.append(mz.group(1))
        ref = ref.model_copy(
            update={"table_code": table_code, "row_no": row_no, "column_path": col or None}
        )
    return ref


def migrate_v1(path_v1: Path, resolver: Resolver) -> tuple[list[GoldItem], list[str]]:
    problems: list[str] = []
    all_chunks = getattr(resolver, "all_chunks", lambda: [])()
    items: list[GoldItem] = []
    with open(path_v1, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            raw = json.loads(line)
            relevant: list[RelevantChunk] = []
            src_ref: SourceRef | None = None
            rules: list[str] = []
            cond = ExpectedConditions()
            labels: list[str] = []
            for i, lbl in enumerate(raw.get("expected_citation_labels", [])):
                chunk, rule = resolve_label(resolver, lbl, all_chunks)
                if chunk is None:
                    problems.append(f"{raw['id']}: label not resolved: {lbl!r}")
                    labels.append(lbl)
                    continue
                # v2 rule: labels are copied byte-for-byte (registry facts get their audit suffix)
                labels.append(rule["citation_label"] if rule else chunk["citation_label"])
                ref = source_ref_of(chunk, rule, lbl)
                relevant.append(
                    RelevantChunk(
                        chunk_id=chunk["chunk_id"],
                        citation_label=chunk["citation_label"],
                        grade=2,
                        source_ref=ref,
                    )
                )
                if i == 0:
                    src_ref = ref
                if rule:
                    rules.append(rule["rule_code"])
                    cond = ExpectedConditions(
                        zone=rule.get("zone"),
                        flow_band=ref.column_path[0]
                        if ref.column_path and ref.column_path[0].startswith("F")
                        else None,
                        o2_ref=rule.get("ref_o2"),
                        equipment_path=(
                            next(
                                (
                                    c.get("value")[-1]
                                    for c in rule.get("conditions", [])
                                    if c.get("type") == "equipment_path"
                                    and isinstance(c.get("value"), list)
                                ),
                                None,
                            )
                        ),
                        fuel=next(
                            (
                                c.get("value")
                                for c in rule.get("conditions", [])
                                if c.get("type") == "fuel_type"
                            ),
                            None,
                        ),
                        receiving_body=next(
                            (
                                c.get("value")
                                for c in rule.get("conditions", [])
                                if c.get("type") == "receiving_body"
                            ),
                            None,
                        ),
                    )
            must = bool(raw.get("must_abstain"))
            mode = "answer"
            if must:
                mode = "pointer" if relevant else "abstain"
            answer_text = raw.get("expected_answer", "")
            dates = sorted(dates_in(answer_text) - dates_in(raw.get("question", "")))
            nums = sorted(numbers(answer_text) - numbers(raw.get("question", "")))
            exp_numbers = (
                []
                if must
                else [
                    ExpectedNumber(value=d, display=d, role="date", must_appear=False)
                    for d in dates
                ]
                + [
                    ExpectedNumber(value=n, display=n, role="other", must_appear=False)
                    for n in nums
                ]
            )
            as_of = raw.get("as_of_date")
            notes = raw.get("notes", "")
            if as_of is None and raw["intent"] in ("effectivity", "applicability"):
                as_of = "2026-01-15"
                notes = (notes + " " if notes else "") + "[v2] as_of_date mặc định 2026-01-15"
            item = GoldItem(
                id=raw["id"],
                tier="B",
                question=raw["question"],
                domain=raw["domain"],
                intent=raw["intent"],
                as_of_date=as_of,
                split="dev",
                stratum=STRATUM_BY_ID.get(raw["id"]),
                source="v1_migrated",
                answer_type="pointer" if must and relevant else ("none" if must else "value"),
                expected_answer=raw["expected_answer"],
                expected_numbers=exp_numbers,
                expected_conditions=cond,
                target_rule_codes=rules,
                source_ref=src_ref,
                relevant=relevant,
                expected_citation_labels=labels,
                must_abstain=must,
                expected_behaviour=ExpectedBehaviour(mode=mode),
                expected_warnings=raw.get("expected_warnings", []),
                trap_type="out_of_scope" if raw["domain"] == "out_of_scope" else None,
                tags=["v1"],
                notes=notes,
                status=raw.get("status", "draft"),
                reviewer=raw.get("reviewer"),
                check_level="authored",
            )
            items.append(item)
    return items, problems
