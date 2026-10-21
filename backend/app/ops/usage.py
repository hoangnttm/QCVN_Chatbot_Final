"""Daily usage per client and cost budget (M7). Counting only: per PO decision (2026-09-24) quotas
warn, they never block. `usage_daily` is upserted atomically from the QA audit step."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as m

COUNTERS = (
    "questions",
    "llm_calls",
    "prompt_tokens",
    "output_tokens",
    "thinking_tokens",
    "cost_usd",
    "errors",
    "abstained",
)


def today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def add_usage(
    session: Session, client_key: str, day: dt.date | None = None, **counters: Any
) -> None:
    """Add the given counters to the (day, client_key) row; creates it when missing."""
    values = {k: counters.get(k, 0) for k in COUNTERS}
    values["cost_usd"] = float(values["cost_usd"] or 0.0)
    stmt = pg_insert(m.UsageDaily).values(day=day or today(), client_key=client_key[:64], **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[m.UsageDaily.day, m.UsageDaily.client_key],
        set_={k: getattr(m.UsageDaily, k) + getattr(stmt.excluded, k) for k in COUNTERS}
        | {"updated_at": func.now()},
    )
    session.execute(stmt)


def spent(session: Session, day: dt.date) -> float:
    return float(
        session.execute(
            select(func.coalesce(func.sum(m.UsageDaily.cost_usd), 0.0)).where(
                m.UsageDaily.day == day
            )
        ).scalar_one()
    )


def spent_between(session: Session, start: dt.date, end: dt.date) -> float:
    return float(
        session.execute(
            select(func.coalesce(func.sum(m.UsageDaily.cost_usd), 0.0)).where(
                m.UsageDaily.day >= start, m.UsageDaily.day <= end
            )
        ).scalar_one()
    )


def client_usage(session: Session, client_key: str, day: dt.date) -> dict[str, Any]:
    row = session.get(m.UsageDaily, (day, client_key[:64]))
    if row is None:
        return {k: 0 for k in COUNTERS}
    return {k: getattr(row, k) for k in COUNTERS}


def quota_status(session: Session, client_key: str, settings: Settings) -> dict[str, Any]:
    day = today()
    used = int(client_usage(session, client_key, day)["questions"])
    limit = int(settings.quota_questions_per_client_day)
    budget = float(settings.quota_global_usd_per_day)
    spent_today = spent(session, day)
    return {
        "day": day.isoformat(),
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "over_quota": limit > 0 and used >= limit,
        "budget": {
            "limit_usd": budget,
            "spent_usd": round(spent_today, 6),
            "ratio": round(spent_today / budget, 4) if budget > 0 else 0.0,
        },
        "enforced": False,  # warn-only by decision of the PO (2026-09-24)
    }


def clients_over_quota(session: Session, day: dt.date, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    rows = session.execute(
        select(m.UsageDaily.client_key, m.UsageDaily.questions, m.UsageDaily.cost_usd)
        .where(m.UsageDaily.day == day, m.UsageDaily.questions >= limit)
        .order_by(m.UsageDaily.questions.desc())
    ).all()
    return [{"client_key": k, "questions": q, "cost_usd": float(c or 0)} for k, q, c in rows]


def top_clients(session: Session, day: dt.date, n: int = 5) -> list[dict[str, Any]]:
    rows = session.execute(
        select(m.UsageDaily.client_key, m.UsageDaily.questions, m.UsageDaily.cost_usd)
        .where(m.UsageDaily.day == day)
        .order_by(m.UsageDaily.questions.desc())
        .limit(n)
    ).all()
    return [{"client_key": k, "questions": q, "cost_usd": float(c or 0)} for k, q, c in rows]


def usage_rows(
    session: Session, start: dt.date, end: dt.date, group: str = "day"
) -> list[dict[str, Any]]:
    if group == "client":
        key = m.UsageDaily.client_key
    else:
        key = m.UsageDaily.day
    cols = [func.sum(getattr(m.UsageDaily, k)).label(k) for k in COUNTERS]
    rows = session.execute(
        select(key.label("key"), *cols)
        .where(m.UsageDaily.day >= start, m.UsageDaily.day <= end)
        .group_by(key)
        .order_by(key)
    ).all()
    out = []
    for r in rows:
        d = {"key": r.key.isoformat() if isinstance(r.key, dt.date) else r.key}
        for k in COUNTERS:
            v = getattr(r, k)
            d[k] = float(v or 0) if k == "cost_usd" else int(v or 0)
        out.append(d)
    return out
