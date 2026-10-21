"""Personalized PageRank over the Neo4j projection as a retrieval signal (plan 2026-09-29 §2, E3).

Seeds = the entities the query understanding found (parameters, zone, fuel, receiving body,
sector, documents) plus the top fused chunks. PPR spreads that mass along the graph, so a
footnote, a definition or a transition clause linked to the seeds scores high even when it
shares no words with the question. Two ways to use the scores (D30, chosen on dev):

- `fuse`   — PPR ranking is a third list in RRF, next to BM25 and vector: can add candidates.
             The seed chunks are left out of that list — restarts give them the most mass, so
             they would only repeat (double-count) the fused votes that made them seeds
- `rerank` — only reorders the fused head (top-N) by α·rrf + (1−α)·ppr: never adds candidates

`seed_only` (control C11-seedonly) scores the seed chunks by their seed weight without any graph
walk, to separate the effect of propagation from the effect of re-weighting the seeds.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import Settings
from app.graph import gds
from app.graph.runtime import GraphRuntime
from app.ontology import corpus as corpus_mod
from app.retrieval.query import QueryUnderstanding

Mode = Literal["fuse", "rerank"]
SeedMode = Literal["both", "entity", "chunk"]


@dataclass
class PPRParams:
    mode: Mode = "fuse"
    variant: gds.Variant = "full"
    seeds: SeedMode = "both"
    seed_only: bool = False
    exclude_seeds: bool = True  # fuse: leave the seed chunks out of the PPR list
    damping: float = 0.85
    iterations: int = 20
    entity_mass: float = 0.5
    seed_chunks: int = 10
    weight: float = 1.0  # λ of the PPR list in RRF (fuse)
    alpha: float = 0.5  # rerank: share of the fused score
    rerank_top_n: int = 30

    @classmethod
    def from_settings(cls, s: Settings, **over: Any) -> PPRParams:
        base = cls(
            damping=s.ppr_damping,
            iterations=s.ppr_max_iterations,
            entity_mass=s.ppr_entity_mass,
            seed_chunks=s.ppr_seed_chunks,
            weight=s.ppr_weight,
        )
        for k, v in over.items():
            if v is not None:
                setattr(base, k, v)
        return base


@dataclass
class PPRResult:
    scores: list[tuple[str, float]] = field(default_factory=list)  # chunk_id, score (best first)
    info: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def entity_nodes(qu: QueryUnderstanding) -> list[str]:
    """Graph node ids of the entities in the question (only ids the projection defines)."""
    out: list[str] = [f"param:{p}" for p in qu.parameters]
    corpus = corpus_mod.current()
    domains = corpus.candidates(qu.domain)
    if qu.zone in corpus.zones():
        out += corpus.zone_nodes(domains, qu.zone)
    if qu.fuel_type:
        out.append(f"fuel:{qu.fuel_type}")
    if qu.receiving_body:
        out.append(f"rbt:{qu.receiving_body}")
    if qu.sector_id:
        out.append(f"sector:{qu.sector_id}")
    out += [f"doc:{d}" for d in qu.document_ids]
    return list(dict.fromkeys(out))


def build_seeds(
    qu: QueryUnderstanding, ranked: list[tuple[str, float]], p: PPRParams
) -> dict[str, float]:
    """Seed weights summing to 1: `entity_mass` spread evenly over the entity nodes, the rest over
    the top `seed_chunks` fused chunks in proportion to their fused score."""
    # the structure-only graph has no ontology nodes: entity seeds would not exist in it
    ents = entity_nodes(qu) if p.seeds in ("both", "entity") and p.variant != "struct" else []
    top = ranked[: p.seed_chunks] if p.seeds in ("both", "chunk") else []
    if not ents and not top:
        return {}
    ent_mass = p.entity_mass if (ents and top) else (1.0 if ents else 0.0)
    seeds: dict[str, float] = {e: ent_mass / len(ents) for e in ents}
    total = sum(max(s, 0.0) for _, s in top)
    for cid, s in top:
        w = (1.0 - ent_mass) * (max(s, 0.0) / total if total > 0 else 1.0 / len(top))
        seeds[cid] = seeds.get(cid, 0.0) + w
    return seeds


class GraphRanker:
    def __init__(self, runtime: GraphRuntime) -> None:
        self.rt = runtime

    def rank(
        self,
        qu: QueryUnderstanding,
        ranked: list[tuple[str, float]],
        p: PPRParams,
        active_label: str | None,
    ) -> PPRResult:
        t0 = time.perf_counter()
        seeds = build_seeds(qu, ranked, p)
        info: dict[str, Any] = {
            "mode": p.mode,
            "variant": p.variant,
            "seeds": len(seeds),
            "entity_seeds": [k for k in seeds if not k.startswith("chunk:")],
            "damping": p.damping,
        }
        if not seeds:
            return PPRResult(info=info | {"skipped": "no seeds"})
        if p.seed_only:
            scores = sorted(
                ((k, v) for k, v in seeds.items() if k.startswith("chunk:")), key=lambda x: -x[1]
            )
            info["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            return PPRResult(scores=scores, info=info | {"seed_only": True})
        st = self.rt.status()
        reason = st.usable_for(active_label, need_gds=True)
        if reason:
            return PPRResult(info=info, error=reason)
        try:
            assert st.projection_version is not None
            name = gds.ensure_graph(self.rt, st.projection_version, p.variant)
            t1 = time.perf_counter()
            scores = gds.personalized_pagerank(
                self.rt, name, seeds, damping=p.damping, iterations=p.iterations
            )
            info["gds_ms"] = int((time.perf_counter() - t1) * 1000)
        except Exception as e:  # noqa: BLE001 - graph trouble must never break retrieval
            self.rt.mark_failed(e)
            return PPRResult(info=info, error=f"{type(e).__name__}: {str(e)[:160]}")
        if p.mode == "fuse" and p.exclude_seeds:
            scores = [(c, v) for c, v in scores if c not in seeds]
        info["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        return PPRResult(scores=scores, info=info)


def fuse(
    ranked: list[tuple[str, float]],
    ppr: list[tuple[str, float]],
    *,
    rrf_k: int,
    weight: float,
    admit: Any,
) -> tuple[list[tuple[str, float]], set[str]]:
    """Add boost·λ/(rrf_k + rank) of the PPR list to the fused scores (the same intent-aware type
    boost the fused score got). `admit(chunk_id)` returns that boost, or None when the chunk is
    filtered out (domain / effectivity) — filtered chunks take no rank. Returns the new ranking
    and the ids PPR added as new candidates."""
    score = dict(ranked)
    added: set[str] = set()
    rank = 0
    for cid, _ in ppr:
        mult = admit(cid)
        if mult is None:
            continue
        if cid not in score:
            score[cid] = 0.0
            added.add(cid)
        rank += 1
        score[cid] += mult * weight / (rrf_k + rank)
    return sorted(score.items(), key=lambda x: -x[1]), added


def rerank_head(
    ranked: list[tuple[str, float]], ppr: list[tuple[str, float]], *, top_n: int, alpha: float
) -> list[tuple[str, float]]:
    """Reorder the top `top_n` by α·(rrf/max) + (1−α)·(ppr/max); the tail and the stored scores
    are kept, so the result only differs in the order of the head."""
    head, tail = ranked[:top_n], ranked[top_n:]
    if not head:
        return ranked
    pmap = dict(ppr)
    fmax = max(s for _, s in head) or 1.0
    pmax = max((pmap.get(c, 0.0) for c, _ in head), default=0.0) or 1.0
    order = sorted(
        head,
        key=lambda x: -(alpha * x[1] / fmax + (1 - alpha) * pmap.get(x[0], 0.0) / pmax),
    )
    return order + tail
