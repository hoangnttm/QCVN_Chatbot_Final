"""Persistence gate: artefacts round-trip into PostgreSQL (`db_settings` fixture in conftest)."""

from sqlalchemy import select

from app.db import models as m
from app.db.loader import ensure_registry_documents, load_document
from app.registry.loader import load_registry


def test_load_tt06_and_qcvn40_round_trip(db_settings):
    from app.db.session import get_sessionmaker

    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker() as session:
        n = ensure_registry_documents(session, reg)
        assert n == len(reg.documents) + len(reg.external_stubs)
        counts = load_document("QCVN_40_2025", reg, db_settings, session)
        session.commit()
    assert (
        counts["limit_rules"] == 213
        and counts["table_rows"] == 199
        and counts["evidence_chunks"] == 234
    )

    with maker() as session:
        # provenance survives: a rule points at its row, its row at its table, all under the doc
        rule = session.get(m.LimitRule, "QCVN40_2025.B1.r1.F>2000.B")
        assert rule is not None and rule.value == 50 and rule.parameter_id == "BOD5"
        row = session.get(m.TableRow, rule.row_id)
        assert row is not None and row.values["F>2000.B"]["value"] == 50
        assert session.get(m.TableDef, row.table_code).document_id == "QCVN_40_2025"
        chunk = session.get(m.EvidenceChunk, "chunk:QCVN40_2025.B2.r4.2")
        assert chunk is not None and chunk.embedding is None and "≤ 8,0" in chunk.context_text
        rels = (
            session.execute(
                select(m.DocumentRelation).where(
                    m.DocumentRelation.source_document_id == "QCVN_40_2025"
                )
            )
            .scalars()
            .all()
        )
        assert sum(1 for r in rels if r.origin == "registry" and r.type == "REPLACES") == 11
        assert sum(1 for r in rels if r.origin == "text" and r.type == "REPLACES") == 11

        # reloading is idempotent and keeps an embedding for an unchanged chunk
        chunk.embedding = [0.001] * m.EMBEDDING_DIM
        chunk.embedding_model = "test"
        session.commit()
    with maker() as session:
        counts2 = load_document("QCVN_40_2025", reg, db_settings, session)
        session.commit()
        assert counts2["evidence_chunks"] == 234 and counts2["embeddings_reused"] == 1
        again = session.get(m.EvidenceChunk, "chunk:QCVN40_2025.B2.r4.2")
        assert again.embedding is not None and again.embedding_model == "test"
        runs = (
            session.execute(
                select(m.IngestionRun).where(m.IngestionRun.document_id == "QCVN_40_2025")
            )
            .scalars()
            .all()
        )
        assert len(runs) >= 2  # one per load; other test modules may have loaded it too

    with maker() as session:
        counts3 = load_document("TT_06_2025", reg, db_settings, session)
        session.commit()
        assert counts3["transition_rules"] == 6 and counts3["evidence_chunks"] == 23
        facts = (
            session.execute(
                select(m.RegistryFact).where(m.RegistryFact.document_id == "TT_06_2025")
            )
            .scalars()
            .all()
        )
        assert any(f.fact_type == "effective_date" and f.value == "2025-09-01" for f in facts)
