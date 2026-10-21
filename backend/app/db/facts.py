"""Fact layer derived from the ingestion artefacts (plan 2026-10-06, G2 steps 2.2–2.5).

Pure functions: artefacts + registry in, database rows out. Nothing here reads the database and
nothing is produced by an LLM.

- `rule_condition_rows`  one row per condition of a limit rule, on a declared dimension
- `fact_rows`            the registry of citable statements: limit, definition, transition,
                         monitoring, method, relation, registry — each with its legal address,
                         the chunk that carries it, a trust level and a validity interval
- `document_validity`    when the statements of a document are in force (from the registry)
- `trust_of`             approved (expert) > auto_verified (independent reader agrees) > pending
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from app.ingestion.address import node_address
from app.ingestion.models import (
    EvidenceChunk,
    ExtractedRelations,
    LimitRule,
    MonitoringRequirement,
    ParsedStructure,
    ParsedTables,
)
from app.ontology.schema import Dictionaries
from app.registry.schema import Document, Registry

TRUST_APPROVED = "approved"
TRUST_AUTO = "auto_verified"
TRUST_PENDING = "pending"
EFFECTIVE = "effective_date"
SUPERSEDING = ("REPLACES", "REPEALS")
# registry facts that are themselves citable statements, and the fact type they become
REGISTRY_FACT_TYPE = {EFFECTIVE: "effectivity", "partial_effective_date": "effectivity"}


def trust_of(review_status: str | None, verified: bool) -> str:
    if review_status == TRUST_APPROVED:
        return TRUST_APPROVED
    return TRUST_AUTO if verified else TRUST_PENDING


def _date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _effective(doc: Document) -> dt.date | None:
    return next(
        (_date(f.value) for f in doc.facts if f.fact_type == EFFECTIVE and f.scope is None), None
    )


def document_validity(doc_id: str, reg: Registry) -> dict[str, Any]:
    """`valid_from`: the document's own effective date, else that of the circular that
    promulgates it. `valid_to`: the day before a document that fully replaces or repeals it comes
    into force. `basis` names the registry entries used, so the interval can be explained."""
    docs = {d.document_id: d for d in reg.documents}
    doc = docs.get(doc_id)
    out: dict[str, Any] = {"valid_from": None, "valid_to": None, "basis": []}
    if doc is None:
        return out
    start = _effective(doc)
    if start is not None:
        out["basis"].append(f"{doc_id}: {EFFECTIVE}")
    else:
        for other in reg.documents:
            if any(r.type == "PROMULGATES" and r.target == doc_id for r in other.relations):
                start = _effective(other)
                if start is not None:
                    out["basis"].append(f"{other.document_id}: {EFFECTIVE} (PROMULGATES {doc_id})")
                    break
    out["valid_from"] = start
    ends = []
    for other in reg.documents:
        for r in other.relations:
            if r.type in SUPERSEDING and r.target == doc_id and r.scope == "full":
                since = document_validity(other.document_id, reg)["valid_from"]
                if since is not None:
                    ends.append((since - dt.timedelta(days=1), f"{other.document_id}: {r.type}"))
    if ends:
        end, why = min(ends)
        out["valid_to"] = end
        out["basis"].append(why)
    return out


def rule_condition_rows(rules: list[LimitRule], d: Dictionaries) -> list[dict[str, Any]]:
    """Conditions of the limit rules on the dimensions of `condition_dimensions.yaml`.
    A condition on an undeclared dimension stops the load: it must be added to the registry of
    dimensions (data) or the table grammar is wrong."""
    known = {x.name: x for x in d.condition_dimensions}
    rows: list[dict[str, Any]] = []
    for r in rules:
        for pos, c in enumerate(r.conditions):
            dim = known.get(c.type)
            if dim is None:
                raise ValueError(
                    f"{r.rule_code}: condition dimension {c.type!r} is not declared in "
                    "data/dictionaries/condition_dimensions.yaml"
                )
            number = (
                c.value
                if isinstance(c.value, int | float) and not isinstance(c.value, bool)
                else None
            )
            text = (
                None
                if number is not None or c.value is None
                else c.value
                if isinstance(c.value, str)
                else json.dumps(c.value, ensure_ascii=False)
            )
            rows.append(
                {
                    "rule_code": r.rule_code,
                    "document_id": r.document_id,
                    "position": pos,
                    "dimension": c.type,
                    "op": c.op,
                    "value_num": float(number) if number is not None else None,
                    "value_text": text,
                    "unit": c.unit,
                    "raw_text": c.text,
                    "inherited_from": c.inherited_from,
                    # a scoped dimension (zone) means something only inside its document
                    "scope": (r.zone_scheme or r.document_id) if dim.scoped_by else None,
                }
            )
    return rows


def _fact(
    fact_id: str,
    fact_type: str,
    doc_id: str,
    chunk_id: str | None,
    address: list[dict[str, Any]],
    label: str | None,
    validity: dict[str, Any],
    ref: tuple[str, str],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "fact_type": fact_type,
        "document_id": doc_id,
        "chunk_id": chunk_id,
        "address": address,
        "address_label": label,
        "locator": extra.get("locator"),
        "quote": extra.get("quote"),
        "method": extra.get("method", "structure_parser"),
        "trust": extra.get("trust", TRUST_PENDING),
        "valid_from": validity["valid_from"],
        "valid_to": validity["valid_to"],
        "ref_table": ref[0],
        "ref_id": ref[1],
    }


def fact_rows(
    doc: Document,
    structure: ParsedStructure,
    tables: ParsedTables,
    rules: list[LimitRule],
    relations: ExtractedRelations,
    monitoring: list[MonitoringRequirement],
    chunks: list[EvidenceChunk],
    validity: dict[str, Any],
    verified: set[str],
) -> list[dict[str, Any]]:
    """Every citable statement of one document. A statement without an evidence chunk is not a
    fact (it could not be cited), so it is left out and shows up in the quality report instead."""
    doc_id = doc.document_id
    nodes = structure.by_id()
    chunk_ids = {c.chunk_id for c in chunks}
    by_source = {c.source_id: c for c in chunks}
    out: list[dict[str, Any]] = []

    for r in rules:
        chunk_id = f"chunk:{r.row_id}"
        out.append(
            _fact(
                f"fact:limit:{r.rule_code}",
                "limit",
                doc_id,
                chunk_id if chunk_id in chunk_ids else None,
                r.address,
                r.citation_label,
                validity,
                ("limit_rules", r.rule_code),
                locator=r.column_key,
                quote=r.cell_raw,
                method="rule_extraction",
                trust=trust_of(r.review_status, r.rule_code in verified),
            )
        )

    for n in structure.nodes:
        if n.kind == "Definition" and f"chunk:{n.node_id}" in chunk_ids:
            out.append(
                _fact(
                    f"fact:definition:{n.node_id}",
                    "definition",
                    doc_id,
                    f"chunk:{n.node_id}",
                    node_address(n.node_id, nodes),
                    n.citation_label,
                    validity,
                    ("structure_nodes", n.node_id),
                    quote=n.term,
                )
            )

    for t in relations.transition_rules:
        chunk_id = f"chunk:{t.source_node_id}"
        if chunk_id in chunk_ids:
            out.append(
                _fact(
                    f"fact:transition:{t.rule_id}",
                    "transition",
                    doc_id,
                    chunk_id,
                    node_address(t.source_node_id, nodes),
                    t.citation_label,
                    validity,
                    ("transition_rules", t.rule_id),
                    locator=t.kind,
                    method="relation_extraction",
                )
            )

    for i, rel in enumerate(relations.relations):
        chunk_id = f"chunk:{rel.source_node_id}" if rel.source_node_id else None
        if chunk_id in chunk_ids:
            target = rel.target_document_id or rel.target_code or "?"
            out.append(
                _fact(
                    f"fact:relation:{doc_id}:{rel.type}:{target}:{i}",
                    "relation",
                    doc_id,
                    chunk_id,
                    node_address(rel.source_node_id, nodes),
                    rel.citation_label,
                    validity,
                    ("document_relations", f"{doc_id}:{rel.type}:{target}"),
                    locator=f"{rel.type} {target}",
                    method="relation_extraction",
                )
            )

    for q in monitoring:
        if q.evidence_chunk_id in chunk_ids:
            out.append(
                _fact(
                    f"fact:monitoring:{q.requirement_id}",
                    "monitoring",
                    doc_id,
                    q.evidence_chunk_id,
                    node_address(q.source_node_id, nodes),
                    q.citation_label,
                    validity,
                    ("monitoring_requirements", q.requirement_id),
                    locator=q.requirement_type,
                    method="curated",
                )
            )

    for t in tables.tables:
        for r in t.rows:
            chunk_id = f"chunk:{r.row_id}"
            if not r.methods or chunk_id not in chunk_ids:
                continue
            row_key = r.row_id.removeprefix(f"{t.table_code}.r")
            address = [*node_address(t.node_id, nodes), {"level": "TableRow", "number": row_key}]
            for meth in r.methods:
                out.append(
                    _fact(
                        f"fact:method:{r.row_id}:{meth.order}:{meth.stage}",
                        "method",
                        doc_id,
                        chunk_id,
                        address,
                        r.citation_label,
                        validity,
                        ("table_rows", r.row_id),
                        locator=meth.code,
                        quote=meth.code,
                        method="table_parser",
                    )
                )

    by_label = {c.citation_label: c for c in chunks}
    for f in doc.facts:
        fact_type = REGISTRY_FACT_TYPE.get(f.fact_type)
        chunk = by_source.get(f"{doc_id}:registry_fact:{f.fact_type}")
        if fact_type and chunk is None and f.evidence:
            # a date read from the text: the registry names the provision ("Điều 2, Khoản 1 (dòng
            # 24)") — the fact is cited from that provision, by its legal address
            where = f.evidence.split(" (")[0].strip()
            chunk = next(
                (
                    c
                    for label, c in by_label.items()
                    # the label may go on with the page: "…, Điều 45, Khoản 2, tr. 51"
                    if label.endswith(f", {where}") or f", {where}, " in label
                ),
                None,
            )
        if fact_type and chunk is not None:
            out.append(
                _fact(
                    f"fact:{fact_type}:{doc_id}:{f.fact_type}",
                    fact_type,
                    doc_id,
                    chunk.chunk_id,
                    node_address(chunk.source_id, nodes)
                    or [{"level": "Registry", "label": f.fact_type}],
                    chunk.citation_label,
                    validity,
                    ("registry_facts", f"{doc_id}:{f.fact_type}"),
                    locator=f.fact_type,
                    quote=f.value,
                    method="registry",
                )
            )
    return out
