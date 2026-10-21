"""Admin query logs (M18), per-client history (M20) and error auditing of failed requests."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.db import models as m
from app.db.session import get_sessionmaker
from app.ops.usage import today as usage_today
from tests.helpers import (
    ADMIN,
    CLIENT_A,
    CLIENT_B,
    Q_BOD,
    Q_FREQ,
    ExplodingLLM,
    build_client,
    good_answer,
    load_service,
    parse_sse,
    scripted,
    teardown_client,
)


@pytest.fixture(scope="module")
def service(db_settings, tmp_path_factory):
    return load_service(db_settings, tmp_path_factory.mktemp("bm25"))


def test_logs_history_and_errors(db_settings, service, monkeypatch):
    from app.main import app

    maker = get_sessionmaker(db_settings)
    try:
        client = build_client(db_settings, service, scripted(good_answer), monkeypatch)
        r1 = client.post("/api/v1/questions/answer", json={"question": Q_BOD}, headers=CLIENT_A)
        assert r1.status_code == 200 and r1.json()["status"] == "answered"
        q1 = r1.json()["query_id"]
        assert r1.json()["cost_usd"] == 0.0  # FakeLLM reports zero tokens
        r2 = client.post("/api/v1/questions/answer", json={"question": Q_FREQ}, headers=CLIENT_A)
        assert r2.json()["status"] == "abstained"

        # a Gemini outage during SSE → error event + audited QueryLog(status=error)
        app.state.llm = ExplodingLLM()
        r3 = client.post("/api/v1/questions/ask", json={"question": Q_BOD}, headers=CLIENT_A)
        events = dict(parse_sse(r3.text))
        assert "error" in events and "done" in events
        err = json.loads(events["error"])
        assert err["code"] == "llm_error" and err["query_id"]
        with maker() as s:
            log = s.get(m.QueryLog, err["query_id"])
            assert log is not None and log.status == "error" and "GEMINI" in (log.error or "")
            assert log.client_id == CLIENT_A["X-Client-Id"]
            key = f"cid:{CLIENT_A['X-Client-Id']}"
            u = s.get(m.UsageDaily, (usage_today(), key))
            assert u is not None and u.questions == 3 and u.errors == 1 and u.abstained == 1

        # feedback with a comment from client A
        fb = client.post(
            "/api/v1/feedback",
            json={"query_id": q1, "rating": -1, "comment": "thiếu điều kiện"},
            headers=CLIENT_A,
        )
        assert fb.status_code == 200

        # admin list with filters
        lst = client.get("/api/v1/admin/query-logs?status=error", headers=ADMIN).json()
        assert lst["total"] == 1 and lst["items"][0]["error"].startswith("RuntimeError")
        neg = client.get("/api/v1/admin/query-logs?feedback=negative", headers=ADMIN).json()
        assert [i["query_id"] for i in neg["items"]] == [q1]
        assert neg["items"][0]["feedback_min"] == -1 and neg["items"][0]["feedback_count"] == 1
        allq = client.get("/api/v1/admin/query-logs?limit=2&offset=0", headers=ADMIN).json()
        assert allq["total"] >= 3 and len(allq["items"]) == 2
        page2 = client.get("/api/v1/admin/query-logs?limit=2&offset=2", headers=ADMIN).json()
        assert page2["items"] and page2["items"][0]["query_id"] not in {
            i["query_id"] for i in allq["items"]
        }
        by_q = client.get("/api/v1/admin/query-logs?q=BOD5", headers=ADMIN).json()
        assert all("BOD5" in i["question"] for i in by_q["items"]) and by_q["total"] >= 2

        # detail: enriched evidence and feedback
        det = client.get(f"/api/v1/admin/query-logs/{q1}", headers=ADMIN).json()
        assert det["evidence"][0]["text"] and det["evidence"][0]["citation_label"]
        assert det["feedback"][0]["comment"] == "thiếu điều kiện"
        assert det["citations"][0]["chunk_id"] == "chunk:QCVN40_2025.B1.r1"
        assert det["client_id"] == CLIENT_A["X-Client-Id"]

        # CSV export: BOM + header + one line per row
        csv = client.get("/api/v1/admin/query-logs/export.csv?status=error", headers=ADMIN)
        assert csv.status_code == 200 and csv.text.startswith("﻿")
        lines = [ln for ln in csv.text.lstrip("﻿").splitlines() if ln.strip()]
        assert lines[0].startswith("query_id,created_at") and len(lines) == 2

        # history is per client id
        hist = client.get("/api/v1/history", headers=CLIENT_A).json()
        assert {h["query_id"] for h in hist} >= {q1, r2.json()["query_id"]}
        assert next(h for h in hist if h["query_id"] == q1)["conclusion"].startswith("BOD5")
        assert client.get("/api/v1/history", headers=CLIENT_B).json() == []
        assert client.get("/api/v1/history").json() == []

        # rebuild a stored answer; other clients cannot read it
        mine = client.get(f"/api/v1/questions/{q1}", headers=CLIENT_A)
        assert mine.status_code == 200 and mine.json()["mine"] is True
        assert mine.json()["evidence"][0]["text"] and mine.json()["answer"]["conclusion"]
        assert client.get(f"/api/v1/questions/{q1}", headers=CLIENT_B).status_code == 404
        assert client.get(f"/api/v1/questions/{q1}").status_code == 404

        # quota status is informational only
        quota = client.get("/api/v1/me/quota", headers=CLIENT_A).json()
        assert quota["used"] == 3 and quota["enforced"] is False
        with maker() as s:
            assert (
                s.execute(select(m.QueryLog).where(m.QueryLog.id == q1)).scalar_one().cost_usd
                == 0.0
            )
    finally:
        teardown_client()
