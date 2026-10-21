"""Admin: query logs (M18) — filtered list, detail with evidence and feedback, CSV export."""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.api.common import query_detail
from app.api.deps import get_db
from app.core.security import require_admin
from app.db import models as m

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])
DB = Annotated[Session, Depends(get_db)]
EXPORT_MAX = 10_000


def _feedback_subquery():
    return (
        select(
            m.Feedback.query_id.label("query_id"),
            func.min(m.Feedback.rating).label("min_rating"),
            func.max(m.Feedback.rating).label("max_rating"),
            func.count().label("n"),
        )
        .group_by(m.Feedback.query_id)
        .subquery()
    )


def _base(
    status: str | None,
    path: str | None,
    feedback: str | None,
    q: str | None,
    from_: dt.date | None,
    to: dt.date | None,
    client_id: str | None,
) -> tuple[Select, Any]:
    fb = _feedback_subquery()
    stmt = (
        select(m.QueryLog, fb.c.min_rating, fb.c.max_rating, fb.c.n, m.AnswerRecord.abstained)
        .outerjoin(fb, fb.c.query_id == m.QueryLog.id)
        .outerjoin(m.AnswerRecord, m.AnswerRecord.query_id == m.QueryLog.id)
    )
    if status:
        stmt = stmt.where(m.QueryLog.status == status)
    if path:
        stmt = stmt.where(m.QueryLog.path == path)
    if feedback == "negative":
        stmt = stmt.where(fb.c.min_rating < 0)
    elif feedback == "positive":
        stmt = stmt.where(fb.c.max_rating > 0)
    elif feedback == "none":
        stmt = stmt.where(fb.c.n.is_(None))
    elif feedback == "any":
        stmt = stmt.where(fb.c.n.is_not(None))
    if q:
        stmt = stmt.where(m.QueryLog.question.ilike(f"%{q}%"))
    if from_:
        stmt = stmt.where(m.QueryLog.created_at >= dt.datetime.combine(from_, dt.time.min, dt.UTC))
    if to:
        stmt = stmt.where(
            m.QueryLog.created_at
            < dt.datetime.combine(to + dt.timedelta(days=1), dt.time.min, dt.UTC)
        )
    if client_id:
        stmt = stmt.where(m.QueryLog.client_id == client_id)
    return stmt, fb


def _item(r: m.QueryLog, min_rating, max_rating, n, abstained) -> dict[str, Any]:
    return {
        "query_id": r.id,
        "question": r.question,
        "created_at": r.created_at,
        "client_id": r.client_id,
        "domain": r.domain,
        "intent": r.intent,
        "status": r.status,
        "path": r.path,
        "model_id": r.model_id,
        "attempts": r.attempts,
        "latency_total_ms": (r.latency_ms or {}).get("total"),
        "total_tokens": (r.usage or {}).get("total_tokens"),
        "cost_usd": r.cost_usd,
        "validation_ok": (r.validation or {}).get("ok"),
        "abstained": abstained,
        "feedback_min": min_rating,
        "feedback_max": max_rating,
        "feedback_count": int(n or 0),
        "error": r.error,
    }


@router.get("/query-logs")
def query_logs(
    db: DB,
    status: str | None = None,
    path: str | None = None,
    feedback: Annotated[str | None, Query(pattern="^(negative|positive|none|any)$")] = None,
    q: str | None = None,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: dt.date | None = None,
    client_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    stmt, _ = _base(status, path, feedback, q, from_, to, client_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(m.QueryLog.created_at.desc()).limit(limit).offset(offset)).all()
    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": [_item(*row) for row in rows],
    }


@router.get("/query-logs/export.csv")
def export_csv(
    db: DB,
    status: str | None = None,
    path: str | None = None,
    feedback: Annotated[str | None, Query(pattern="^(negative|positive|none|any)$")] = None,
    q: str | None = None,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: dt.date | None = None,
    client_id: str | None = None,
) -> StreamingResponse:
    stmt, _ = _base(status, path, feedback, q, from_, to, client_id)
    rows = db.execute(stmt.order_by(m.QueryLog.created_at.desc()).limit(EXPORT_MAX)).all()
    cols = [
        "query_id",
        "created_at",
        "client_id",
        "question",
        "domain",
        "intent",
        "status",
        "path",
        "model_id",
        "attempts",
        "latency_total_ms",
        "total_tokens",
        "cost_usd",
        "validation_ok",
        "abstained",
        "feedback_min",
        "feedback_count",
        "error",
    ]

    def gen() -> Iterator[str]:
        buf = io.StringIO()
        w = csv.writer(buf)
        yield "﻿"  # UTF-8 BOM so Excel renders Vietnamese
        w.writerow(cols)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()
        for row in rows:
            item = _item(*row)
            w.writerow([item.get(c, "") if item.get(c) is not None else "" for c in cols])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="query-logs-{stamp}.csv"'},
    )


@router.get("/query-logs/{query_id}")
def query_log_detail(query_id: str, db: DB) -> dict[str, Any]:
    log = db.get(m.QueryLog, query_id)
    if log is None:
        raise HTTPException(404, "query_id not found")
    out = query_detail(db, log)
    out["client_id"] = log.client_id
    out["feedback"] = [
        {"id": f.id, "rating": f.rating, "comment": f.comment, "created_at": f.created_at}
        for f in db.execute(
            select(m.Feedback).where(m.Feedback.query_id == query_id).order_by(m.Feedback.id)
        ).scalars()
    ]
    return out
