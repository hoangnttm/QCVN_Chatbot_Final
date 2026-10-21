"""Embedding stage: pending-only, resumable, pgvector nearest-neighbour works, index_versions kept.
The provider is faked (no network); the live Gemini check runs only with QCVN_LIVE_TESTS=1."""

import hashlib
import os

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db import models as m
from app.db.loader import load_document
from app.db.session import get_sessionmaker
from app.registry.loader import load_registry
from app.retrieval.embedder import l2_normalize
from app.retrieval.index import embed_pending, index_label, pending_summary


class FakeEmbedder:
    model_name = "fake-embedder"
    dim = m.EMBEDDING_DIM

    def __init__(self) -> None:
        self.calls: list[int] = []

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [(digest[i % len(digest)] - 128) / 128 for i in range(self.dim)]
        return l2_normalize(raw)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def test_embed_pending_is_resumable_and_searchable(db_settings: Settings):
    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker.begin() as session:
        counts = load_document("TT_06_2025", reg, db_settings, session)
    assert counts["evidence_chunks"] == 23

    fake = FakeEmbedder()
    before = pending_summary(db_settings, ["TT_06_2025"], fake.model_name)
    assert before["pending_chunks"] == 23 and before["est_tokens"] > 0

    first = embed_pending(fake, db_settings, ["TT_06_2025"], batch_size=10, limit=15)
    assert first["embedded"] == 15 and fake.calls == [10, 5]
    assert pending_summary(db_settings, ["TT_06_2025"], fake.model_name)["pending_chunks"] == 8

    second = embed_pending(fake, db_settings, ["TT_06_2025"], batch_size=10)
    assert second["embedded"] == 8
    assert pending_summary(db_settings, ["TT_06_2025"], fake.model_name)["pending_chunks"] == 0
    # vectors from another model count as pending for the configured (Gemini) model
    assert pending_summary(db_settings, ["TT_06_2025"])["pending_chunks"] == 23

    with maker() as session:
        chunk = session.execute(
            select(m.EvidenceChunk).where(m.EvidenceChunk.document_id == "TT_06_2025").limit(1)
        ).scalar_one()
        assert chunk.embedding_model == "fake-embedder" and chunk.embedded_at is not None
        nearest = session.execute(
            select(m.EvidenceChunk.chunk_id)
            .where(m.EvidenceChunk.embedding.is_not(None))
            .order_by(
                m.EvidenceChunk.embedding.cosine_distance(fake.embed_query(chunk.context_text))
            )
            .limit(1)
        ).scalar_one()
        assert nearest == chunk.chunk_id

        iv = session.execute(
            select(m.IndexVersion).where(
                m.IndexVersion.label == index_label(fake.model_name, m.EMBEDDING_DIM)
            )
        ).scalar_one()
        assert iv.embedding_model == "fake-embedder" and iv.embedding_dim == m.EMBEDDING_DIM
        assert iv.status in ("building", "inactive")  # other test documents may still be pending
        assert iv.document_hashes["TT_06_2025"] == reg.by_id("TT_06_2025").source.sha256

    # a reload of the same artefacts keeps every vector (no re-spend)
    with maker.begin() as session:
        again = load_document("TT_06_2025", reg, db_settings, session)
    assert again["embeddings_reused"] == 23


@pytest.mark.skipif(os.environ.get("QCVN_LIVE_TESTS") != "1", reason="set QCVN_LIVE_TESTS=1")
def test_gemini_embedder_live():
    from app.retrieval.embedder import GeminiEmbedder

    settings = Settings()
    if not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY not configured")
    emb = GeminiEmbedder(settings)
    docs = emb.embed_documents(["Giá trị giới hạn BOD5 cột B", "Bụi tổng, mg/Nm3"])
    query = emb.embed_query("giới hạn BOD5 nước thải cột B")
    assert len(docs) == 2 and all(len(v) == settings.embedding_dim for v in docs)
    assert abs(sum(x * x for x in query) - 1.0) < 1e-3
    sim = [sum(a * b for a, b in zip(query, d, strict=True)) for d in docs]
    assert sim[0] > sim[1]
