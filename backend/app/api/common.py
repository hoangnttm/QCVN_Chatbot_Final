"""Helpers shared by the public QA endpoints and the admin log pages."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m


def document_codes(session: Session) -> dict[str, str]:
    return dict(session.execute(select(m.Document.document_id, m.Document.document_code)).all())


def enrich_retrieved(session: Session, retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`query_logs.retrieved` holds ids/ranks/scores; add label, document, type, status and text
    (same shape as `EvidenceItem`) with one `IN` query."""
    ids = [x.get("chunk_id") for x in retrieved if x.get("chunk_id")]
    if not ids:
        return []
    chunks = {
        c.chunk_id: c
        for c in session.execute(
            select(m.EvidenceChunk).where(m.EvidenceChunk.chunk_id.in_(ids))
        ).scalars()
    }
    codes = document_codes(session)
    out = []
    for x in retrieved:
        c = chunks.get(x.get("chunk_id"))
        if c is None:
            continue
        out.append(
            {
                "rank": x.get("rank"),
                "chunk_id": c.chunk_id,
                "citation_label": c.citation_label,
                "document_id": c.document_id,
                "document_code": codes.get(c.document_id, c.document_id),
                "chunk_type": c.chunk_type,
                "domain": c.domain,
                "text_status": c.text_status,
                "score": x.get("score"),
                "sources": x.get("sources") or [],
                "text": c.text,
            }
        )
    return out


def answer_record(session: Session, query_id: str) -> m.AnswerRecord | None:
    return session.execute(
        select(m.AnswerRecord).where(m.AnswerRecord.query_id == query_id)
    ).scalar_one_or_none()


def citations_of(session: Session, answer_id: str) -> list[dict[str, Any]]:
    rows = list(
        session.execute(
            select(m.AnswerCitation)
            .where(m.AnswerCitation.answer_id == answer_id)
            .order_by(m.AnswerCitation.id)
        ).scalars()
    )
    if not rows:
        return []
    chunks = {
        c.chunk_id: c
        for c in session.execute(
            select(m.EvidenceChunk).where(m.EvidenceChunk.chunk_id.in_([r.chunk_id for r in rows]))
        ).scalars()
    }
    codes = document_codes(session)
    return [
        {
            "chunk_id": r.chunk_id,
            "citation_label": r.citation_label,
            "document_code": codes.get(chunks[r.chunk_id].document_id, "")
            if r.chunk_id in chunks
            else "",
            "text_status": chunks[r.chunk_id].text_status if r.chunk_id in chunks else "unknown",
        }
        for r in rows
    ]


def query_detail(session: Session, log: m.QueryLog, *, include_text: bool = True) -> dict[str, Any]:
    """Everything the chat UI needs to rebuild a turn from the audit tables."""
    from app.qa import prompts as P

    ans = answer_record(session, log.id)
    evidence = enrich_retrieved(session, log.retrieved or [])
    if not include_text:
        for e in evidence:
            e.pop("text", None)
    return {
        "query_id": log.id,
        "question": log.question,
        "as_of_date": log.as_of_date,
        "understanding": log.understanding,
        "structured_hits": [{"rule_code": c} for c in (log.structured_hits or [])],
        "evidence": evidence,
        "answer": ans.answer if ans else None,
        "citations": citations_of(session, ans.id) if ans else [],
        "validation": log.validation,
        "status": log.status,
        "path": log.path,
        "attempts": log.attempts,
        "model": log.model_id,
        "thinking_level": log.thinking_level,
        "prompt_version": log.prompt_version,
        "usage": log.usage,
        "latency_ms": log.latency_ms,
        "cost_usd": log.cost_usd,
        "error": log.error,
        "disclaimer": P.DISCLAIMER,
        "created_at": log.created_at,
    }
