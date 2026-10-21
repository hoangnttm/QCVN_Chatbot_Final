"""Retrieval API (PLAN.md §9, Phase 2): POST /search (internal, for Recall@k), index versions.
Index activation moved to app/api/admin_documents.py (needs the admin token)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db import models as m
from app.retrieval.retriever import RetrievalService, Retriever, SearchRequest, SearchResult

router = APIRouter(prefix="/api/v1", tags=["retrieval"])
DB = Annotated[Session, Depends(get_db)]


def get_service(request: Request) -> RetrievalService:
    svc = getattr(request.app.state, "retrieval", None)
    if svc is None:
        svc = RetrievalService()
        request.app.state.retrieval = svc
    return svc


SVC = Annotated[RetrievalService, Depends(get_service)]


@router.post("/search", response_model=SearchResult)
def search(req: SearchRequest, db: DB, svc: SVC) -> SearchResult:
    return Retriever(db, svc).search(req)


@router.get("/index-versions")
def list_index_versions(db: DB) -> list[dict[str, Any]]:
    return [
        {
            "id": v.id,
            "label": v.label,
            "status": v.status,
            "embedding_model": v.embedding_model,
            "embedding_dim": v.embedding_dim,
            "document_hashes": v.document_hashes,
            "notes": v.notes,
            "created_at": v.created_at,
            "activated_at": v.activated_at,
        }
        for v in db.execute(select(m.IndexVersion).order_by(m.IndexVersion.id)).scalars()
    ]
