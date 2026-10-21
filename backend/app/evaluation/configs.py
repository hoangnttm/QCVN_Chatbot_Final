"""Evaluation configurations / ablation matrix (plan §3.7). C4 = production."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.qa.models import RetrievalOptions

Level = Literal["retrieval", "qa"]


class EvalConfig(BaseModel):
    name: str
    description: str
    level: Level = "retrieval"
    retrieval: RetrievalOptions = Field(default_factory=RetrievalOptions)
    model: str | None = None
    thinking_level: Literal["low", "medium", "high"] | None = None
    thinking_override_all: bool = False
    validator_mode: Literal["enforce", "observe"] = "enforce"
    understanding_llm: bool = True
    graph_verify: Literal["off", "observe", "enforce"] | None = None  # None → settings
    repeats: int = 1
    k: int = 10


R = RetrievalOptions

CONFIGS: dict[str, EvalConfig] = {
    "C0": EvalConfig(
        name="C0",
        description="BM25-only (no vector, no structured, no boost, no expansion)",
        retrieval=R(use_vector=False, use_structured=False, type_boost=False, expand=False),
    ),
    "C1": EvalConfig(
        name="C1",
        description="dense-only",
        retrieval=R(
            use_bm25=False,
            use_structured=False,
            type_boost=False,
            expand=False,
            use_expansions=False,
        ),
    ),
    "C2": EvalConfig(
        name="C2",
        description="hybrid RRF (BM25 + vector)",
        retrieval=R(use_structured=False, type_boost=False, expand=False),
    ),
    "C3": EvalConfig(
        name="C3", description="hybrid + structured lookup + type boost", retrieval=R(expand=False)
    ),
    "C4": EvalConfig(
        name="C4",
        description="production: hybrid + structured + boost + graph expansion",
        level="qa",
        repeats=3,
    ),
    "C4-nofilter": EvalConfig(
        name="C4-nofilter",
        description="production without domain/effectivity filters",
        retrieval=R(domain_filter=False, effectivity_filter=False),
    ),
    "C7": EvalConfig(
        name="C7",
        description="production without query expansions (BM25 canonical tokens)",
        retrieval=R(use_expansions=False),
    ),
    "C4-offline": EvalConfig(
        name="C4-offline",
        description="CI gate: BM25 + structured + boost + expansion, no vector",
        retrieval=R(use_vector=False),
    ),
    "C5": EvalConfig(
        name="C5",
        description="UNSAFE (eval only): production retrieval, validator observe-only",
        level="qa",
        validator_mode="observe",
        repeats=3,
    ),
    "C6a": EvalConfig(
        name="C6a",
        description="thinking medium for every question",
        level="qa",
        thinking_level="medium",
        thinking_override_all=True,
    ),
    "C6b": EvalConfig(
        name="C6b",
        description="gemini-3.5-flash-lite answers",
        level="qa",
        model="gemini-3.5-flash-lite",
    ),
    "C6c": EvalConfig(
        name="C6c",
        description="gemini-3.1-pro-preview answers",
        level="qa",
        model="gemini-3.1-pro-preview",
    ),
    "C8": EvalConfig(
        name="C8",
        description="rules-only understanding (LLM understanding off)",
        level="qa",
        understanding_llm=False,
    ),
    "C0-e2e": EvalConfig(
        name="C0-e2e",
        description="end-to-end with BM25-only retrieval",
        level="qa",
        retrieval=R(use_vector=False, use_structured=False, type_boost=False, expand=False),
    ),
    "C2-e2e": EvalConfig(
        name="C2-e2e",
        description="end-to-end with plain hybrid retrieval",
        level="qa",
        retrieval=R(use_structured=False, type_boost=False, expand=False),
    ),
    # ---- reranker study (research plan 2026-09-28; D17 keeps production without reranker) ----
    "C3-cap": EvalConfig(
        name="C3-cap",
        description="R0': C3 with only the 3 most specific structured rows boosted (no reranker)",
        retrieval=R(expand=False, structured_cap=3),
    ),
    "C9": EvalConfig(
        name="C9",
        description="C2 + Gemini LLM rerank of the top 30 (flash-lite)",
        retrieval=R(use_structured=False, type_boost=False, expand=False, rerank="llm"),
    ),
    "C10": EvalConfig(
        name="C10",
        description="C3 + Gemini LLM rerank of the top 30 (flash-lite)",
        retrieval=R(expand=False, rerank="llm"),
    ),
    "C10-n20": EvalConfig(
        name="C10-n20",
        description="C10 reranking the top 20",
        retrieval=R(expand=False, rerank="llm", rerank_top_n=20),
    ),
    "C10-n50": EvalConfig(
        name="C10-n50",
        description="C10 reranking the top 50",
        retrieval=R(expand=False, rerank="llm", rerank_top_n=50),
    ),
    "C10-cap": EvalConfig(
        name="C10-cap",
        description="C3-cap + Gemini LLM rerank of the top 30",
        retrieval=R(expand=False, structured_cap=3, rerank="llm"),
    ),
    "C10-flash": EvalConfig(
        name="C10-flash",
        description="C10 with gemini-3.8-flash as reranker",
        retrieval=R(expand=False, rerank="llm", rerank_model="gemini-3.8-flash"),
    ),
    "C4-rr": EvalConfig(
        name="C4-rr",
        description="production + Gemini LLM rerank of the top 30 (end-to-end)",
        level="qa",
        retrieval=R(rerank="llm"),
    ),
    # ---- vector leg served by the Neo4j vector index instead of pgvector (same embeddings) ----
    "C1-neo4j": EvalConfig(
        name="C1-neo4j",
        description="C1 (dense-only) with the vector search in Neo4j",
        retrieval=R(
            use_bm25=False,
            use_structured=False,
            type_boost=False,
            expand=False,
            use_expansions=False,
            vector_backend="neo4j",
        ),
    ),
    "C3-neo4j": EvalConfig(
        name="C3-neo4j",
        description="C3 with the vector search in Neo4j",
        retrieval=R(expand=False, vector_backend="neo4j"),
    ),
    # ---- graph ablation (plan docs/plans/2026-09-29-graph-ppr-path-verification.md §4.2) ----
    # "no graph" = C3; C4 adds the rule-based expansion (PostgreSQL); C11/C12 add PPR (Neo4j GDS)
    "C11": EvalConfig(
        name="C11",
        description="C3 + Personalized PageRank (GDS), PPR list fused into RRF",
        retrieval=R(expand=False, ppr="fuse"),
    ),
    "C11-rerank": EvalConfig(
        name="C11-rerank",
        description="C3 + PPR reordering the fused top 30 only (no new candidates)",
        retrieval=R(expand=False, ppr="rerank"),
    ),
    "C12": EvalConfig(
        name="C12",
        description="C4 (rule expansion) + PPR fused into RRF — full graph",
        retrieval=R(ppr="fuse"),
    ),
    "C11-seedonly": EvalConfig(
        name="C11-seedonly",
        description="control: C11 scoring the seed chunks by seed weight, no graph walk",
        retrieval=R(expand=False, ppr="fuse", ppr_seed_only=True),
    ),
    "C11-shuffle": EvalConfig(
        name="C11-shuffle",
        description="control: C11 on a degree-preserving random rewiring of the graph",
        retrieval=R(expand=False, ppr="fuse", ppr_variant="shuffle"),
    ),
    "C11-struct": EvalConfig(
        name="C11-struct",
        description="control: C11 on document-structure edges only (no ontology/rule edges)",
        retrieval=R(expand=False, ppr="fuse", ppr_variant="struct"),
    ),
    "C11-entity": EvalConfig(
        name="C11-entity",
        description="C11 seeded by query entities only (no chunk seeds)",
        retrieval=R(expand=False, ppr="fuse", ppr_seeds="entity"),
    ),
}

# RQ families with their pre-registered primary contrasts (Holm-Bonferroni per family)
FAMILIES: dict[str, dict[str, object]] = {
    "RQ1": {
        "metric": "ndcg@10",
        "stage": "A",
        "baseline": "C4",
        "contrasts": ["C0", "C1", "C2", "C3"],
    },
    "RQ2": {"metric": "correct_safe", "baseline": "C4", "contrasts": ["C5", "C0-e2e"]},
    "RQ4": {"metric": "correct_safe", "baseline": "C4", "contrasts": ["C6a", "C6b", "C6c"]},
    "RQ5": {
        "metric": "ndcg@10",
        "stage": "A",
        "baseline": "C3",
        "contrasts": ["C3-cap", "C9", "C10", "C10-cap", "C10-flash"],
    },
    # graph ablation (plan 2026-09-29 §4.2); on tier M the primary metric is pack completeness
    "RQ6": {
        "metric": "ndcg@10",
        "stage": "A",
        "baseline": "C3",
        "contrasts": ["C11", "C11-seedonly", "C11-shuffle"],
    },
    # final-report ablation (plan 2026-10-21-kich-ban-ablation): graph versus full text
    "BT-R": {
        "metric": "recall_must@10",
        "stage": "B",
        "baseline": "C2",
        "contrasts": [
            "C0",
            "C1",
            "C9",
            "C3",
            "C4",
            "C4-nofilter",
            "C7",
            "C4-gf",
            "C4-gff",
            "C4-u2",
            "C4-u2-gf",
            "C12",
            "C12-u2",
            "C11-seedonly",
            "C11-shuffle",
        ],
    },
    "BT-E": {
        "metric": "correct_safe",
        "baseline": "C4",
        "contrasts": [
            "S1g",
            "S1g-nov2",
            "S1g-nogf",
            "S1g-nov8",
            "S1",
            "C8",
            "C5",
            "C0-e2e",
            "C2-e2e",
            "C4-rr",
        ],
    },
}

# Regression gates = dev-split baseline minus a margin (2026-09-24, C4-offline, CI-eligible items:
# hit@10 0.888, routing_strict 0.846). Raise them deliberately, never to "make CI green".
CI_GATES: dict[str, float] = {"hit@10": 0.80, "routing_strict": 0.78}


# ---- D74 (2026-10-20): query understanding v2 on the answer path, rule 8 enforced, and the
# full improved system S1 with its leave-one-out arms (plan 2026-10-20-bao-cao-cuoi-mon §3.2)
def _v2(r: RetrievalOptions) -> RetrievalOptions:
    return r.model_copy(update={"understanding_engine": "v2"})


CONFIGS.update(
    {
        "C4-u2": EvalConfig(
            name="C4-u2",
            description="production retrieval with query understanding v2 pinning rows and hops",
            level="qa",
            repeats=3,
            retrieval=_v2(CONFIGS["C4"].retrieval),
        ),
        "C4-V8": EvalConfig(
            name="C4-V8",
            description="production with validator rule 8 (graph path) enforced",
            level="qa",
            repeats=3,
            graph_verify="enforce",
        ),
        "C12-u2": EvalConfig(
            name="C12-u2",
            description="C12 (PPR fuse) + query understanding v2",
            level="qa",
            repeats=3,
            retrieval=_v2(CONFIGS["C12"].retrieval),
        ),
        "C12-V8": EvalConfig(
            name="C12-V8",
            description="C12 (PPR fuse) with rule 8 enforced",
            level="qa",
            repeats=3,
            retrieval=CONFIGS["C12"].retrieval,
            graph_verify="enforce",
        ),
        "S1": EvalConfig(
            name="S1",
            description="full improved system: C12 + understanding v2 + rule 8 enforced",
            level="qa",
            repeats=3,
            retrieval=_v2(CONFIGS["C12"].retrieval),
            graph_verify="enforce",
        ),
        "S1-noppr": EvalConfig(
            name="S1-noppr",
            description="S1 without PPR (= C4 + v2 + rule 8)",
            level="qa",
            repeats=3,
            retrieval=_v2(CONFIGS["C4"].retrieval),
            graph_verify="enforce",
        ),
        "S1-nov8": EvalConfig(
            name="S1-nov8",
            description="S1 with rule 8 observe-only (= C12 + v2)",
            level="qa",
            repeats=3,
            retrieval=_v2(CONFIGS["C12"].retrieval),
            graph_verify="observe",
        ),
        "S1-nov2": EvalConfig(
            name="S1-nov2",
            description="S1 with understanding v1 (= C12 + rule 8)",
            level="qa",
            repeats=3,
            retrieval=CONFIGS["C12"].retrieval,
            graph_verify="enforce",
        ),
    }
)


# ---- graph-first search on the answer path (PO 2026-10-20: "tìm đồ thị trước")
def _gf(r: RetrievalOptions, mode: str = "fuse") -> RetrievalOptions:
    return r.model_copy(update={"graph_first": mode})


CONFIGS.update(
    {
        "C4-gf": EvalConfig(
            name="C4-gf",
            description="production + graph-first region fused into RRF",
            level="qa",
            repeats=3,
            retrieval=_gf(CONFIGS["C4"].retrieval),
        ),
        "C4-gff": EvalConfig(
            name="C4-gff",
            description="production restricted to the graph-first region (filter), fused",
            level="qa",
            repeats=3,
            retrieval=_gf(CONFIGS["C4"].retrieval, "filter"),
        ),
        "C4-u2-gf": EvalConfig(
            name="C4-u2-gf",
            description="C4 + understanding v2 + graph-first (fuse)",
            level="qa",
            repeats=3,
            retrieval=_gf(_v2(CONFIGS["C4"].retrieval)),
        ),
        "S1g": EvalConfig(
            name="S1g",
            description="improved system with graph-first instead of PPR: C4 + v2 + graph-first + rule 8",
            level="qa",
            repeats=3,
            retrieval=_gf(_v2(CONFIGS["C4"].retrieval)),
            graph_verify="enforce",
        ),
    }
)


# ---- leave-one-out arms of S1g (plan 2026-10-21-kich-ban-ablation §5.4)
CONFIGS.update(
    {
        "S1g-nov2": EvalConfig(
            name="S1g-nov2",
            description="S1g with understanding v1 (= C4 + graph-first + rule 8 enforced)",
            level="qa",
            repeats=3,
            retrieval=_gf(CONFIGS["C4"].retrieval),
            graph_verify="enforce",
        ),
        "S1g-nogf": EvalConfig(
            name="S1g-nogf",
            description="S1g without graph-first (= C4 + v2 + rule 8 enforced)",
            level="qa",
            repeats=3,
            retrieval=_v2(CONFIGS["C4"].retrieval),
            graph_verify="enforce",
        ),
        "S1g-nov8": EvalConfig(
            name="S1g-nov8",
            description="S1g with rule 8 observe-only (= C4 + v2 + graph-first)",
            level="qa",
            repeats=3,
            retrieval=_gf(_v2(CONFIGS["C4"].retrieval)),
            graph_verify="observe",
        ),
    }
)


# ---- PO 2026-10-21: the two switches on the plain hybrid (no v1 structured lookup, no rule
# expansion): v2 understanding and graph-first, alone and together
CONFIGS.update(
    {
        "C2-u2": EvalConfig(
            name="C2-u2",
            description="hybrid RRF + understanding v2 (its rows and hops pinned)",
            retrieval=_v2(CONFIGS["C2"].retrieval),
        ),
        "C2-gf": EvalConfig(
            name="C2-gf",
            description="hybrid RRF + graph-first (fuse)",
            retrieval=_gf(CONFIGS["C2"].retrieval),
        ),
        "C2-u2-gf": EvalConfig(
            name="C2-u2-gf",
            description="hybrid RRF + understanding v2 + graph-first (fuse)",
            retrieval=_gf(_v2(CONFIGS["C2"].retrieval)),
        ),
    }
)
