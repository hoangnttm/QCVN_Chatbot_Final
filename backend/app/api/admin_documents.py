"""Admin: registry, ingestion runs and index versions (M19 / UC-08)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.search import get_service
from app.core.security import require_admin
from app.db import models as m
from app.ops.audit import audit
from app.retrieval.retriever import RetrievalService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])
DB = Annotated[Session, Depends(get_db)]
SVC = Annotated[RetrievalService, Depends(get_service)]


def _counts(db: Session, model: Any) -> dict[str, int]:
    return {
        k: int(v)
        for k, v in db.execute(
            select(model.document_id, func.count()).group_by(model.document_id)
        ).all()
    }


def _index_version_dict(v: m.IndexVersion) -> dict[str, Any]:
    return {
        "id": v.id,
        "label": v.label,
        "status": v.status,
        "is_active": v.status == "active",
        "embedding_model": v.embedding_model,
        "embedding_dim": v.embedding_dim,
        "document_hashes": v.document_hashes,
        "notes": v.notes,
        "created_at": v.created_at,
        "activated_at": v.activated_at,
    }


@router.get("/documents")
def admin_documents(db: DB, include_stubs: bool = False) -> list[dict[str, Any]]:
    docs = list(db.execute(select(m.Document).order_by(m.Document.document_id)).scalars())
    versions = {
        v.document_id: v
        for v in db.execute(
            select(m.DocumentVersion).where(m.DocumentVersion.is_current.is_(True))
        ).scalars()
    }
    counts = {
        "structure_nodes": _counts(db, m.StructureNode),
        "table_rows": _counts(db, m.TableRow),
        "limit_rules": _counts(db, m.LimitRule),
        "evidence_chunks": _counts(db, m.EvidenceChunk),
        "monitoring_requirements": _counts(db, m.MonitoringRequirement),
    }
    review: dict[str, dict[str, int]] = {}
    for doc_id, status, n in db.execute(
        select(m.LimitRule.document_id, m.LimitRule.review_status, func.count()).group_by(
            m.LimitRule.document_id, m.LimitRule.review_status
        )
    ).all():
        review.setdefault(doc_id, {})[status] = int(n)
    last_runs: dict[str, m.IngestionRun] = {}
    for run in db.execute(select(m.IngestionRun).order_by(m.IngestionRun.loaded_at)).scalars():
        last_runs[run.document_id] = run  # last one wins (ordered ascending)
    active = db.execute(
        select(m.IndexVersion).where(m.IndexVersion.status == "active")
    ).scalar_one_or_none()
    active_hashes = (active.document_hashes or {}) if active else {}
    out = []
    for d in docs:
        if d.document_type == "external_stub" and not include_stubs:
            continue
        v = versions.get(d.document_id)
        run = last_runs.get(d.document_id)
        report = run.report if run else None
        in_index = bool(v and active_hashes.get(d.document_id) == v.sha256)
        out.append(
            {
                "document_id": d.document_id,
                "document_code": d.document_code,
                "document_type": d.document_type,
                "title": d.title,
                "domain": d.domain,
                "text_status": d.text_status,
                "ingest_scope": d.ingest_scope,
                "grammar": d.grammar,
                "version": {
                    "file_name": v.file_name,
                    "format": v.format,
                    "sha256": v.sha256,
                    "size_bytes": v.size_bytes,
                    "page_count": v.page_count,
                    "registered_at": v.registered_at,
                }
                if v
                else None,
                "counts": {k: c.get(d.document_id, 0) for k, c in counts.items()},
                "review_by_status": review.get(d.document_id, {}),
                "last_ingestion": {
                    "id": run.id,
                    "loaded_at": run.loaded_at,
                    "file_hash": run.file_hash,
                    "report_summary": {
                        "coverage_ratio": report.get("coverage_ratio") if report else None,
                        "rules": report.get("rules") if report else None,
                        "chunks": report.get("chunks") if report else None,
                        "warnings": len(report.get("warnings") or []) if report else None,
                        "unresolved_entities": len(report.get("unresolved_entities") or [])
                        if report
                        else None,
                    },
                }
                if run
                else None,
                "in_active_index": in_index,
                "stale": bool(v and active_hashes.get(d.document_id) not in (None, v.sha256)),
                "not_indexed": bool(v and d.document_id not in active_hashes),
            }
        )
    return out


@router.get("/documents/{document_id}/ingestion-runs")
def ingestion_runs(document_id: str, db: DB) -> list[dict[str, Any]]:
    if db.get(m.Document, document_id) is None:
        raise HTTPException(404, "document not found")
    rows = db.execute(
        select(m.IngestionRun)
        .where(m.IngestionRun.document_id == document_id)
        .order_by(m.IngestionRun.loaded_at.desc())
    ).scalars()
    return [
        {"id": r.id, "loaded_at": r.loaded_at, "file_hash": r.file_hash, "report": r.report}
        for r in rows
    ]


@router.get("/index-versions")
def admin_index_versions(db: DB) -> list[dict[str, Any]]:
    return [
        _index_version_dict(v)
        for v in db.execute(select(m.IndexVersion).order_by(m.IndexVersion.id)).scalars()
    ]


class ActivateIn(BaseModel):
    actor: str = "admin"


@router.post("/index-versions/{label}/activate")
def activate_index(label: str, db: DB, svc: SVC, body: ActivateIn | None = None) -> dict[str, Any]:
    """UC-08: exactly one active index; rebuilds the in-process BM25 from the current chunks."""
    v = db.execute(select(m.IndexVersion).where(m.IndexVersion.label == label)).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, f"index version {label} not found")
    if v.status == "building":
        raise HTTPException(409, f"index version {label} is still building")
    previous = db.execute(
        select(m.IndexVersion.label).where(m.IndexVersion.status == "active")
    ).scalar_one_or_none()
    db.execute(
        update(m.IndexVersion).where(m.IndexVersion.status == "active").values(status="inactive")
    )
    db.execute(
        update(m.IndexVersion)
        .where(m.IndexVersion.label == label)
        .values(status="active", activated_at=func.now())
    )
    audit(db, (body.actor if body else "admin"), "index.activate", label, {"previous": previous})
    db.commit()
    stats = svc.reload(db)
    return {"activated": label, "previous": previous, **stats}
