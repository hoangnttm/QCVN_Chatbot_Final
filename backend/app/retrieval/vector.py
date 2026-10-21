"""Cosine search with a Gemini RETRIEVAL_QUERY vector: pgvector over `evidence_chunks.embedding`
(default) or the Neo4j vector index over `EvidenceChunk.embedding` (app.graph.vectors)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.graph import vectors
from app.graph.runtime import GraphRuntime
from app.retrieval.embedder import Embedder


class VectorIndex:
    def __init__(self, session: Session, embedder: Embedder) -> None:
        self.s = session
        self.embedder = embedder
        self._cache: dict[str, list[float]] = {}

    def embed(self, query: str) -> list[float]:
        if query not in self._cache:
            self._cache[query] = self.embedder.embed_query(query)
        return self._cache[query]

    def search(
        self, query: str, k: int = 50, domains: set[str] | None = None
    ) -> list[tuple[str, float]]:
        vec = self.embed(query)
        dist = m.EvidenceChunk.embedding.cosine_distance(vec).label("d")
        stmt = select(m.EvidenceChunk.chunk_id, dist).where(m.EvidenceChunk.embedding.is_not(None))
        if domains:
            stmt = stmt.where(m.EvidenceChunk.domain.in_(sorted(domains)))
        stmt = stmt.order_by(dist).limit(k)
        return [(cid, 1.0 - float(d)) for cid, d in self.s.execute(stmt)]


class Neo4jVectorIndex:
    """Same contract as `VectorIndex`, answered from the graph projection. Raises when the
    projection is unusable (down, stale, built without embeddings) — the retriever reports it."""

    def __init__(
        self, graph: GraphRuntime, embedder: Embedder, active_label: str | None = None
    ) -> None:
        self.graph = graph
        self.embedder = embedder
        self.active_label = active_label
        self._cache: dict[str, list[float]] = {}

    def embed(self, query: str) -> list[float]:
        if query not in self._cache:
            self._cache[query] = self.embedder.embed_query(query)
        return self._cache[query]

    def search(
        self, query: str, k: int = 50, domains: set[str] | None = None
    ) -> list[tuple[str, float]]:
        st = self.graph.status()
        reason = st.usable_for(self.active_label, need_vectors=True)
        if reason:
            raise RuntimeError(reason)
        assert st.server_version is not None
        try:
            return vectors.search(self.graph.run, self.embed(query), k, domains, st.server_version)
        except Exception as e:
            self.graph.mark_failed(e)
            raise
