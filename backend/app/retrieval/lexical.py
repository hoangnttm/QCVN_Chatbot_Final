"""In-process BM25 over evidence chunks (ADR-0002 §2): rebuilt from PostgreSQL, cached on disk.

Document tokens = pyvi tokens of `context_text` + canonical entity tokens (`param_bod5`,
`unit_mg_l`, `doc_qcvn_40_2025`, `ctype_table_row`) so query-side alias expansion meets them.
The cache signature is the set of chunk ids, so any reload of a document invalidates it.
"""

from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models as m
from app.retrieval.tokenize import canonical_token, tokenize


@dataclass
class ChunkMeta:
    chunk_id: str
    document_id: str
    chunk_type: str
    domain: str
    text_status: str


def signature(session: Session) -> str:
    """Changes whenever a chunk is added, removed or its context_text changes (re-ingest)."""
    rows = sorted(
        session.execute(
            select(m.EvidenceChunk.chunk_id, func.md5(m.EvidenceChunk.context_text))
        ).all()
    )
    return hashlib.sha1("\n".join(f"{a}:{b}" for a, b in rows).encode()).hexdigest()[:16]


class BM25Index:
    def __init__(self, metas: list[ChunkMeta], tokens: list[list[str]], sig: str) -> None:
        self.metas = metas
        self.tokens = tokens
        self.sig = sig
        self.bm25 = BM25Okapi(tokens)
        self._pos = {mt.chunk_id: i for i, mt in enumerate(metas)}

    # ------------------------------------------------------------------ build / cache

    @staticmethod
    def doc_tokens(c: m.EvidenceChunk) -> list[str]:
        toks = tokenize(c.context_text)
        ents = c.entities if isinstance(c.entities, dict) else {}
        if ents.get("parameter_id"):
            toks.append(canonical_token("param", ents["parameter_id"]))
        if ents.get("unit_id"):
            toks.append(canonical_token("unit", ents["unit_id"]))
        for pid in ents.get("characteristic_parameter_ids") or []:
            toks.append(canonical_token("param", pid))
        if ents.get("sector_id"):
            toks.append(canonical_token("sector", ents["sector_id"]))
        toks.append(canonical_token("doc", c.document_id))
        toks.append(f"ctype_{c.chunk_type}")
        return toks

    @classmethod
    def build(cls, session: Session) -> BM25Index:
        rows = list(
            session.execute(select(m.EvidenceChunk).order_by(m.EvidenceChunk.chunk_id)).scalars()
        )
        metas = [
            ChunkMeta(c.chunk_id, c.document_id, c.chunk_type, c.domain, c.text_status)
            for c in rows
        ]
        tokens = [cls.doc_tokens(c) for c in rows]
        return cls(metas, tokens, signature(session))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump({"metas": self.metas, "tokens": self.tokens, "sig": self.sig}, fh)

    @classmethod
    def load(cls, path: Path) -> BM25Index:
        with open(path, "rb") as fh:
            d = pickle.load(fh)
        return cls(d["metas"], d["tokens"], d["sig"])

    @classmethod
    def load_or_build(cls, session: Session, cache_dir: Path) -> BM25Index:
        sig = signature(session)
        path = cache_dir / f"bm25-{sig}.pkl"
        if path.exists():
            idx = cls.load(path)
            if idx.sig == sig:
                return idx
        idx = cls.build(session)
        idx.save(path)
        return idx

    # ------------------------------------------------------------------ search

    def search(
        self,
        query_tokens: list[str],
        k: int = 50,
        domains: set[str] | None = None,
        chunk_types: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        scores = self.bm25.get_scores(query_tokens)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        out: list[tuple[str, float]] = []
        for i in order:
            if scores[i] <= 0:
                break
            mt = self.metas[i]
            if domains and mt.domain not in domains:
                continue
            if chunk_types and mt.chunk_type not in chunk_types:
                continue
            out.append((mt.chunk_id, float(scores[i])))
            if len(out) >= k:
                break
        return out

    def meta(self, chunk_id: str) -> ChunkMeta | None:
        i = self._pos.get(chunk_id)
        return self.metas[i] if i is not None else None

    def stats(self) -> dict[str, Any]:
        return {"chunks": len(self.metas), "signature": self.sig}
