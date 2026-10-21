"""Carry the embeddings of unchanged chunks from another database into the current one.

A new database (new schema version) is loaded from the ingestion artefacts, so its chunks have no
vectors. A vector depends only on the model and on `context_text`; where both are the same as in
the source database the vector is copied instead of being bought again from the embedding API.
The source database is only read.

Usage:
    DATABASE_URL=postgresql+psycopg://…/qcvn_v2 \
        python backend/scripts/carry_embeddings.py --source postgresql+psycopg://…/qcvn
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select, update  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.db import models as m  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.retrieval.index import record_index_version  # noqa: E402


def _hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="SQLAlchemy URL of the database to read from")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings()
    if args.source == settings.database_url:
        print("source and target are the same database")
        return 1

    model = settings.embedding_model
    with create_engine(args.source).connect() as src:
        have = {
            row.chunk_id: row
            for row in src.execute(
                select(
                    m.EvidenceChunk.chunk_id,
                    m.EvidenceChunk.context_text,
                    m.EvidenceChunk.embedding,
                    m.EvidenceChunk.embedded_at,
                ).where(
                    m.EvidenceChunk.embedding.is_not(None),
                    m.EvidenceChunk.embedding_model == model,
                )
            )
        }

    copied, changed, new = 0, [], []
    with get_sessionmaker(settings).begin() as session:
        pending = session.execute(
            select(m.EvidenceChunk.chunk_id, m.EvidenceChunk.context_text).where(
                m.EvidenceChunk.embedding.is_(None)
            )
        ).all()
        for chunk_id, context_text in pending:
            old = have.get(chunk_id)
            if old is None:
                new.append(chunk_id)
            elif _hash(old.context_text) != _hash(context_text):
                changed.append(chunk_id)
            else:
                session.execute(
                    update(m.EvidenceChunk)
                    .where(m.EvidenceChunk.chunk_id == chunk_id)
                    .values(
                        embedding=old.embedding, embedding_model=model, embedded_at=old.embedded_at
                    )
                )
                copied += 1

    status = record_index_version(
        SimpleNamespace(model_name=model, dim=settings.embedding_dim), settings
    )
    print(
        f"[carry] model={model} pending={len(pending)} copied={copied} "
        f"text_changed={len(changed)} not_in_source={len(new)} index_status={status}"
    )
    for chunk_id in (changed + new)[:10]:
        print("      to embed:", chunk_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
