"""Monitoring building blocks (M7 / M15): pricing, daily usage, metrics, health, alerts,
JSON logging."""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import uuid

import pytest
from sqlalchemy import delete, select

from app.core.config import Settings
from app.core.logging import JsonFormatter, query_id_var
from app.core.pricing import cost_of_usage, est_cost
from app.db import models as m
from app.db.session import get_sessionmaker
from app.ops import alerts, usage
from app.ops.health import health_details, health_public
from app.ops.metrics import compute_metrics
from app.ops.state import STATE


@pytest.fixture()
def session(db_settings):
    s = get_sessionmaker(db_settings)()
    yield s
    s.close()


def _settings(db_settings, **over) -> Settings:
    base = {
        "database_url": db_settings.database_url,
        "alert_min_samples": 3,
        "alert_error_rate": 0.3,
        "alert_cooldown_min": 60,
        "alerts_enabled": True,
        "alert_webhook_url": None,
        "quota_global_usd_per_day": 5.0,
        "quota_questions_per_client_day": 2,
    }
    base.update(over)
    return Settings(_env_file=None, **base)


def _log(session, status="answered", path="llm", attempts=1, total_ms=500, cost=0.001, cid=None):
    session.add(
        m.QueryLog(
            id=str(uuid.uuid4()),
            question="q",
            domain="wastewater",
            intent="threshold",
            understanding={},
            retrieved=[],
            structured_hits=[],
            status=status,
            path=path,
            prompt_version="qa-v1.0",
            usage={},
            latency_ms={"total": total_ms},
            validation={},
            attempts=attempts,
            client_id=cid,
            cost_usd=cost,
        )
    )


def _clear(session):
    session.execute(delete(m.QueryLog))
    session.execute(delete(m.AlertEvent))
    session.execute(delete(m.UsageDaily))
    session.commit()
    STATE.reset()


def test_pricing():
    assert est_cost("gemini-3.8-flash", 1_000_000, 0, 0) == 0.75
    assert est_cost("gemini-3.8-flash", 0, 1_000_000, 1_000_000) == 7.5
    assert est_cost("unknown-model", 10, 10, 0) is None
    assert cost_of_usage("gemini-3.5-flash-lite", {"prompt_tokens": 2_000_000}) == 0.6
    assert cost_of_usage(None, {"prompt_tokens": 5}) == 0.0


def test_usage_upsert_and_quota(session, db_settings):
    _clear(session)
    usage.add_usage(session, "cid:a", questions=1, llm_calls=2, prompt_tokens=100, cost_usd=0.5)
    usage.add_usage(session, "cid:a", questions=1, prompt_tokens=50, cost_usd=0.25, abstained=1)
    usage.add_usage(session, "cid:b", questions=1, errors=1)
    session.commit()
    row = usage.client_usage(session, "cid:a", usage.today())
    assert row["questions"] == 2 and row["llm_calls"] == 2 and row["prompt_tokens"] == 150
    assert row["cost_usd"] == pytest.approx(0.75) and row["abstained"] == 1
    st = usage.quota_status(session, "cid:a", _settings(db_settings))
    assert st["used"] == 2 and st["limit"] == 2 and st["over_quota"] is True
    assert st["enforced"] is False and st["budget"]["spent_usd"] == pytest.approx(0.75)
    assert usage.clients_over_quota(session, usage.today(), 2) == [
        {"client_key": "cid:a", "questions": 2, "cost_usd": pytest.approx(0.75)}
    ]
    rows = usage.usage_rows(session, usage.today(), usage.today(), "client")
    assert {r["key"] for r in rows} == {"cid:a", "cid:b"}


def test_metrics_rates_and_latency(session, db_settings):
    _clear(session)
    _log(session, "answered", "llm", 1, 400)
    _log(session, "answered", "llm", 2, 800)  # regenerated once → validator reject
    _log(session, "failed", "fallback", 2, 1200)
    _log(session, "abstained", "policy", 0, 100, cost=0.0)
    _log(session, "error", "error", 0, 50, cost=0.0)
    session.commit()
    mtr = compute_metrics(session, _settings(db_settings), window_min=60)
    assert mtr["questions"] == 5
    assert mtr["by_status"] == {"answered": 2, "failed": 1, "abstained": 1, "error": 1}
    assert mtr["error_rate"] == pytest.approx(0.2) and mtr["abstain_rate"] == pytest.approx(0.2)
    assert mtr["llm_questions"] == 3 and mtr["validator_reject_rate"] == pytest.approx(
        2 / 3, abs=1e-3
    )
    assert isinstance(mtr["latency_ms"]["p95"], int) and mtr["latency_ms"]["max"] == 1200
    assert mtr["cost_usd"]["window"] == pytest.approx(0.003)


def test_health_and_state(session, db_settings):
    STATE.reset()
    settings = _settings(db_settings)
    pub = health_public(session, None, settings)
    assert pub["database"] is True and pub["status"] == "degraded" and pub["index_active"] is None
    STATE.record_llm("429: quota")
    STATE.record_llm("503: down")
    det = health_details(session, None, settings)
    g = det["checks"]["gemini"]
    assert g["consecutive_errors"] == 2 and g["last_error"].startswith("503")
    STATE.record_llm(None)
    assert health_details(session, None, settings)["checks"]["gemini"]["consecutive_errors"] == 0
    assert det["checks"]["neo4j"]["skipped"] is True


def test_alerts_fire_dedup_resolve_and_notify(session, db_settings, monkeypatch):
    _clear(session)
    settings = _settings(db_settings, alert_webhook_url="https://hooks.example/test")
    sent: list[dict] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def fake_urlopen(req, timeout=5.0):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    for _ in range(4):
        _log(session, "error", "error", 0, 50, cost=0.0)
    session.commit()
    fired = alerts.evaluate(session, settings)
    rules = {e.rule for e in fired}
    assert "error_rate" in rules and "health_degraded" in rules  # no index in this test app
    err = next(e for e in fired if e.rule == "error_rate")
    assert err.notified is True and any("Tỷ lệ lỗi" in s["text"] for s in sent)
    # second evaluation: still active → nothing new
    assert alerts.evaluate(session, settings) == []
    # condition gone → auto-resolved
    session.execute(delete(m.QueryLog))
    session.commit()
    alerts.evaluate(session, settings)
    session.refresh(err)
    assert err.resolved_at is not None and err.resolved_by == "auto"
    # cooldown: same key does not re-fire within 60 min
    for _ in range(4):
        _log(session, "error", "error", 0, 50, cost=0.0)
    session.commit()
    assert "error_rate" not in {e.rule for e in alerts.evaluate(session, settings)}
    # budget 80 % fires once per day; webhook failure is recorded, not raised
    _clear(session)
    usage.add_usage(session, "cid:x", questions=1, cost_usd=4.5)
    session.commit()

    def broken_urlopen(req, timeout=5.0):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", broken_urlopen)
    fired = alerts.evaluate(session, settings)
    b = next(e for e in fired if e.rule == "budget_80")
    assert b.notified is False and "connection refused" in (b.notify_error or "")
    assert "budget_80" not in {e.rule for e in alerts.evaluate(session, settings)}
    # per-client quota exceeded → info alert, deduplicated per client and day
    usage.add_usage(session, "cid:x", questions=2)
    session.commit()
    fired = alerts.evaluate(session, settings)
    assert any(e.rule == "client_quota_exceeded" and e.severity == "info" for e in fired)


def test_json_formatter_carries_query_id():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("test.json")
    log.propagate = False
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    token = query_id_var.set("q-123")
    try:
        log.info("hello", extra={"latency_ms": 12})
    finally:
        query_id_var.reset(token)
        log.removeHandler(handler)
    rec = json.loads(stream.getvalue().strip())
    assert rec["msg"] == "hello" and rec["query_id"] == "q-123" and rec["latency_ms"] == 12
    assert rec["level"] == "INFO" and rec["ts"].endswith("+00:00")


def test_purge_cutoff(session):
    _clear(session)
    old = m.QueryLog(
        id=str(uuid.uuid4()),
        question="old",
        domain="wastewater",
        intent="threshold",
        understanding={},
        retrieved=[],
        structured_hits=[],
        status="answered",
        path="llm",
        prompt_version="qa-v1.0",
        usage={},
        latency_ms={},
        validation={},
        attempts=1,
        created_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=400),
    )
    session.add(old)
    _log(session)
    session.commit()
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=365)
    session.execute(delete(m.QueryLog).where(m.QueryLog.created_at < cutoff))
    session.commit()
    assert session.execute(select(m.QueryLog)).scalars().one().question == "q"
