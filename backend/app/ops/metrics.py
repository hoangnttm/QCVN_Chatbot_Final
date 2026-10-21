"""Operational metrics over `query_logs` / `feedback` / `usage_daily` (M15). One aggregate query
per block; no in-memory scan of rows."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as m
from app.ops import usage
from app.ops.state import STATE


def _since(window_min: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(minutes=max(1, window_min))


def compute_metrics(session: Session, settings: Settings, window_min: int = 60) -> dict[str, Any]:
    since = _since(window_min)
    q = m.QueryLog

    rows = session.execute(
        select(q.status, q.path, func.count())
        .where(q.created_at >= since)
        .group_by(q.status, q.path)
    ).all()
    by_status: dict[str, int] = {}
    by_path: dict[str, int] = {}
    total = 0
    for status, path, n in rows:
        by_status[status] = by_status.get(status, 0) + n
        by_path[path] = by_path.get(path, 0) + n
        total += n

    llm_total = session.execute(
        select(func.count()).where(q.created_at >= since, q.path.in_(["llm", "fallback"]))
    ).scalar_one()
    rejected = session.execute(
        select(func.count()).where(
            q.created_at >= since,
            q.path.in_(["llm", "fallback"]),
            or_(q.attempts >= 2, q.path == "fallback"),
        )
    ).scalar_one()

    lat = cast(q.latency_ms["total"].astext, Integer)
    p50, p95, mx = session.execute(
        select(
            func.percentile_cont(0.5).within_group(lat),
            func.percentile_cont(0.95).within_group(lat),
            func.max(lat),
        ).where(q.created_at >= since, q.latency_ms.has_key("total"))
    ).one()
    cost_window = session.execute(
        select(func.coalesce(func.sum(q.cost_usd), 0.0)).where(q.created_at >= since)
    ).scalar_one()

    fb_neg, fb_pos = session.execute(
        select(
            func.count().filter(m.Feedback.rating < 0),
            func.count().filter(m.Feedback.rating > 0),
        ).where(m.Feedback.created_at >= since)
    ).one()

    today = usage.today()
    month_start = today.replace(day=1)
    spent_today = usage.spent(session, today)
    spent_month = usage.spent_between(session, month_start, today)
    budget = float(settings.quota_global_usd_per_day)
    errors = by_status.get("error", 0)
    abstained = by_status.get("abstained", 0)

    def rate(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    return {
        "window_min": window_min,
        "since": since,
        "questions": total,
        "by_status": by_status,
        "by_path": by_path,
        "error_rate": rate(errors, total),
        "abstain_rate": rate(abstained, total),
        "validator_reject_rate": rate(int(rejected), int(llm_total)),
        "llm_questions": int(llm_total),
        "latency_ms": {
            "p50": int(p50) if p50 is not None else None,
            "p95": int(p95) if p95 is not None else None,
            "max": int(mx) if mx is not None else None,
        },
        "llm": STATE.snapshot(),
        "cost_usd": {
            "window": round(float(cost_window or 0), 6),
            "today": round(spent_today, 6),
            "month": round(spent_month, 6),
        },
        "budget": {
            "limit_usd": budget,
            "spent_usd": round(spent_today, 6),
            "ratio": round(spent_today / budget, 4) if budget > 0 else 0.0,
        },
        "feedback": {"negative": int(fb_neg), "positive": int(fb_pos)},
        "quota": {
            "per_client_limit": settings.quota_questions_per_client_day,
            "clients_over_quota": usage.clients_over_quota(
                session, today, settings.quota_questions_per_client_day
            ),
        },
        "top_clients_today": usage.top_clients(session, today),
    }
