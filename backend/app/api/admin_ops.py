"""Admin: health, metrics, usage, alerts, audit trail (M7 + M15). All routes need X-Admin-Token."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.search import get_service
from app.core.config import get_settings
from app.core.security import require_admin
from app.db import models as m
from app.db.loader import db_stats
from app.ops import alerts, usage
from app.ops.audit import audit
from app.ops.health import health_details
from app.ops.metrics import compute_metrics
from app.retrieval.retriever import RetrievalService

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])
DB = Annotated[Session, Depends(get_db)]
SVC = Annotated[RetrievalService, Depends(get_service)]


@router.get("/ping")
def ping() -> dict[str, Any]:
    return {"ok": True, "version": get_settings().app_version}


@router.get("/health")
def admin_health(db: DB, svc: SVC, probe: bool = False) -> dict[str, Any]:
    return health_details(db, svc, get_settings(), probe=probe)


@router.get("/metrics")
def metrics(db: DB, window: int = Query(60, ge=1, le=10080)) -> dict[str, Any]:
    return compute_metrics(db, get_settings(), window)


@router.get("/usage")
def usage_report(
    db: DB,
    from_: Annotated[dt.date | None, Query(alias="from")] = None,
    to: dt.date | None = None,
    group: Annotated[str, Query(pattern="^(day|client)$")] = "day",
) -> dict[str, Any]:
    end = to or usage.today()
    start = from_ or (end - dt.timedelta(days=30))
    return {
        "from": start,
        "to": end,
        "group": group,
        "rows": usage.usage_rows(db, start, end, group),
    }


def _alert_dict(ev: m.AlertEvent) -> dict[str, Any]:
    return {
        "id": ev.id,
        "rule": ev.rule,
        "severity": ev.severity,
        "dedup_key": ev.dedup_key,
        "message": ev.message,
        "payload": ev.payload,
        "fired_at": ev.fired_at,
        "resolved_at": ev.resolved_at,
        "resolved_by": ev.resolved_by,
        "notified": ev.notified,
        "notify_error": ev.notify_error,
    }


@router.get("/alerts")
def list_alerts(db: DB, active: bool = True, limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
    q = select(m.AlertEvent).order_by(m.AlertEvent.fired_at.desc()).limit(limit)
    if active:
        q = q.where(m.AlertEvent.resolved_at.is_(None))
    return [_alert_dict(ev) for ev in db.execute(q).scalars()]


class ResolveIn(BaseModel):
    actor: str = "admin"
    note: str | None = None


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: ResolveIn, db: DB) -> dict[str, Any]:
    ev = db.get(m.AlertEvent, alert_id)
    if ev is None:
        raise HTTPException(404, "alert not found")
    if ev.resolved_at is None:
        ev.resolved_at = dt.datetime.now(dt.UTC)
        ev.resolved_by = body.actor
        audit(db, body.actor, "alert.resolve", f"alert:{alert_id}", {"note": body.note})
        db.commit()
    return _alert_dict(ev)


@router.post("/alerts/evaluate")
def evaluate_alerts(db: DB, svc: SVC) -> dict[str, Any]:
    fired = alerts.evaluate(db, get_settings(), svc)
    return {"fired": [_alert_dict(ev) for ev in fired]}


@router.post("/alerts/test")
def test_alert(request: Request) -> dict[str, Any]:
    settings = get_settings()
    if not settings.alert_webhook_url:
        return {"sent": False, "error": "ALERT_WEBHOOK_URL chưa cấu hình"}
    try:
        alerts.notify(
            settings.alert_webhook_url,
            f"[QCVN Chatbot][test] Cảnh báo thử từ {request.client.host if request.client else '?'}",
        )
        return {"sent": True, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"sent": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


@router.get("/stats")
def stats(db: DB) -> dict[str, int]:
    return db_stats(db)


@router.get("/audit-events")
def audit_events(db: DB, limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    rows = db.execute(
        select(m.AuditEvent).order_by(m.AuditEvent.created_at.desc()).limit(limit)
    ).scalars()
    return [
        {
            "id": r.id,
            "actor": r.actor,
            "action": r.action,
            "target": r.target,
            "payload": r.payload,
            "created_at": r.created_at,
        }
        for r in rows
    ]
