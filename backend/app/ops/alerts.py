"""Alert rules, deduplication and webhook notification (M15 / M7).

`evaluate()` computes the current metrics + health, turns them into alert *candidates*, fires
those without an active or cooling-down event of the same `dedup_key`, auto-resolves rate-based
events whose condition no longer holds, and posts new events to the webhook. `alert_loop()` runs
it periodically inside the FastAPI lifespan. Everything is stdlib + SQLAlchemy.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as m
from app.db.session import get_sessionmaker
from app.ops import usage
from app.ops.health import health_public
from app.ops.metrics import compute_metrics

logger = logging.getLogger(__name__)

AUTO_RESOLVE_RULES = {
    "error_rate",
    "abstain_rate",
    "validator_reject_rate",
    "p95_latency",
    "gemini_consecutive_errors",
    "health_degraded",
}


@dataclass
class Candidate:
    rule: str
    severity: str  # info | warn | crit
    dedup_key: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


def candidates(
    metrics: dict[str, Any], health: dict[str, Any], settings: Settings, day: dt.date
) -> list[Candidate]:
    out: list[Candidate] = []
    n = int(metrics["questions"])
    win = settings.alert_window_min
    if n >= settings.alert_min_samples:
        if metrics["error_rate"] >= settings.alert_error_rate:
            out.append(
                Candidate(
                    "error_rate",
                    "crit",
                    "error_rate",
                    f"Tỷ lệ lỗi {metrics['error_rate']:.0%} trên {n} câu hỏi trong {win} phút "
                    f"(ngưỡng {settings.alert_error_rate:.0%})",
                    {"error_rate": metrics["error_rate"], "questions": n},
                )
            )
        if metrics["abstain_rate"] >= settings.alert_abstain_rate:
            out.append(
                Candidate(
                    "abstain_rate",
                    "warn",
                    "abstain_rate",
                    f"Tỷ lệ abstain {metrics['abstain_rate']:.0%} trên {n} câu trong {win} phút "
                    f"(ngưỡng {settings.alert_abstain_rate:.0%})",
                    {"abstain_rate": metrics["abstain_rate"], "questions": n},
                )
            )
        p95 = metrics["latency_ms"].get("p95")
        if p95 is not None and p95 >= settings.alert_p95_latency_ms:
            out.append(
                Candidate(
                    "p95_latency",
                    "warn",
                    "p95_latency",
                    f"Độ trễ p95 {p95} ms trong {win} phút (ngưỡng {settings.alert_p95_latency_ms} ms)",
                    {"p95_ms": p95, "questions": n},
                )
            )
    if int(metrics["llm_questions"]) >= settings.alert_min_samples:
        if metrics["validator_reject_rate"] >= settings.alert_validator_reject_rate:
            out.append(
                Candidate(
                    "validator_reject_rate",
                    "warn",
                    "validator_reject_rate",
                    f"Validator từ chối {metrics['validator_reject_rate']:.0%} câu qua LLM "
                    f"trong {win} phút (ngưỡng {settings.alert_validator_reject_rate:.0%})",
                    {
                        "validator_reject_rate": metrics["validator_reject_rate"],
                        "llm_questions": metrics["llm_questions"],
                    },
                )
            )
    llm = metrics["llm"]
    if llm["llm_consecutive_errors"] >= settings.alert_gemini_consecutive_errors:
        out.append(
            Candidate(
                "gemini_consecutive_errors",
                "crit",
                "gemini_consecutive_errors",
                f"Gemini lỗi {llm['llm_consecutive_errors']} lần liên tiếp: {llm['llm_last_error']}",
                {
                    "consecutive_errors": llm["llm_consecutive_errors"],
                    "last_error": llm["llm_last_error"],
                },
            )
        )
    ratio = float(metrics["budget"]["ratio"])
    if ratio >= 1.0:
        out.append(
            Candidate(
                "budget_100",
                "crit",
                f"budget:{day.isoformat()}:100",
                f"Chi phí Gemini hôm nay {metrics['budget']['spent_usd']:.4f} USD đã vượt ngân sách "
                f"{metrics['budget']['limit_usd']:.2f} USD/ngày (chỉ cảnh báo, không chặn)",
                dict(metrics["budget"]),
            )
        )
    elif ratio >= 0.8:
        out.append(
            Candidate(
                "budget_80",
                "warn",
                f"budget:{day.isoformat()}:80",
                f"Chi phí Gemini hôm nay {metrics['budget']['spent_usd']:.4f} USD đạt "
                f"{ratio:.0%} ngân sách {metrics['budget']['limit_usd']:.2f} USD/ngày",
                dict(metrics["budget"]),
            )
        )
    for c in metrics["quota"]["clients_over_quota"]:
        out.append(
            Candidate(
                "client_quota_exceeded",
                "info",
                f"quota:{day.isoformat()}:{c['client_key']}",
                f"Client {c['client_key']} đã hỏi {c['questions']} câu hôm nay "
                f"(hạn mức {metrics['quota']['per_client_limit']}; chỉ cảnh báo)",
                c,
            )
        )
    if health["status"] != "ok":
        out.append(
            Candidate(
                "health_degraded",
                "crit",
                "health:degraded",
                "Hệ thống suy giảm: "
                + ", ".join(
                    k
                    for k, ok in (
                        ("database", health["database"]),
                        ("index_active", bool(health["index_active"])),
                        ("bm25", health["bm25_loaded"]),
                    )
                    if not ok
                ),
                {k: v for k, v in health.items() if k != "version"},
            )
        )
    return out


def notify(url: str, text: str, timeout: float = 5.0) -> None:
    """POST `{"text": ...}` — accepted by Slack, Google Chat and Teams incoming webhooks."""
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - admin-configured URL
        resp.read()


def evaluate(
    session: Session,
    settings: Settings,
    retrieval: Any = None,
    now: dt.datetime | None = None,
) -> list[m.AlertEvent]:
    now = now or dt.datetime.now(dt.UTC)
    day = usage.today()
    metrics = compute_metrics(session, settings, settings.alert_window_min)
    health = health_public(session, retrieval, settings)
    cands = candidates(metrics, health, settings, day)
    active = {
        ev.dedup_key: ev
        for ev in session.execute(
            select(m.AlertEvent).where(m.AlertEvent.resolved_at.is_(None))
        ).scalars()
    }
    cooldown_since = now - dt.timedelta(minutes=settings.alert_cooldown_min)
    recent = {
        ev.dedup_key
        for ev in session.execute(
            select(m.AlertEvent).where(
                m.AlertEvent.resolved_at.is_not(None), m.AlertEvent.fired_at >= cooldown_since
            )
        ).scalars()
    }
    fired: list[m.AlertEvent] = []
    current_keys = {c.dedup_key for c in cands}
    for c in cands:
        if c.dedup_key in active or c.dedup_key in recent:
            continue
        ev = m.AlertEvent(
            rule=c.rule,
            severity=c.severity,
            dedup_key=c.dedup_key,
            message=c.message,
            payload=c.payload,
            fired_at=now,
        )
        if settings.alert_webhook_url:
            try:
                notify(settings.alert_webhook_url, f"[QCVN Chatbot][{c.severity}] {c.message}")
                ev.notified = True
            except Exception as e:  # noqa: BLE001 - never let a webhook failure stop the loop
                ev.notify_error = f"{type(e).__name__}: {str(e)[:300]}"
        session.add(ev)
        fired.append(ev)
    for key, ev in active.items():
        if ev.rule in AUTO_RESOLVE_RULES and key not in current_keys:
            ev.resolved_at = now
            ev.resolved_by = "auto"
    session.commit()
    if fired:
        logger.warning(
            "alerts fired", extra={"rules": [e.rule for e in fired], "count": len(fired)}
        )
    return fired


def run_once(settings: Settings, retrieval: Any = None) -> int:
    with get_sessionmaker(settings)() as session:
        return len(evaluate(session, settings, retrieval))


async def alert_loop(settings: Settings, retrieval: Any = None) -> None:
    """Background task for the FastAPI lifespan; swallows every error so the loop never dies."""
    while True:
        await asyncio.sleep(max(5, settings.alert_interval_s))
        try:
            await asyncio.to_thread(run_once, settings, retrieval)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("alert loop iteration failed")
