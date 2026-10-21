"""Read-only document endpoints (PLAN.md §8, Phase 1): registry, structure tree, tables, evidence.

Everything is served from the PostgreSQL system of record; tables are rendered from `table_rows`
(never from the source Markdown) so what the UI shows is exactly what the rules were built from.
"""

from __future__ import annotations

from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.db import models as m
from app.ontology.loader import load_dictionaries

router = APIRouter(prefix="/api/v1", tags=["documents"])
DB = Annotated[Session, Depends(get_db)]

HIDDEN_KINDS = {"Boilerplate"}


def _doc_summary(d: m.Document, session: Session) -> dict[str, Any]:
    facts = session.execute(
        select(m.RegistryFact).where(m.RegistryFact.document_id == d.document_id)
    ).scalars()
    version = session.execute(
        select(m.DocumentVersion).where(
            m.DocumentVersion.document_id == d.document_id, m.DocumentVersion.is_current.is_(True)
        )
    ).scalar_one_or_none()
    return {
        "document_id": d.document_id,
        "document_code": d.document_code,
        "document_type": d.document_type,
        "title": d.title,
        "issuer": d.issuer,
        "domain": d.domain,
        "text_status": d.text_status,
        "ingest_scope": d.ingest_scope,
        "note": d.note,
        "version": None
        if version is None
        else {
            "file_name": version.file_name,
            "format": version.format,
            "sha256": version.sha256,
            "page_count": version.page_count,
            "registered_at": version.registered_at,
        },
        "facts": [
            {
                "fact_type": f.fact_type,
                "value": f.value,
                "source": f.source,
                "evidence": f.evidence,
                "scope": f.scope,
                "declared_by": f.declared_by,
                "declared_at": f.declared_at,
            }
            for f in facts
        ],
    }


def short_code(document_code: str) -> str:
    """Compact form for navigation: "QCVN 40:2025/BTNMT" → "QCVN 40:2025",
    "06/2025/TT-BTNMT" → "TT 06/2025"."""
    parts = document_code.split("/")
    if len(parts) == 3:
        return f"{parts[2].split('-')[0]} {parts[0]}/{parts[1]}"
    return parts[0]


@router.get("/corpus")
def corpus_info(db: DB) -> dict[str, Any]:
    """What the corpus holds, for the pages that used to name it: regulated domains, documents
    (regulations first) and the sample questions of the chat page."""
    settings = get_settings()
    docs = db.execute(
        select(m.Document)
        .where(m.Document.document_type != "external_stub")
        .order_by(m.Document.document_type.desc(), m.Document.document_id)
    ).scalars()
    ui_file = settings.data_dir / "prompts" / "ui.yaml"
    ui = yaml.safe_load(ui_file.read_text("utf-8")) if ui_file.exists() else {}
    return {
        "domains": [
            {"code": d.code, "name": d.name} for d in load_dictionaries(settings=settings).domains
        ],
        "documents": [
            {
                "document_id": d.document_id,
                "document_code": d.document_code,
                "short_code": short_code(d.document_code),
                "document_type": d.document_type,
                "domain": d.domain,
            }
            for d in docs
        ],
        "sample_questions": ui.get("sample_questions", []),
    }


@router.get("/documents")
def list_documents(
    db: DB,
    include_stubs: bool = Query(False, description="also list documents outside the corpus"),
) -> list[dict[str, Any]]:
    stmt = select(m.Document).order_by(m.Document.document_type, m.Document.document_id)
    if not include_stubs:
        stmt = stmt.where(m.Document.document_type != "external_stub")
    docs = db.execute(stmt).scalars().all()
    counts = {
        doc_id: n
        for doc_id, n in db.execute(
            select(m.EvidenceChunk.document_id, func.count()).group_by(m.EvidenceChunk.document_id)
        )
    }
    out = []
    for d in docs:
        s = _doc_summary(d, db)
        s["chunks"] = counts.get(d.document_id, 0)
        out.append(s)
    return out


@router.get("/documents/{document_id}")
def get_document(document_id: str, db: DB) -> dict[str, Any]:
    d = db.get(m.Document, document_id)
    if d is None:
        raise HTTPException(404, f"document {document_id} not found")
    s = _doc_summary(d, db)
    rels = db.execute(
        select(m.DocumentRelation).where(
            (m.DocumentRelation.source_document_id == document_id)
            | (m.DocumentRelation.target_document_id == document_id)
        )
    ).scalars()
    codes = {x.document_id: x.document_code for x in db.execute(select(m.Document)).scalars()}
    s["relations"] = [
        {
            "direction": "out" if r.source_document_id == document_id else "in",
            "type": r.type,
            "source": codes.get(r.source_document_id, r.source_document_id),
            "target": codes.get(r.target_document_id, r.target_code)
            if r.target_document_id
            else r.target_code,
            "target_document_id": r.target_document_id,
            "scope": r.scope,
            "target_part": r.target_part,
            "condition": r.condition,
            "origin": r.origin,
            "evidence": r.evidence,
            "citation_label": r.citation_label,
        }
        for r in rels
    ]
    s["transition_rules"] = [
        {
            "rule_id": t.rule_id,
            "kind": t.kind,
            "text": t.text,
            "dates": t.dates,
            "relative_dates": t.relative_dates,
            "citation_label": t.citation_label,
        }
        for t in db.execute(
            select(m.TransitionRule).where(m.TransitionRule.document_id == document_id)
        ).scalars()
    ]

    def _count(model, extra=None):
        stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
        return db.execute(stmt).scalar_one()

    s["counts"] = {
        "structure_nodes": _count(m.StructureNode),
        "tables": _count(m.TableDef),
        "table_rows": _count(m.TableRow),
        "limit_rules": _count(m.LimitRule),
        "monitoring_requirements": _count(m.MonitoringRequirement),
        "evidence_chunks": _count(m.EvidenceChunk),
    }
    s["review"] = dict(
        db.execute(
            select(m.LimitRule.review_status, func.count())
            .where(m.LimitRule.document_id == document_id)
            .group_by(m.LimitRule.review_status)
        ).all()
    )
    s["tables"] = [
        {"table_code": t.table_code, "title": t.title, "node_id": t.node_id, "columns": t.columns}
        for t in db.execute(
            select(m.TableDef).where(m.TableDef.document_id == document_id)
        ).scalars()
    ]
    return s


@router.get("/documents/{document_id}/tree")
def get_tree(
    document_id: str,
    db: DB,
    include_text: bool = Query(True),
    scope: str | None = Query(None, description="in_scope | structure_only"),
) -> dict[str, Any]:
    if db.get(m.Document, document_id) is None:
        raise HTTPException(404, f"document {document_id} not found")
    stmt = (
        select(m.StructureNode)
        .where(m.StructureNode.document_id == document_id)
        .order_by(m.StructureNode.order)
    )
    nodes = db.execute(stmt).scalars().all()
    tables_by_node = {
        t.node_id: t.table_code
        for t in db.execute(
            select(m.TableDef).where(m.TableDef.document_id == document_id)
        ).scalars()
    }
    items: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for n in nodes:
        if n.kind in HIDDEN_KINDS or (scope and n.scope != scope):
            continue
        item = {
            "node_id": n.node_id,
            "kind": n.kind,
            "number": n.number,
            "label": n.label,
            "title": n.title,
            "term": n.term,
            "citation_label": n.citation_label,
            "scope": n.scope,
            "domain": n.domain,
            "span": {
                "line_start": n.line_start,
                "line_end": n.line_end,
                "pdf_page_start": n.pdf_page_start,
                "printed_page": n.printed_page,
            },
            "table_code": tables_by_node.get(n.node_id),
            "children": [],
        }
        if include_text:
            item["text"] = n.text
        items[n.node_id] = item
        parent = items.get(n.parent_id) if n.parent_id else None
        (parent["children"] if parent else roots).append(item)
    return {"document_id": document_id, "nodes": len(items), "tree": roots}


@router.get("/tables/{table_code}")
def get_table(table_code: str, db: DB) -> dict[str, Any]:
    t = db.get(m.TableDef, table_code)
    if t is None:
        raise HTTPException(404, f"table {table_code} not found")
    rows = db.execute(
        select(m.TableRow)
        .where(m.TableRow.table_code == table_code)
        .order_by(m.TableRow.line_start, m.TableRow.row_id)
    ).scalars()
    rules: dict[str, list[dict[str, Any]]] = {}
    for r in db.execute(select(m.LimitRule).where(m.LimitRule.table_code == table_code)).scalars():
        rules.setdefault(r.row_id, []).append(
            {
                "rule_code": r.rule_code,
                "zone": r.zone,
                "op": r.op,
                "value": r.value,
                "min": r.min,
                "max": r.max,
                "ref_o2": r.ref_o2,
                "unit_id": r.unit_id,
                "parameter_id": r.parameter_id,
                "review_status": r.review_status,
                "citation_label": r.citation_label,
            }
        )
    node = db.get(m.StructureNode, t.node_id)
    footnotes = {}
    return {
        "table_code": t.table_code,
        "document_id": t.document_id,
        "title": t.title,
        "citation_label": node.citation_label if node else None,
        "columns": t.columns,
        "header_rows": t.header_rows,
        "warnings": t.warnings,
        "rows": [
            {
                "row_id": r.row_id,
                "row_no": r.row_no,
                "row_path": r.row_path,
                "is_group_header": r.is_group_header,
                "inherits_from": r.inherits_from,
                "cells": r.cells,
                "raw_cells": r.raw_cells,
                "entities": r.entities,
                "values": r.values,
                "conditions": r.conditions,
                "exceptions": r.exceptions,
                "methods": r.methods,
                "conditional_parameters": r.conditional_parameters,
                "vsic_codes": r.vsic_codes,
                "coefficient": r.coefficient,
                "footnote_refs": r.footnote_refs,
                "citation_label": r.citation_label,
                "parse_warnings": r.parse_warnings,
                "span": {"line_start": r.line_start, "printed_page": r.printed_page},
                "rules": rules.get(r.row_id, []),
            }
            for r in rows
        ],
        "footnotes": footnotes
        or {
            f: (db.get(m.StructureNode, f).text if db.get(m.StructureNode, f) else None)
            for f in {
                ref
                for r in db.execute(
                    select(m.TableRow.footnote_refs).where(m.TableRow.table_code == table_code)
                ).scalars()
                for ref in r
            }
        },
    }


@router.get("/evidence/{chunk_id:path}")
def get_evidence(chunk_id: str, db: DB) -> dict[str, Any]:
    c = db.get(m.EvidenceChunk, chunk_id)
    if c is None:
        raise HTTPException(404, f"chunk {chunk_id} not found")
    return {
        "chunk_id": c.chunk_id,
        "document_id": c.document_id,
        "chunk_type": c.chunk_type,
        "domain": c.domain,
        "scope": c.scope,
        "source_id": c.source_id,
        "text": c.text,
        "context_text": c.context_text,
        "citation_label": c.citation_label,
        "entities": c.entities,
        "footnote_refs": c.footnote_refs,
        "text_status": c.text_status,
        "span": {
            "file_hash": c.file_hash,
            "line_start": c.line_start,
            "line_end": c.line_end,
            "pdf_page_start": c.pdf_page_start,
            "printed_page": c.printed_page,
        },
    }


@router.get("/documents/{document_id}/monitoring")
def get_monitoring(document_id: str, db: DB) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": q.requirement_id,
            "requirement_type": q.requirement_type,
            "domain": q.domain,
            "summary": q.summary,
            "text": q.text,
            "conditions": q.conditions,
            "parameters": q.parameters,
            "references": q.references,
            "citation_label": q.citation_label,
            "evidence_chunk_id": q.evidence_chunk_id,
            "review_status": q.review_status,
        }
        for q in db.execute(
            select(m.MonitoringRequirement)
            .where(m.MonitoringRequirement.document_id == document_id)
            .order_by(m.MonitoringRequirement.requirement_id)
        ).scalars()
    ]
