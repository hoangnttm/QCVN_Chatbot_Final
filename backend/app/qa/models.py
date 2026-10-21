"""QA I/O models (PLAN.md §8): LLM understanding + answer schemas, validator result, API response."""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from app.retrieval.query import QueryUnderstanding
from app.retrieval.retriever import EvidenceItem, RankedCandidate, StructuredHit


class LLMEntities(BaseModel):
    qcvn_codes: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(
        default_factory=list, description="tên thông số như trong câu hỏi"
    )
    zone: str | None = None
    equipment: str | None = None
    sector: str | None = None
    flow: str | None = None
    as_of_date: str | None = None


class LLMUnderstanding(BaseModel):
    domain: str  # a regulated domain code, "cross_domain" or "out_of_scope"
    intent: Literal[
        "threshold",
        "method",
        "scope",
        "definition",
        "effectivity",
        "applicability",
        "sampling",
        "frequency",
        "other",
    ]
    entities: LLMEntities = Field(default_factory=LLMEntities)
    needs_clarification: bool = False
    clarification_question: str | None = None


@lru_cache
def _understanding_schema(domains: tuple[str, ...]) -> type[LLMUnderstanding]:
    values = (*domains, "cross_domain", "out_of_scope")
    return create_model(  # type: ignore[call-overload]
        "LLMUnderstanding", __base__=LLMUnderstanding, domain=(Literal[values], ...)
    )


def understanding_schema(domains: list[str]) -> type[LLMUnderstanding]:
    """The classification schema sent to the LLM: `domain` is an enum of the regulated domains of
    the corpus (domains.yaml) plus the two routing outcomes, so the model cannot invent one."""
    return _understanding_schema(tuple(domains))


class Basis(BaseModel):
    text: str
    citations: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    conclusion: str
    basis: list[Basis] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    abstained: bool = False
    abstain_reason: str | None = None

    def all_citations(self) -> list[str]:
        out: list[str] = []
        for c in self.citations + [c for b in self.basis for c in b.citations]:
            if c not in out:
                out.append(c)
        return out


class ValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rules_checked: list[str] = Field(default_factory=list)
    enforced: bool = Field(
        default=True, description="False only in evaluation runs with validator_mode=observe"
    )
    graph: dict[str, Any] | None = Field(
        default=None, description="rule 8 (graph-grounded citations): mode, ok, errors, warnings"
    )


class Citation(BaseModel):
    chunk_id: str
    citation_label: str
    document_code: str
    text_status: str


class QARequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    as_of_date: dt.date | None = None
    k: int = Field(default=10, ge=3, le=20)


class RetrievalOptions(BaseModel):
    """Ablation switches mirrored onto `SearchRequest` (evaluation only; defaults = production)."""

    use_bm25: bool = True
    use_vector: bool = True
    use_structured: bool = True
    type_boost: bool = True
    expand: bool = True
    understanding_engine: Literal["v1", "v2"] | None = None  # None → settings
    graph_first: Literal["off", "fuse", "filter"] | None = None  # None → settings
    domain_filter: bool = True
    effectivity_filter: bool = True
    use_expansions: bool = True
    rrf_k: int = Field(default=60, ge=1)
    candidates: int = Field(default=50, ge=10, le=200)
    k: int | None = Field(default=None, ge=3, le=30)
    structured_cap: int | None = Field(default=None, ge=0, le=8)
    rerank: Literal["llm"] | None = None
    rerank_model: str | None = None
    rerank_top_n: int = Field(default=30, ge=5, le=50)
    vector_backend: Literal["pgvector", "neo4j"] | None = None
    ppr: Literal["fuse", "rerank"] | None = None
    ppr_variant: Literal["full", "struct", "shuffle"] = "full"
    ppr_seeds: Literal["both", "entity", "chunk"] = "both"
    ppr_seed_only: bool = False
    ppr_exclude_seeds: bool = True
    ppr_damping: float | None = Field(default=None, ge=0.0, lt=1.0)
    ppr_entity_mass: float | None = Field(default=None, ge=0.0, le=1.0)
    ppr_weight: float | None = Field(default=None, ge=0.0)
    ppr_seed_chunks: int | None = Field(default=None, ge=0, le=50)


class EvalOptions(BaseModel):
    """Evaluation-only knobs passed to `QAService.answer`. Never exposed through the API: a client
    must not be able to switch the validator off or skip the audit log."""

    retrieval: RetrievalOptions | None = None
    model: str | None = None
    thinking_level: Literal["low", "medium", "high"] | None = None
    thinking_override_all: bool = False
    validator_mode: Literal["enforce", "observe"] = "enforce"
    graph_verify: Literal["off", "observe", "enforce"] | None = None  # None → settings
    understanding_llm: bool = True
    audit: bool = True
    return_ranking: bool = False


class QAResponse(BaseModel):
    query_id: str
    question: str
    as_of_date: dt.date | None
    understanding: QueryUnderstanding
    llm_understanding: LLMUnderstanding | None = None
    understanding_v2: dict[str, Any] | None = Field(
        default=None, description="query understanding v2: frame, verdict, what it pinned"
    )
    structured_hits: list[StructuredHit] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    answer: Answer
    citations: list[Citation] = Field(default_factory=list)
    validation: ValidationResult
    evidence_paths: list[dict[str, Any]] = Field(
        default_factory=list, description="rule 8: graph path behind each citation"
    )
    status: Literal["answered", "abstained", "failed"]
    path: Literal["policy", "llm", "fallback"]
    attempts: int = 0
    model: str | None = None
    thinking_level: str | None = None
    prompt_version: str
    usage: dict[str, Any] = Field(default_factory=dict)
    cost_usd: float = 0.0
    latency_ms: dict[str, int] = Field(default_factory=dict)
    disclaimer: str
    ranking: list[RankedCandidate] | None = Field(
        default=None, description="ranker-stage candidates (evaluation runs only)"
    )
