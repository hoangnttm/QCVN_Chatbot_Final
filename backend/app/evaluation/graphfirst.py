"""Graph-first search executed in Neo4j — relations pick the candidates, the embeddings stored on
the chunk nodes rank them (`graph_candidates.neo4j_search`) — against the production-style hybrid
over the whole corpus. Dev check for the proposal "quan hệ trước, vector sau, cùng trên Neo4j".

Per gold item (must-have chunks = grade 2), recall_must@10 of:

- `hybrid_all`    BM25 + pgvector over the corpus, RRF (the baseline of `evaluate.py coverage`)
- `vector_all`    Neo4j vector index over the corpus (same embeddings, domain filter)
- `gf_vector`     Neo4j: candidates by relations, ranked by cosine on the nodes
- `gf_hybrid`     the same candidates, RRF of that cosine ranking and BM25 restricted to the set

`parity` checks that the Cypher lookup returns exactly the in-memory candidate set the coverage
metric was measured on. An empty candidate set falls back to `vector_all` / `hybrid_all`.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.evaluation.coverage import recall
from app.evaluation.gold_schema import GoldItem
from app.graph import vectors
from app.retrieval import graph_candidates as gc
from app.retrieval.retriever import RRF_K, RetrievalService, Retriever, SearchRequest
from app.retrieval.tokenize import tokenize

K = 10
VARIANTS = ("hybrid_all", "vector_all", "gf_vector", "gf_hybrid")


def rrf(*rankings: list[str]) -> list[str]:
    score: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            score[cid] = score.get(cid, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(score, key=lambda c: (-score[c], c))


def run(
    session: Session, svc: RetrievalService, items: list[GoldItem]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    svc.ensure(session)
    assert svc.bm25 is not None
    rt = svc.graph
    st = rt.status(force=True)
    reason = st.usable_for(svc.index_label, need_vectors=True)
    if reason:
        raise RuntimeError(f"graph-first check needs the projection with embeddings: {reason}")
    assert st.server_version is not None
    chunk_ids = set(session.execute(select(m.EvidenceChunk.chunk_id)).scalars())
    view = gc.load_view(rt, chunk_ids)
    retriever = Retriever(session, svc)
    embedder = svc.embedder()
    rows: list[dict[str, Any]] = []
    for it in items:
        must = [r.chunk_id for r in it.relevant if r.grade == 2]
        if not must:
            continue
        res = retriever.search(
            SearchRequest(
                question=it.question,
                as_of_date=it.as_of_date,
                k=K,
                candidates=200,
                use_structured=False,
                type_boost=False,
                expand=False,
                return_ranking=True,
            )
        )
        qu = res.understanding
        hybrid_all = [c.chunk_id for c in res.ranking]
        vec = embedder.embed_query(it.question)
        domains = retriever._domains_for(qu, None)

        t0 = time.perf_counter()
        vector_all = [c for c, _ in vectors.search(rt.run, vec, 50, domains, st.server_version)]
        t1 = time.perf_counter()
        found = gc.neo4j_search(rt, view, qu, vec)
        t2 = time.perf_counter()

        cand = {r["id"] for r in found}
        expected = gc.union(gc.candidates(view, qu))
        gf_vector = [r["id"] for r in found if r["score"] is not None]
        bm25 = svc.bm25.search(tokenize(it.question) + qu.expansions, k=len(chunk_ids))
        gf_hybrid = rrf([c for c, _ in bm25 if c in cand], gf_vector)
        if not cand:  # nothing to go on: search everything
            gf_vector, gf_hybrid = vector_all, hybrid_all
        rankings = {
            "hybrid_all": hybrid_all,
            "vector_all": vector_all,
            "gf_vector": gf_vector,
            "gf_hybrid": gf_hybrid,
        }
        rows.append(
            {
                "item_id": it.id,
                "tier": it.tier,
                "stratum": it.stratum,
                "intent": qu.intent,
                "must": must,
                "candidates": len(cand),
                "parity": cand == expected,
                "fallback": not cand,
                "degraded": any("vector search unavailable" in w for w in res.warnings),
                "recall": {k: recall(v, must, K) for k, v in rankings.items()},
                "top": {k: v[:K] for k, v in rankings.items()},
                "ms": {
                    "vector_all": round((t1 - t0) * 1000, 1),
                    "graph_first": round((t2 - t1) * 1000, 1),
                },
            }
        )
    graph = {"projection_version": st.projection_version, "chunks": len(chunk_ids)}
    return rows, summarize(rows) | {"graph": graph}


def _pct(xs: list[float], q: float) -> float:
    return round(sorted(xs)[min(len(xs) - 1, int(len(xs) * q))], 1)


def _block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    for v in VARIANTS:
        out[v] = round(statistics.mean(r["recall"][v] for r in rows), 4)
    for v in ("vector_all", "gf_vector", "gf_hybrid"):
        out[f"{v}_vs_hybrid_all"] = {
            "better": sum(r["recall"][v] > r["recall"]["hybrid_all"] + 1e-9 for r in rows),
            "worse": sum(r["recall"][v] < r["recall"]["hybrid_all"] - 1e-9 for r in rows),
        }
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sizes = [r["candidates"] for r in rows if not r["fallback"]]
    tiers = sorted({r["tier"] for r in rows})
    return {
        "all": _block(rows),
        "tiers": {t: _block([r for r in rows if r["tier"] == t]) for t in tiers},
        "parity": sum(r["parity"] for r in rows),
        "fallback": sum(r["fallback"] for r in rows),
        "degraded": sum(r["degraded"] for r in rows),
        "candidates": (
            {"median": int(statistics.median(sizes)), "p90": _pct(sizes, 0.9), "max": max(sizes)}
            if sizes
            else {}
        ),
        "latency_ms": {
            k: {
                "p50": _pct([r["ms"][k] for r in rows], 0.5),
                "p95": _pct([r["ms"][k] for r in rows], 0.95),
            }
            for k in ("vector_all", "graph_first")
        },
    }


def render(run_id: str, summ: dict[str, Any]) -> str:
    lines = [
        f"# Graph-first search in Neo4j — {run_id}",
        "",
        f"Graph {summ['graph']['projection_version']}, {summ['graph']['chunks']} chunks. "
        f"recall = recall_must@{K}. Cypher lookup equals the in-memory candidate set on "
        f"{summ['parity']}/{summ['all']['n']} items; {summ['fallback']} fell back (no candidates).",
        "",
        "| items | n | hybrid_all | vector_all | gf_vector | gf_hybrid | gf_hybrid vs hybrid_all |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, b in [("all", summ["all"])] + [(f"tier {t}", v) for t, v in summ["tiers"].items()]:
        d = b["gf_hybrid_vs_hybrid_all"]
        lines.append(
            f"| {label} | {b['n']} | {b['hybrid_all']} | {b['vector_all']} | {b['gf_vector']} "
            f"| {b['gf_hybrid']} | +{d['better']} / -{d['worse']} |"
        )
    lines += [
        "",
        f"Candidates per question: {summ['candidates']}.",
        "Latency (ms, one Neo4j round trip each, embedding of the question not included): "
        f"{summ['latency_ms']}.",
    ]
    if summ["degraded"]:
        lines.append(f"**DEGRADED on {summ['degraded']} item(s) — do not use.**")
    return "\n".join(lines) + "\n"
