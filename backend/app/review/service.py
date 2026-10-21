"""Review workflow (PLAN.md §11) shared by the Excel import script and the admin API.

A decision is recorded in `review_decisions` and, for objects that carry a `review_status`
column, mirrored onto that column (all LimitRules of a table row, one transition rule, one
document relation, one monitoring requirement). Bulk approval of a threshold table is allowed
only when >= 10 % of its rows have an `approved` latest decision and no row is `rejected` /
`needs_recheck`; it approves the remaining `pending` rules and records one `limit_rules:table`
decision with the sample size as evidence.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import models as m
from app.ontology.corpus import Corpus
from app.review.sampling import required_sample, sample_rows

DECISIONS = ("approved", "rejected", "needs_recheck")
STATUS_TARGETS = ("table_rows", "transition_rules", "document_relations", "monitoring_requirements")
TARGET_TYPES = STATUS_TARGETS + ("registry_facts", "dictionary_mapping")
HIGH_RISK_RELATIONS = ("REPLACES", "REPEALS")


class TargetNotFound(LookupError):
    pass


class BulkApproveBlocked(Exception):
    def __init__(self, check: dict[str, Any]) -> None:
        super().__init__("sample_incomplete")
        self.check = check


def _relation_id(target_id: str) -> int | None:
    raw = target_id[4:] if target_id.startswith("rel:") else target_id
    return int(raw) if raw.isdigit() else None


def _status_update(session: Session, target_type: str, target_id: str, decision: str) -> int:
    if target_type == "table_rows":
        stmt = update(m.LimitRule).where(m.LimitRule.row_id == target_id)
    elif target_type == "transition_rules":
        stmt = update(m.TransitionRule).where(m.TransitionRule.rule_id == target_id)
    elif target_type == "document_relations":
        rid = _relation_id(target_id)
        if rid is None:
            return 0
        stmt = update(m.DocumentRelation).where(m.DocumentRelation.id == rid)
    elif target_type == "monitoring_requirements":
        stmt = update(m.MonitoringRequirement).where(
            m.MonitoringRequirement.requirement_id == target_id
        )
    else:
        return 0
    return int(session.execute(stmt.values(review_status=decision)).rowcount or 0)


def target_exists(session: Session, target_type: str, target_id: str) -> bool:
    if target_type == "table_rows":
        q = select(func.count()).where(m.LimitRule.row_id == target_id)
    elif target_type == "transition_rules":
        q = select(func.count()).where(m.TransitionRule.rule_id == target_id)
    elif target_type == "document_relations":
        rid = _relation_id(target_id)
        if rid is None:
            return False
        q = select(func.count()).where(m.DocumentRelation.id == rid)
    elif target_type == "monitoring_requirements":
        q = select(func.count()).where(m.MonitoringRequirement.requirement_id == target_id)
    else:
        return True
    return int(session.execute(q).scalar_one()) > 0


def apply_decision(
    session: Session,
    *,
    target_type: str,
    target_id: str,
    decision: str,
    reviewer: str,
    note: str | None = None,
    correction: str | None = None,
    source: str | None = None,
    check_exists: bool = True,
) -> tuple[m.ReviewDecision, int]:
    """Record one decision and mirror it on the object's `review_status`; returns (decision, rows)."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if target_type not in TARGET_TYPES:
        raise ValueError(f"target_type must be one of {TARGET_TYPES}")
    if check_exists and not target_exists(session, target_type, target_id):
        raise TargetNotFound(f"{target_type}:{target_id}")
    dec = m.ReviewDecision(
        target_type=target_type,
        target_id=target_id,
        decision=decision,
        correction=correction or None,
        note=note or None,
        reviewer=reviewer,
        source=source,
    )
    session.add(dec)
    updated = _status_update(session, target_type, target_id, decision)
    return dec, updated


def latest_decisions(
    session: Session, target_type: str, target_ids: list[str] | None = None
) -> dict[str, m.ReviewDecision]:
    """Most recent decision per target (PostgreSQL DISTINCT ON)."""
    q = (
        select(m.ReviewDecision)
        .where(m.ReviewDecision.target_type == target_type)
        .distinct(m.ReviewDecision.target_id)
        .order_by(
            m.ReviewDecision.target_id,
            m.ReviewDecision.reviewed_at.desc(),
            m.ReviewDecision.id.desc(),
        )
    )
    if target_ids is not None:
        if not target_ids:
            return {}
        q = q.where(m.ReviewDecision.target_id.in_(target_ids))
    return {d.target_id: d for d in session.execute(q).scalars()}


def decision_history(session: Session, target_id: str, limit: int = 50) -> list[m.ReviewDecision]:
    return list(
        session.execute(
            select(m.ReviewDecision)
            .where(m.ReviewDecision.target_id == target_id)
            .order_by(m.ReviewDecision.reviewed_at.desc(), m.ReviewDecision.id.desc())
            .limit(limit)
        ).scalars()
    )


def table_rows(session: Session, table_code: str) -> list[m.TableRow]:
    return list(
        session.execute(
            select(m.TableRow)
            .where(m.TableRow.table_code == table_code)
            .order_by(m.TableRow.line_start, m.TableRow.row_id)
        ).scalars()
    )


def sample_check(session: Session, table_code: str) -> dict[str, Any]:
    """Is the >= 10 % sample of this table checked and clean?"""
    rows = [r for r in table_rows(session, table_code) if not r.is_group_header]
    required = required_sample(len(rows))
    sample_ids = [r.row_id for r in sample_rows(rows, required)]
    latest = latest_decisions(session, "table_rows", [r.row_id for r in rows])
    approved = [rid for rid, d in latest.items() if d.decision == "approved"]
    blocking = [
        {"row_id": rid, "status": d.decision, "reviewer": d.reviewer, "note": d.note}
        for rid, d in latest.items()
        if d.decision != "approved"
    ]
    ok = len(rows) > 0 and len(approved) >= required and not blocking
    return {
        "table_code": table_code,
        "rows_total": len(rows),
        "required": required,
        "reviewed_rows": len(approved),
        "sample_row_ids": sample_ids,
        "blocking_rows": blocking,
        "ok": ok,
    }


def bulk_approve(
    session: Session,
    table_code: str,
    reviewer: str,
    note: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    check = sample_check(session, table_code)
    if not check["ok"]:
        raise BulkApproveBlocked(check)
    total = int(
        session.execute(
            select(func.count())
            .select_from(m.LimitRule)
            .where(m.LimitRule.table_code == table_code)
        ).scalar_one()
    )
    approved_now = int(
        session.execute(
            update(m.LimitRule)
            .where(m.LimitRule.table_code == table_code, m.LimitRule.review_status == "pending")
            .values(review_status="approved")
        ).rowcount
        or 0
    )
    evidence = (
        f"bulk approve: {check['reviewed_rows']} sampled rows all 'Đúng' "
        f"(>= {check['required']} = 10 % of {check['rows_total']}, PLAN.md §11), {total} rules"
    )
    session.add(
        m.ReviewDecision(
            target_type="limit_rules:table",
            target_id=table_code,
            decision="approved",
            note=f"{evidence}; {note}" if note else evidence,
            reviewer=reviewer,
            source=source,
        )
    )
    return {
        "table": table_code,
        "sample": check["reviewed_rows"],
        "required": check["required"],
        "rules": total,
        "approved_now": approved_now,
    }


def rules_by_status(session: Session, table_code: str | None = None) -> dict[str, int]:
    q = select(m.LimitRule.review_status, func.count()).group_by(m.LimitRule.review_status)
    if table_code:
        q = q.where(m.LimitRule.table_code == table_code)
    return {k: int(v) for k, v in session.execute(q).all()}


def _by_status(session: Session, col: Any, *filters: Any) -> dict[str, int]:
    q = select(col, func.count()).group_by(col)
    for f in filters:
        q = q.where(f)
    return {k: int(v) for k, v in session.execute(q).all()}


def summary(session: Session) -> dict[str, Any]:
    tables = []
    for code in Corpus.load(session).threshold_tables():  # tables that carry limit rules
        t = session.get(m.TableDef, code)
        if t is None:
            continue
        check = sample_check(session, code)
        last = session.execute(
            select(func.max(m.ReviewDecision.reviewed_at)).where(
                m.ReviewDecision.target_type.in_(["table_rows", "limit_rules:table"]),
                (m.ReviewDecision.target_id == code)
                | m.ReviewDecision.target_id.in_(
                    select(m.TableRow.row_id).where(m.TableRow.table_code == code)
                ),
            )
        ).scalar_one()
        tables.append(
            {
                "table_code": code,
                "document_id": t.document_id,
                "title": t.title,
                "rules_by_status": rules_by_status(session, code),
                "rules_total": sum(rules_by_status(session, code).values()),
                "rows_total": check["rows_total"],
                "sample_required": check["required"],
                "rows_reviewed": check["reviewed_rows"],
                "all_sampled_approved": check["ok"],
                "blocking_rows": check["blocking_rows"],
                "last_decision_at": last,
            }
        )
    return {
        "tables": tables,
        "transition_rules": {"by_status": _by_status(session, m.TransitionRule.review_status)},
        "document_relations": {
            "by_status": _by_status(
                session,
                m.DocumentRelation.review_status,
                m.DocumentRelation.type.in_(HIGH_RISK_RELATIONS),
            ),
            "high_risk_types": list(HIGH_RISK_RELATIONS),
        },
        "monitoring_requirements": {
            "by_status": _by_status(session, m.MonitoringRequirement.review_status)
        },
        "generated_at": dt.datetime.now(dt.UTC),
    }
