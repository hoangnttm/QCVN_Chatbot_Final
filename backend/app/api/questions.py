"""QA API (PLAN.md §9): POST /questions/ask (SSE), POST /questions/answer (JSON), feedback,
history per anonymous client, quota status. Admin log routes live in app/api/admin_logs.py."""

from __future__ import annotations

import contextvars
import json
import logging
import queue
import threading
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.common import query_detail
from app.api.deps import get_db
from app.api.search import get_service
from app.core.config import get_settings
from app.core.logging import query_id_var
from app.core.security import get_client_id, get_client_key
from app.db import models as m
from app.db.session import get_sessionmaker
from app.llm.client import LLMClient
from app.ops import usage
from app.qa.models import QARequest, QAResponse
from app.qa.service import QAService
from app.retrieval.retriever import RetrievalService

router = APIRouter(prefix="/api/v1", tags=["qa"])
DB = Annotated[Session, Depends(get_db)]
SVC = Annotated[RetrievalService, Depends(get_service)]
logger = logging.getLogger(__name__)


def get_llm_dep(request: Request) -> LLMClient | None:
    """Tests inject a FakeLLM on app.state.llm; production lazily builds the Gemini client."""
    return getattr(request.app.state, "llm", None)


LLM = Annotated[LLMClient | None, Depends(get_llm_dep)]


def _error_code(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if "GEMINI" in text or "gemini" in text.lower() or "APIError" in text:
        return "llm_error"
    return "internal_error"


@router.post("/questions/answer", response_model=QAResponse)
def answer(req: QARequest, request: Request, db: DB, svc: SVC, llm: LLM) -> QAResponse:
    query_id = str(uuid.uuid4())
    client_id, client_key = get_client_id(request), get_client_key(request)
    token = query_id_var.set(query_id)
    try:
        return QAService(db, svc, llm).answer(
            req, query_id=query_id, client_id=client_id, client_key=client_key
        )
    except Exception as exc:  # noqa: BLE001
        QAService.log_error(get_sessionmaker(), query_id, req, client_id, client_key, exc)
        raise HTTPException(
            500, {"code": _error_code(exc), "query_id": query_id, "message": str(exc)[:300]}
        ) from exc
    finally:
        query_id_var.reset(token)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/questions/ask")
def ask(req: QARequest, request: Request, svc: SVC, llm: LLM) -> StreamingResponse:
    """Server-sent events: stage → understanding → evidence → delta* → answer → done."""
    q: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
    query_id = str(uuid.uuid4())
    client_id, client_key = get_client_id(request), get_client_key(request)
    ctx = contextvars.copy_context()  # carries request_id into the worker thread

    def run() -> None:
        query_id_var.set(query_id)
        session = get_sessionmaker()()
        try:
            QAService(session, svc, llm).answer(
                req,
                on_event=lambda e, d: q.put((e, d)),
                query_id=query_id,
                client_id=client_id,
                client_key=client_key,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("qa.ask failed")
            QAService.log_error(get_sessionmaker(), query_id, req, client_id, client_key, exc)
            q.put(
                (
                    "error",
                    {
                        "code": _error_code(exc),
                        "query_id": query_id,
                        "message": f"{type(exc).__name__}: {str(exc)[:300]}",
                    },
                )
            )
        finally:
            session.close()
            q.put(None)

    threading.Thread(target=ctx.run, args=(run,), daemon=True).start()

    def gen() -> Iterator[str]:
        while True:
            item = q.get()
            if item is None:
                yield _sse("done", {"query_id": query_id})
                break
            yield _sse(*item)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Query-Id": query_id},
    )


class FeedbackIn(BaseModel):
    query_id: str
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/feedback")
def feedback(body: FeedbackIn, request: Request, db: DB) -> dict[str, Any]:
    if db.get(m.QueryLog, body.query_id) is None:
        raise HTTPException(404, "query_id not found")
    fb = m.Feedback(
        query_id=body.query_id,
        rating=body.rating,
        comment=body.comment,
        client_id=get_client_id(request),
    )
    db.add(fb)
    db.commit()
    logger.info(
        "qa.feedback",
        extra={"query_id": body.query_id, "rating": body.rating, "has_comment": bool(body.comment)},
    )
    return {"id": fb.id, "query_id": body.query_id, "rating": body.rating}


@router.get("/history")
def history(
    request: Request,
    db: DB,
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """Questions asked from this browser (`X-Client-Id`); empty without a client id."""
    cid = get_client_id(request)
    if not cid:
        return []
    limit = min(limit, get_settings().history_max)
    rows = db.execute(
        select(m.QueryLog, m.AnswerRecord.answer, m.AnswerRecord.abstained)
        .outerjoin(m.AnswerRecord, m.AnswerRecord.query_id == m.QueryLog.id)
        .where(m.QueryLog.client_id == cid)
        .order_by(m.QueryLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    out = []
    for log, ans, abstained in rows:
        conclusion = (ans or {}).get("conclusion") if isinstance(ans, dict) else None
        out.append(
            {
                "query_id": log.id,
                "question": log.question,
                "status": log.status,
                "abstained": bool(abstained) if abstained is not None else None,
                "created_at": log.created_at,
                "conclusion": (conclusion or "")[:160] or None,
            }
        )
    return out


@router.get("/me/quota")
def my_quota(request: Request, db: DB) -> dict[str, Any]:
    return usage.quota_status(db, get_client_key(request), get_settings())


@router.get("/questions/{query_id}")
def get_query(query_id: str, request: Request, db: DB) -> dict[str, Any]:
    """Rebuild a stored answer (own questions only when the log carries a client id)."""
    r = db.get(m.QueryLog, query_id)
    if r is None:
        raise HTTPException(404, "query_id not found")
    cid = get_client_id(request)
    if r.client_id and r.client_id != cid:
        raise HTTPException(404, "query_id not found")
    out = query_detail(db, r)
    out["mine"] = bool(cid and r.client_id == cid)
    return out
