"""Admin review workflow (M17, PLAN.md §11): decisions, sample check, bulk approval, high-risk
relations and transition rules; the Excel import keeps working through the same service."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select, update

from app.db import models as m
from app.db.session import get_sessionmaker
from app.review import service as rs
from app.review.sampling import required_sample, sample_rows
from tests.helpers import ADMIN, build_client, load_service, teardown_client

B1, B2 = "QCVN40_2025.B1", "QCVN40_2025.B2"


@pytest.fixture(scope="module")
def service(db_settings, tmp_path_factory):
    return load_service(db_settings, tmp_path_factory.mktemp("bm25"))


@pytest.fixture(scope="module", autouse=True)
def _clean_review_state(db_settings):
    """The `qcvn_test` database is shared by the whole session: leave no decisions or changed
    review statuses behind for `test_review_pack.py` (Excel import expects a clean table)."""
    yield
    maker = get_sessionmaker(db_settings)
    with maker.begin() as s:
        s.execute(delete(m.ReviewDecision))
        s.execute(delete(m.AuditEvent))
        for model in (m.LimitRule, m.TransitionRule, m.DocumentRelation, m.MonitoringRequirement):
            s.execute(update(model).values(review_status="pending"))


def _reset(maker, *tables: str) -> None:
    with maker.begin() as s:
        for t in tables:
            s.execute(
                update(m.LimitRule)
                .where(m.LimitRule.table_code == t)
                .values(review_status="pending")
            )
            row_ids = select(m.TableRow.row_id).where(m.TableRow.table_code == t)
            s.execute(delete(m.ReviewDecision).where(m.ReviewDecision.target_id.in_(row_ids)))
            s.execute(delete(m.ReviewDecision).where(m.ReviewDecision.target_id == t))
        s.execute(delete(m.AuditEvent))


def test_required_sample():
    assert required_sample(0) == 0 and required_sample(4) == 1 and required_sample(63) == 7
    assert required_sample(215) == 22


def test_review_summary_and_table(db_settings, service, monkeypatch):
    maker = get_sessionmaker(db_settings)
    _reset(maker, B1, B2)
    try:
        c = build_client(db_settings, service, None, monkeypatch)
        summary = c.get("/api/v1/admin/review/summary", headers=ADMIN).json()
        b1 = next(t for t in summary["tables"] if t["table_code"] == B1)
        assert b1["rows_total"] == 4 and b1["rules_total"] == 24 and b1["sample_required"] == 1
        assert b1["rules_by_status"] == {"pending": 24} and b1["all_sampled_approved"] is False
        assert summary["document_relations"]["high_risk_types"] == ["REPLACES", "REPEALS"]
        assert "pending" in summary["transition_rules"]["by_status"]

        table = c.get(f"/api/v1/admin/review/tables/{B2}", headers=ADMIN).json()
        assert table["table"]["table_code"] == B2 and len(table["rows"]) == 63
        assert table["sample"]["required"] == 7 and len(table["sample"]["row_ids"]) == 7
        with maker() as s:
            rows = [r for r in rs.table_rows(s, B2) if not r.is_group_header]
            assert sorted(r.row_id for r in sample_rows(rows, 7)) == table["sample"]["row_ids"]
        in_sample = [r for r in table["rows"] if r["in_sample"]]
        assert len(in_sample) == 7 and all("risk_score" in r for r in table["rows"])
        only = c.get(f"/api/v1/admin/review/tables/{B2}?in_sample=true", headers=ADMIN).json()
        assert len(only["rows"]) == 7
        risky = c.get(f"/api/v1/admin/review/tables/{B2}?risk_min=3", headers=ADMIN).json()
        assert all(r["risk_score"] >= 3 for r in risky["rows"])
        row = c.get(f"/api/v1/admin/review/rows/{table['rows'][0]['row_id']}", headers=ADMIN).json()
        assert row["evidence"]["chunk_id"] == f"chunk:{table['rows'][0]['row_id']}"
        assert row["evidence"]["text"] and row["decisions"] == []
        assert c.get("/api/v1/admin/review/tables/NOPE", headers=ADMIN).status_code == 404
    finally:
        teardown_client()


def test_decisions_and_bulk_approve(db_settings, service, monkeypatch):
    maker = get_sessionmaker(db_settings)
    _reset(maker, B1, B2)
    try:
        c = build_client(db_settings, service, None, monkeypatch)
        table = c.get(f"/api/v1/admin/review/tables/{B1}", headers=ADMIN).json()
        first = table["rows"][0]["row_id"]
        # note is mandatory for rejected / needs_recheck
        r = c.post(
            "/api/v1/admin/review/decisions",
            json={
                "target_type": "table_rows",
                "target_id": first,
                "decision": "rejected",
                "reviewer": "CG",
            },
            headers=ADMIN,
        )
        assert r.status_code == 422
        r = c.post(
            "/api/v1/admin/review/decisions",
            json={
                "target_type": "table_rows",
                "target_id": "no-such-row",
                "decision": "approved",
                "reviewer": "CG",
            },
            headers=ADMIN,
        )
        assert r.status_code == 404
        r = c.post(
            "/api/v1/admin/review/decisions",
            json={
                "target_type": "table_rows",
                "target_id": first,
                "decision": "approved",
                "reviewer": "CG",
            },
            headers=ADMIN,
        )
        assert r.status_code == 200 and r.json()["rules_updated"] == 6
        with maker() as s:
            statuses = set(
                s.execute(
                    select(m.LimitRule.review_status).where(m.LimitRule.row_id == first)
                ).scalars()
            )
            assert statuses == {"approved"}
            assert (
                s.execute(select(m.AuditEvent).where(m.AuditEvent.action == "review.decision"))
                .scalars()
                .one()
            )
        # B1: 4 rows, required 1 → bulk approve allowed after one approved row
        r = c.post(
            f"/api/v1/admin/review/tables/{B1}/bulk-approve",
            json={"reviewer": "CG", "note": "đã kiểm mẫu"},
            headers=ADMIN,
        )
        assert r.status_code == 200
        assert r.json()["approved_now"] == 18 and r.json()["rules"] == 24
        with maker() as s:
            assert rs.rules_by_status(s, B1) == {"approved": 24}
            bulk = (
                s.execute(
                    select(m.ReviewDecision).where(
                        m.ReviewDecision.target_type == "limit_rules:table"
                    )
                )
                .scalars()
                .one()
            )
            assert (
                bulk.target_id == B1
                and "1 sampled rows" in bulk.note
                and "đã kiểm mẫu" in bulk.note
            )

        # B2: 63 rows, required 7; a rejected row blocks, too few approved rows block
        table = c.get(f"/api/v1/admin/review/tables/{B2}", headers=ADMIN).json()
        ids = [row["row_id"] for row in table["rows"]]
        c.post(
            "/api/v1/admin/review/decisions",
            json={
                "target_type": "table_rows",
                "target_id": ids[0],
                "decision": "rejected",
                "reviewer": "CG",
                "note": "sai đơn vị",
            },
            headers=ADMIN,
        )
        r = c.post(
            f"/api/v1/admin/review/tables/{B2}/bulk-approve",
            json={"reviewer": "CG", "note": "x"},
            headers=ADMIN,
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["code"] == "sample_incomplete" and detail["required"] == 7
        assert detail["blocking_rows"][0]["row_id"] == ids[0]
        for rid in ids[:7]:  # re-approve the first (latest decision wins) and six more
            c.post(
                "/api/v1/admin/review/decisions",
                json={
                    "target_type": "table_rows",
                    "target_id": rid,
                    "decision": "approved",
                    "reviewer": "CG",
                },
                headers=ADMIN,
            )
        r = c.post(
            f"/api/v1/admin/review/tables/{B2}/bulk-approve",
            json={"reviewer": "CG", "note": "đủ mẫu"},
            headers=ADMIN,
        )
        assert r.status_code == 200 and r.json()["sample"] == 7
        with maker() as s:
            assert rs.rules_by_status(s, B2) == {"approved": 189}
        hist = c.get(f"/api/v1/admin/review/decisions?target_id={ids[0]}", headers=ADMIN).json()
        assert [d["decision"] for d in hist] == ["approved", "rejected"]
    finally:
        teardown_client()


def test_items_transition_and_relations(db_settings, service, monkeypatch):
    maker = get_sessionmaker(db_settings)
    try:
        c = build_client(db_settings, service, None, monkeypatch)
        items = c.get("/api/v1/admin/review/items?type=transition_rules", headers=ADMIN).json()
        assert items and items[0]["review_status"] in (
            "pending",
            "approved",
            "rejected",
            "needs_recheck",
        )
        tid = items[0]["target_id"]
        r = c.post(
            "/api/v1/admin/review/decisions",
            json={
                "target_type": "transition_rules",
                "target_id": tid,
                "decision": "approved",
                "reviewer": "CG",
            },
            headers=ADMIN,
        )
        assert r.status_code == 200 and r.json()["rules_updated"] == 1
        with maker() as s:
            assert s.get(m.TransitionRule, tid).review_status == "approved"
        rels = c.get("/api/v1/admin/review/items?type=document_relations", headers=ADMIN).json()
        assert rels and all(i["kind"] in ("REPLACES", "REPEALS") for i in rels)
        rid = rels[0]["target_id"]
        r = c.post(
            "/api/v1/admin/review/decisions",
            json={
                "target_type": "document_relations",
                "target_id": rid,
                "decision": "needs_recheck",
                "reviewer": "CG",
                "note": "kiểm lại phạm vi",
            },
            headers=ADMIN,
        )
        assert r.status_code == 200
        with maker() as s:
            rel = s.get(m.DocumentRelation, int(rid.removeprefix("rel:")))
            assert rel.review_status == "needs_recheck"
        pending = c.get(
            "/api/v1/admin/review/items?type=document_relations&status=needs_recheck", headers=ADMIN
        ).json()
        assert [i["target_id"] for i in pending] == [rid]
        assert pending[0]["last_decision"]["note"] == "kiểm lại phạm vi"
    finally:
        teardown_client()
