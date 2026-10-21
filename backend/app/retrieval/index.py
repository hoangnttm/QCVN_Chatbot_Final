"""Embed evidence chunks into pgvector and keep `index_versions` bookkeeping (PLAN.md §4.1 H, UC-08).

Only chunks without a vector — or with a vector from another model — are (re)embedded, and every
batch is committed on its own so an interrupted run resumes where it stopped. An index version is
`building` while chunks are still pending and `inactive` once complete; activation is a manual
admin step (UC-08), never done here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import Settings, get_settings
from app.db import models as m
from app.db.session import get_sessionmaker
from app.retrieval.embedder import Embedder

# Rough Vietnamese legal text ratio for the dry-run estimate; billing counts real tokens.
CHARS_PER_TOKEN = 3.0
USD_PER_M_TOKENS = 0.15  # gemini-embedding-001 list price (ADR-0005)


def index_label(model_name: str, dim: int) -> str:
    return f"{model_name}@{dim}"


def _pending_stmt(model_name: str, doc_ids: list[str] | None):
    stmt = select(m.EvidenceChunk.chunk_id, m.EvidenceChunk.context_text).where(
        or_(m.EvidenceChunk.embedding.is_(None), m.EvidenceChunk.embedding_model != model_name)
    )
    if doc_ids:
        stmt = stmt.where(m.EvidenceChunk.document_id.in_(doc_ids))
    return stmt.order_by(m.EvidenceChunk.document_id, m.EvidenceChunk.chunk_id)


def pending_summary(
    settings: Settings, doc_ids: list[str] | None = None, model_name: str | None = None
) -> dict[str, float]:
    """What an `embed` run would send: chunk count, characters, token/cost estimate."""
    maker = get_sessionmaker(settings)
    with maker() as session:
        rows = session.execute(_pending_stmt(model_name or settings.embedding_model, doc_ids)).all()
    chars = sum(len(t) for _, t in rows)
    tokens = chars / CHARS_PER_TOKEN
    return {
        "pending_chunks": len(rows),
        "chars": chars,
        "est_tokens": round(tokens),
        "est_usd": round(tokens / 1e6 * USD_PER_M_TOKENS, 4),
        "over_max_chars": sum(1 for _, t in rows if len(t) > settings.embedding_max_chars),
    }


def embed_pending(
    embedder: Embedder,
    settings: Settings | None = None,
    doc_ids: list[str] | None = None,
    batch_size: int | None = None,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int | str]:
    settings = settings or get_settings()
    if embedder.dim != m.EMBEDDING_DIM:
        raise RuntimeError(
            f"embedder {embedder.model_name} returns {embedder.dim} dims but "
            f"evidence_chunks.embedding is vector({m.EMBEDDING_DIM}); add a migration first"
        )
    batch_size = batch_size or settings.embedding_batch_size
    maker = get_sessionmaker(settings)
    with maker() as session:
        pending = session.execute(_pending_stmt(embedder.model_name, doc_ids)).all()
    if limit is not None:
        pending = pending[:limit]

    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = embedder.embed_documents([text for _, text in batch])
        now = datetime.now(UTC)
        with maker.begin() as session:
            session.execute(
                update(m.EvidenceChunk),
                [
                    {
                        "chunk_id": chunk_id,
                        "embedding": vec,
                        "embedding_model": embedder.model_name,
                        "embedded_at": now,
                    }
                    for (chunk_id, _), vec in zip(batch, vectors, strict=True)
                ],
            )
        embedded += len(batch)
        if progress:
            progress(f"embedded {embedded}/{len(pending)}")

    status = record_index_version(embedder, settings)
    return {"pending": len(pending), "embedded": embedded, "index_status": status}


def record_index_version(embedder: Embedder, settings: Settings) -> str:
    """Upsert the index version for this model; `inactive` when every chunk carries its vector."""
    maker = get_sessionmaker(settings)
    with maker.begin() as session:
        remaining = session.execute(
            select(func.count()).select_from(_pending_stmt(embedder.model_name, None).subquery())
        ).scalar_one()
        hashes = {
            doc_id: sha
            for doc_id, sha in session.execute(
                select(m.DocumentVersion.document_id, m.DocumentVersion.sha256).where(
                    m.DocumentVersion.is_current.is_(True)
                )
            )
        }
        status = "inactive" if remaining == 0 else "building"
        label = index_label(embedder.model_name, embedder.dim)
        stmt = pg_insert(m.IndexVersion).values(
            label=label,
            status=status,
            embedding_model=embedder.model_name,
            embedding_dim=embedder.dim,
            document_hashes=hashes,
            notes=f"{remaining} chunk(s) pending" if remaining else "all chunks embedded",
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[m.IndexVersion.label],
            set_={
                "document_hashes": stmt.excluded.document_hashes,
                "notes": stmt.excluded.notes,
                # an admin-activated index is never demoted here (UC-08); otherwise mirror state
                "status": case(
                    (m.IndexVersion.status == "active", "active"), else_=stmt.excluded.status
                ),
            },
        )
        session.execute(stmt)
    return status
