"""Admin documents / ingestion runs / index versions (M19)."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.db import models as m
from app.db.session import get_sessionmaker
from tests.helpers import ADMIN, build_client, load_service, teardown_client


@pytest.fixture(scope="module")
def service(db_settings, tmp_path_factory):
    return load_service(db_settings, tmp_path_factory.mktemp("bm25"))


def test_documents_runs_and_activate(db_settings, service, monkeypatch):
    maker = get_sessionmaker(db_settings)
    with maker.begin() as s:
        s.execute(delete(m.IndexVersion))
        s.execute(delete(m.AuditEvent))
        sha = s.execute(
            select(m.DocumentVersion.sha256).where(
                m.DocumentVersion.document_id == "QCVN_40_2025",
                m.DocumentVersion.is_current.is_(True),
            )
        ).scalar_one()
        s.add(
            m.IndexVersion(
                label="test-index@1536",
                status="inactive",
                embedding_model="gemini-embedding-001",
                embedding_dim=1536,
                document_hashes={"QCVN_40_2025": sha, "TT_06_2025": "stale-hash"},
            )
        )
    try:
        c = build_client(db_settings, service, None, monkeypatch)
        docs = c.get("/api/v1/admin/documents", headers=ADMIN).json()
        by_id = {d["document_id"]: d for d in docs}
        assert "QCVN_40_2025" in by_id and "QCVN_08_2023" not in by_id  # stubs hidden by default
        q40 = by_id["QCVN_40_2025"]
        assert q40["counts"]["limit_rules"] == 213 and q40["counts"]["table_rows"] > 0
        assert q40["last_ingestion"]["report_summary"]["coverage_ratio"] is not None
        assert q40["in_active_index"] is False  # nothing active yet
        assert q40["version"]["sha256"] == sha and sum(q40["review_by_status"].values()) == 213
        stubs = c.get("/api/v1/admin/documents?include_stubs=true", headers=ADMIN).json()
        assert any(d["document_type"] == "external_stub" for d in stubs)

        runs = c.get("/api/v1/admin/documents/QCVN_40_2025/ingestion-runs", headers=ADMIN).json()
        assert runs and runs[0]["report"]["rules"] == 213
        assert (
            c.get("/api/v1/admin/documents/NOPE/ingestion-runs", headers=ADMIN).status_code == 404
        )

        r = c.post("/api/v1/admin/index-versions/test-index@1536/activate", headers=ADMIN)
        assert r.status_code == 200 and r.json()["activated"] == "test-index@1536"
        vs = c.get("/api/v1/admin/index-versions", headers=ADMIN).json()
        assert [v["is_active"] for v in vs] == [True]
        docs = {d["document_id"]: d for d in c.get("/api/v1/admin/documents", headers=ADMIN).json()}
        assert docs["QCVN_40_2025"]["in_active_index"] is True
        assert docs["TT_06_2025"]["stale"] is True and docs["QCVN_19_2024"]["not_indexed"] is True
        with maker() as s:
            ev = (
                s.execute(select(m.AuditEvent).where(m.AuditEvent.action == "index.activate"))
                .scalars()
                .one()
            )
            assert ev.target == "test-index@1536"
        assert (
            c.post("/api/v1/admin/index-versions/missing/activate", headers=ADMIN).status_code
            == 404
        )
        stats = c.get("/api/v1/admin/stats", headers=ADMIN).json()
        assert stats["limit_rules"] >= 213
    finally:
        teardown_client()
        with maker.begin() as s:
            s.execute(delete(m.IndexVersion))
