"""Admin audit trail (PLAN.md §11): who did what to which object — index activation, review
decisions, bulk approvals, alert resolution."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import models as m


def audit(
    session: Session, actor: str, action: str, target: str, payload: dict[str, Any] | None = None
) -> m.AuditEvent:
    ev = m.AuditEvent(
        actor=actor[:200], action=action[:48], target=target[:288], payload=payload or {}
    )
    session.add(ev)
    return ev
