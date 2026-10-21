"""Admin: expert review workflow (M17, PLAN.md §11) on top of `app/review/service.py`."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import require_admin
from app.db import models as m
from app.ops.audit import audit
from app.review import service
from app.review.sampling import risk_score

router = APIRouter(
    prefix="/api/v1/admin/review", tags=["admin"], dependencies=[Depends(require_admin)]
)
DB = Annotated[Session, Depends(get_db)]


def _decision_dict(d: m.ReviewDecision | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {
        "id": d.id,
        "target_type": d.target_type,
        "target_id": d.target_id,
        "decision": d.decision,
        "correction": d.correction,
        "note": d.note,
        "reviewer": d.reviewer,
        "reviewed_at": d.reviewed_at,
        "source": d.source,
    }


def _rule_dict(r: m.LimitRule) -> dict[str, Any]:
    return {
        "rule_code": r.rule_code,
        "zone": r.zone,
        "op": r.op,
        "value": r.value,
        "min": r.min,
        "max": r.max,
        "ref_o2": r.ref_o2,
        "unit_raw": r.unit_raw,
        "unit_id": r.unit_id,
        "parameter_id": r.parameter_id,
        "parameter_raw": r.parameter_raw,
        "equipment_type_id": r.equipment_type_id,
        "conditions": r.conditions,
        "exceptions": r.exceptions,
        "review_status": r.review_status,
        "citation_label": r.citation_label,
    }


def _row_status(rules: list[m.LimitRule]) -> str:
    statuses = {r.review_status for r in rules}
    if not statuses:
        return "pending"
    return statuses.pop() if len(statuses) == 1 else "mixed"


def _row_dict(
    r: m.TableRow, rules: list[m.LimitRule], last: m.ReviewDecision | None, in_sample: bool
) -> dict[str, Any]:
    return {
        "row_id": r.row_id,
        "row_no": r.row_no,
        "row_path": r.row_path,
        "is_group_header": r.is_group_header,
        "inherits_from": r.inherits_from,
        "citation_label": r.citation_label,
        "cells": r.cells,
        "raw_cells": r.raw_cells,
        "entities": r.entities,
        "values": r.values,
        "conditions": r.conditions,
        "exceptions": r.exceptions,
        "footnote_refs": r.footnote_refs,
        "parse_warnings": r.parse_warnings,
        "line_start": r.line_start,
        "printed_page": r.printed_page,
        "risk_score": risk_score(r),
        "in_sample": in_sample,
        "review_status": _row_status(rules),
        "rules": [_rule_dict(x) for x in rules],
        "last_decision": _decision_dict(last),
    }


@router.get("/summary")
def review_summary(db: DB) -> dict[str, Any]:
    return service.summary(db)


@router.get("/tables/{table_code}")
def review_table(
    table_code: str,
    db: DB,
    status: str | None = None,
    risk_min: int = Query(0, ge=0),
    in_sample: bool = False,
    q: str | None = None,
) -> dict[str, Any]:
    t = db.get(m.TableDef, table_code)
    if t is None:
        raise HTTPException(404, f"table {table_code} not found")
    rows = [r for r in service.table_rows(db, table_code) if not r.is_group_header]
    rules_by_row: dict[str, list[m.LimitRule]] = {}
    for rule in db.execute(
        select(m.LimitRule)
        .where(m.LimitRule.table_code == table_code)
        .order_by(m.LimitRule.rule_code)
    ).scalars():
        rules_by_row.setdefault(rule.row_id, []).append(rule)
    check = service.sample_check(db, table_code)
    sample_ids = set(check["sample_row_ids"])
    latest = service.latest_decisions(db, "table_rows", [r.row_id for r in rows])
    items = []
    needle = (q or "").lower()
    for r in rows:
        d = _row_dict(
            r, rules_by_row.get(r.row_id, []), latest.get(r.row_id), r.row_id in sample_ids
        )
        if status and d["review_status"] != status:
            continue
        if d["risk_score"] < risk_min:
            continue
        if in_sample and not d["in_sample"]:
            continue
        if (
            needle
            and needle
            not in (" ".join(str(v) for v in r.cells.values()) + " " + r.citation_label).lower()
        ):
            continue
        items.append(d)
    return {
        "table": {
            "table_code": t.table_code,
            "document_id": t.document_id,
            "title": t.title,
            "columns": t.columns,
            "header_rows": t.header_rows,
            "citation_label": db.get(m.StructureNode, t.node_id).citation_label
            if db.get(m.StructureNode, t.node_id)
            else None,
        },
        "sample": {
            "n": len(sample_ids),
            "required": check["required"],
            "reviewed_rows": check["reviewed_rows"],
            "ok": check["ok"],
            "blocking_rows": check["blocking_rows"],
            "row_ids": sorted(sample_ids),
        },
        "rules_by_status": service.rules_by_status(db, table_code),
        "rows": items,
    }


@router.get("/rows/{row_id:path}")
def review_row(row_id: str, db: DB) -> dict[str, Any]:
    r = db.get(m.TableRow, row_id)
    if r is None:
        raise HTTPException(404, f"row {row_id} not found")
    rules = list(
        db.execute(
            select(m.LimitRule).where(m.LimitRule.row_id == row_id).order_by(m.LimitRule.rule_code)
        ).scalars()
    )
    check = service.sample_check(db, r.table_code)
    latest = service.latest_decisions(db, "table_rows", [row_id]).get(row_id)
    out = _row_dict(r, rules, latest, row_id in set(check["sample_row_ids"]))
    chunk = db.get(m.EvidenceChunk, f"chunk:{row_id}")
    out["evidence"] = (
        {
            "chunk_id": chunk.chunk_id,
            "citation_label": chunk.citation_label,
            "text": chunk.text,
            "context_text": chunk.context_text,
            "text_status": chunk.text_status,
        }
        if chunk
        else None
    )
    footnotes = {}
    if r.footnote_refs:
        for c in db.execute(
            select(m.EvidenceChunk).where(
                m.EvidenceChunk.chunk_id.in_([f"chunk:{ref}" for ref in r.footnote_refs])
            )
        ).scalars():
            footnotes[c.chunk_id.removeprefix("chunk:")] = c.text
    out["footnotes"] = footnotes
    out["decisions"] = [_decision_dict(d) for d in service.decision_history(db, row_id)]
    out["table_code"] = r.table_code
    return out


class DecisionIn(BaseModel):
    target_type: str
    target_id: str = Field(min_length=1, max_length=288)
    decision: str
    reviewer: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4000)
    correction: str | None = Field(default=None, max_length=4000)


@router.post("/decisions")
def post_decision(body: DecisionIn, db: DB) -> dict[str, Any]:
    if body.decision not in service.DECISIONS:
        raise HTTPException(422, f"decision phải là một trong {service.DECISIONS}")
    if body.target_type not in service.TARGET_TYPES:
        raise HTTPException(422, f"target_type phải là một trong {service.TARGET_TYPES}")
    if body.decision in ("rejected", "needs_recheck") and not (body.note or "").strip():
        raise HTTPException(422, "Cần ghi chú lý do khi từ chối hoặc yêu cầu làm rõ")
    try:
        dec, updated = service.apply_decision(
            db,
            target_type=body.target_type,
            target_id=body.target_id,
            decision=body.decision,
            reviewer=body.reviewer,
            note=body.note,
            correction=body.correction,
            source="admin-ui",
        )
    except service.TargetNotFound as e:
        raise HTTPException(404, f"không tìm thấy {e}") from e
    audit(
        db,
        body.reviewer,
        "review.decision",
        f"{body.target_type}:{body.target_id}",
        {"decision": body.decision, "note": body.note, "correction": body.correction},
    )
    db.commit()
    return {
        "decision_id": dec.id,
        "target_type": body.target_type,
        "target_id": body.target_id,
        "review_status": body.decision,
        "rules_updated": updated,
    }


class BulkIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=4000)


@router.post("/tables/{table_code}/bulk-approve")
def bulk_approve(table_code: str, body: BulkIn, db: DB) -> dict[str, Any]:
    if db.get(m.TableDef, table_code) is None:
        raise HTTPException(404, f"table {table_code} not found")
    try:
        result = service.bulk_approve(db, table_code, body.reviewer, body.note, source="admin-ui")
    except service.BulkApproveBlocked as e:
        raise HTTPException(409, {"code": "sample_incomplete", **e.check}) from e
    audit(db, body.reviewer, "review.bulk_approve", f"limit_rules:table:{table_code}", result)
    db.commit()
    return result


@router.get("/items")
def review_items(
    db: DB,
    type: str = Query(
        ..., pattern="^(transition_rules|document_relations|monitoring_requirements)$"
    ),
    status: str | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if type == "transition_rules":
        rows = db.execute(select(m.TransitionRule).order_by(m.TransitionRule.rule_id)).scalars()
        for t in rows:
            items.append(
                {
                    "target_type": type,
                    "target_id": t.rule_id,
                    "document_id": t.document_id,
                    "kind": t.kind,
                    "text": t.text,
                    "citation_label": t.citation_label,
                    "evidence_chunk_id": f"chunk:{t.source_node_id}",
                    "review_status": t.review_status,
                    "meta": {"dates": t.dates, "relative_dates": t.relative_dates},
                }
            )
    elif type == "document_relations":
        rows = db.execute(
            select(m.DocumentRelation)
            .where(m.DocumentRelation.type.in_(service.HIGH_RISK_RELATIONS))
            .order_by(m.DocumentRelation.id)
        ).scalars()
        for rel in rows:
            items.append(
                {
                    "target_type": type,
                    "target_id": f"rel:{rel.id}",
                    "document_id": rel.source_document_id,
                    "kind": rel.type,
                    "text": f"{rel.type} → {rel.target_document_id or rel.target_code}"
                    + (f" ({rel.target_part})" if rel.target_part else "")
                    + (f" — {rel.condition}" if rel.condition else ""),
                    "citation_label": rel.citation_label or rel.evidence,
                    "evidence_chunk_id": f"chunk:{rel.source_node_id}"
                    if rel.source_node_id
                    else None,
                    "review_status": rel.review_status,
                    "meta": {"scope": rel.scope, "origin": rel.origin},
                }
            )
    else:
        rows = db.execute(
            select(m.MonitoringRequirement).order_by(m.MonitoringRequirement.requirement_id)
        ).scalars()
        for q in rows:
            items.append(
                {
                    "target_type": type,
                    "target_id": q.requirement_id,
                    "document_id": q.document_id,
                    "kind": q.requirement_type,
                    "text": q.text,
                    "summary": q.summary,
                    "citation_label": q.citation_label,
                    "evidence_chunk_id": q.evidence_chunk_id,
                    "review_status": q.review_status,
                    "meta": {"domain": q.domain, "parameters": q.parameters},
                }
            )
    latest = service.latest_decisions(db, type, [i["target_id"] for i in items])
    for i in items:
        i["last_decision"] = _decision_dict(latest.get(i["target_id"]))
    if status:
        items = [i for i in items if i["review_status"] == status]
    return items


@router.get("/decisions")
def decisions(db: DB, target_id: str | None = None, limit: int = Query(50, ge=1, le=500)) -> list:
    if target_id:
        rows = service.decision_history(db, target_id, limit)
    else:
        rows = list(
            db.execute(
                select(m.ReviewDecision)
                .order_by(m.ReviewDecision.reviewed_at.desc(), m.ReviewDecision.id.desc())
                .limit(limit)
            ).scalars()
        )
    return [_decision_dict(d) for d in rows]
