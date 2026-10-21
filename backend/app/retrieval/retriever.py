"""Hybrid retriever (PLAN.md §6): understand → structured fast path → BM25 ∪ vector → RRF →
graph expansion → effectivity filter → evidence pack (≤ ~6k tokens) with per-stage latency.

`RetrievalService` holds the process-wide state (BM25 index, alias index, embedder); a `Retriever`
binds it to a database session for one request.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db import models as m
from app.evaluation.cache import JsonCache
from app.graph.runtime import GraphRuntime
from app.llm.client import GeminiClient, LLMClient
from app.ontology import corpus as corpus_mod
from app.ontology.loader import load_dictionaries
from app.retrieval import graph_candidates as gc
from app.retrieval.embedder import Embedder, get_embedder
from app.retrieval.expand import expand
from app.retrieval.graph_rank import GraphRanker, PPRParams, fuse, rerank_head
from app.retrieval.lexical import BM25Index
from app.retrieval.query import CROSS_DOMAIN, UNKNOWN, Intent, QueryUnderstanding, understand
from app.retrieval.rerank import LLMReranker, RerankCandidate
from app.retrieval.structured import StructuredHit, hits_for_codes, lookup
from app.retrieval.tokenize import AliasIndex, tokenize
from app.retrieval.vector import Neo4jVectorIndex, VectorIndex

RRF_K = 60
CHAR_BUDGET = 18000  # ≈ 6k tokens of Vietnamese
TYPE_BOOST: dict[str, dict[str, float]] = {
    "threshold": {"table_row": 1.3, "footnote": 1.1},
    "method": {"table_row": 1.2, "clause": 1.1},
    "effectivity": {"clause": 1.2, "registry_fact": 1.4, "preamble": 1.2, "point": 1.1},
    "applicability": {"clause": 1.2, "point": 1.2},
    "definition": {"definition": 1.5},
    "sampling": {"clause": 1.2, "paragraph": 1.1},
    "frequency": {"clause": 1.2},
}


class SearchRequest(BaseModel):
    question: str
    as_of_date: dt.date | None = None
    k: int = Field(default=8, ge=1, le=30)
    domain: str | None = Field(
        default=None, description="override routing with a regulated domain code (domains.yaml)"
    )
    use_vector: bool = True
    use_bm25: bool = True
    expand: bool = True
    # where the vector leg runs: None → settings.vector_backend (pgvector unless configured)
    vector_backend: Literal["pgvector", "neo4j"] | None = None
    # evaluation-only switches (defaults = production behaviour)
    use_structured: bool = True
    type_boost: bool = True
    domain_filter: bool = True
    understanding_engine: Literal["v1", "v2"] | None = None  # None → settings
    graph_first: Literal["off", "fuse", "filter"] | None = None  # None → settings
    effectivity_filter: bool = True
    use_expansions: bool = True
    rrf_k: int = Field(default=60, ge=1)
    candidates: int = Field(default=50, ge=10, le=200)
    # R0': only the first `structured_cap` structured rows (most specific first) get the +1.0
    structured_cap: int | None = Field(default=None, ge=0, le=8)
    # R3: Gemini grades the top `rerank_top_n` fused candidates (off in production, D17)
    rerank: Literal["llm"] | None = None
    rerank_model: str | None = None
    rerank_top_n: int = Field(default=30, ge=5, le=50)
    # E3 (plan 2026-09-29): Personalized PageRank over the Neo4j projection (off in production, D33)
    ppr: Literal["fuse", "rerank"] | None = None
    ppr_variant: Literal["full", "struct", "shuffle"] = "full"
    ppr_seeds: Literal["both", "entity", "chunk"] = "both"
    ppr_seed_only: bool = False  # control: seed weights only, no graph walk
    ppr_exclude_seeds: bool = True  # fuse: seed chunks are not re-counted in the PPR list
    ppr_damping: float | None = Field(default=None, ge=0.0, lt=1.0)
    ppr_entity_mass: float | None = Field(default=None, ge=0.0, le=1.0)
    ppr_weight: float | None = Field(default=None, ge=0.0)
    ppr_seed_chunks: int | None = Field(default=None, ge=0, le=50)
    return_ranking: bool = False


class RankedCandidate(BaseModel):
    """One fused+boosted candidate before graph expansion (ranker stage, evaluation only)."""

    chunk_id: str
    score: float
    sources: list[str] = Field(default_factory=list)
    chunk_type: str | None = None
    rerank_grade: int | None = None


# query understanding v2 on the answer path (D74): at most three options are listed (D38),
# more are asked about; v2 intents that v1 does not know map onto its vocabulary
V2_LIST_MAX = 3
V1_INTENTS = frozenset(get_args(Intent))
V2_TO_V1_INTENT = {"compliance_check": "threshold", "management": "other", "comparison": "other"}


class EvidenceItem(BaseModel):
    rank: int
    chunk_id: str
    citation_label: str
    document_id: str
    document_code: str
    chunk_type: str
    domain: str
    text_status: str
    score: float
    sources: list[str]
    text: str


class SearchResult(BaseModel):
    understanding: QueryUnderstanding
    structured_hits: list[StructuredHit] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    candidates_considered: int = 0
    warnings: list[str] = Field(default_factory=list)
    latency_ms: dict[str, int] = Field(default_factory=dict)
    index_label: str | None = None
    ranking: list[RankedCandidate] = Field(default_factory=list)
    rerank: dict[str, Any] | None = None  # model, cached, error, candidates, token usage
    ppr: dict[str, Any] | None = None  # mode, variant, seeds, added, gds_ms, error
    understanding_v2: dict[str, Any] | None = None  # frame, verdict, pins (v2 or shadow)
    graph_first: dict[str, Any] | None = None  # mode, seeds, candidates, added, dropped


class RetrievalService:
    """Process-wide, lazily built: BM25 cache, alias index, embedder, active index label."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.bm25: BM25Index | None = None
        self.aliases: AliasIndex | None = None
        self.doc_domains: dict[str, str] = {}
        self.corpus: corpus_mod.Corpus = corpus_mod.current()
        self._embedder: Embedder | None = None
        self.index_label: str | None = None
        self.cache_dir_override: Path | None = None
        self.rerank_llm_override: LLMClient | None = None  # tests / FakeLLM
        self.rerank_cache: JsonCache | None = None  # evaluation runs attach one
        self._reranker: LLMReranker | None = None
        self.graph = GraphRuntime(self.settings)  # optional Neo4j projection (PPR, rule 8)
        self._assist: Any = None  # query understanding v2 (FrameResolver + LLMAssist)
        self.assist_llm_override: LLMClient | None = None  # tests / FakeLLM
        self._gf_view: Any = None  # graph-first view of the projection (seeds, docs, conditions)

    @property
    def cache_dir(self) -> Path:
        return self.cache_dir_override or (self.settings.data_dir / "derived" / "index")

    def ensure(self, session: Session, force: bool = False) -> None:
        if force:
            self._gf_view = None
        if self.aliases is None or force:
            d = load_dictionaries(settings=self.settings)
            all_docs = list(session.execute(select(m.Document)).scalars())
            docs = {x.document_id: x.document_code for x in all_docs}
            self.aliases = AliasIndex(d, docs)
            self.corpus = corpus_mod.set_current(corpus_mod.Corpus.load(session, self.settings, d))
            # domain of corpus documents, inherited by the documents they replace / repeal
            dom = {x.document_id: x.domain for x in all_docs if self.corpus.is_regulated(x.domain)}
            for r in session.execute(
                select(m.DocumentRelation).where(
                    m.DocumentRelation.type.in_(["REPLACES", "REPEALS"])
                )
            ).scalars():
                if r.target_document_id and r.source_document_id in dom:
                    dom.setdefault(r.target_document_id, dom[r.source_document_id])
            self.doc_domains = dom
        if self.bm25 is None or force:
            self.bm25 = BM25Index.load_or_build(session, self.cache_dir)
        active = session.execute(
            select(m.IndexVersion.label).where(m.IndexVersion.status == "active")
        ).scalar_one_or_none()
        self.index_label = active

    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = get_embedder(self.settings)
        return self._embedder

    def graph_view(self, session: Session) -> Any:
        """In-memory view of the projection for graph-first lookups (seeds, documents in
        scope, flow / fuel conditions): read once per process, `ensure(force=True)` reloads."""
        if self._gf_view is None:
            ids = set(session.execute(select(m.EvidenceChunk.chunk_id)).scalars())
            self._gf_view = gc.load_view(self.graph, ids)
        return self._gf_view

    def v2(self, session: Session) -> Any:
        """Query understanding v2 (plan G3): rule frame → candidate rows and hops, two
        constrained LLM calls when the rules are unsure. Built once per process; the hop
        tables are read with the session of the current request."""
        if self._assist is None:
            from app.llm.client import get_llm
            from app.retrieval.frame import FrameBuilder
            from app.retrieval.frame_llm import LLMAssist
            from app.retrieval.hops import FrameResolver

            assert self.aliases is not None
            d = load_dictionaries(settings=self.settings)
            builder = FrameBuilder(d, self.aliases, self.corpus, self.doc_domains)
            resolver = FrameResolver(session, d, builder, self.corpus)
            self._assist = LLMAssist(
                resolver,
                self.assist_llm_override or get_llm(self.settings),
                repair_model=self.settings.gemini_understanding_model,
                choice_model=self.settings.gemini_answer_model,
                choice_thinking=self.settings.gemini_answer_thinking,
            )
        self._assist.resolver.s = session
        return self._assist

    def reranker(self) -> LLMReranker:
        if self._reranker is None:
            llm = self.rerank_llm_override or GeminiClient(
                self.settings.model_copy(
                    update={"gemini_timeout_s": self.settings.rerank_timeout_s}
                ),
                max_retries=0,  # a slow reranker must not hold the answer: fall back to RRF
            )
            self._reranker = LLMReranker(
                llm,
                self.settings.reranker_model,
                max_chars=self.settings.rerank_max_chars,
                cache=self.rerank_cache,
            )
        return self._reranker

    def reload(self, session: Session) -> dict[str, Any]:
        self.ensure(session, force=True)
        assert self.bm25 is not None
        graph = self.graph.status(force=True)
        return {
            "bm25": self.bm25.stats(),
            "index_label": self.index_label,
            "graph": graph.usable_for(self.index_label) or "ok",
        }


class Retriever:
    def __init__(self, session: Session, service: RetrievalService) -> None:
        self.s = session
        self.svc = service
        self.svc.ensure(session)
        self._codes: dict[str, str] | None = None

    # ------------------------------------------------------------------ helpers

    def _code(self, doc_id: str) -> str:
        if self._codes is None:
            self._codes = {
                d.document_id: d.document_code for d in self.s.execute(select(m.Document)).scalars()
            }
        return self._codes.get(doc_id, doc_id)

    def _effective_dates(self) -> dict[str, dt.date]:
        out: dict[str, dt.date] = {}
        for f in self.s.execute(
            select(m.RegistryFact).where(
                m.RegistryFact.fact_type == "effective_date", m.RegistryFact.scope.is_(None)
            )
        ).scalars():
            try:
                out[f.document_id] = dt.date.fromisoformat(f.value)
            except ValueError:
                continue
        return out

    def _domains_for(self, qu: QueryUnderstanding, override: str | None) -> set[str] | None:
        # None = cross-domain / unknown / out of scope: search everything
        return self.svc.corpus.search_domains(override or qu.domain)

    # ------------------------------------------------------------------ main

    def search(self, req: SearchRequest) -> SearchResult:
        t: dict[str, int] = {}
        t0 = time.perf_counter()
        assert self.svc.aliases is not None and self.svc.bm25 is not None
        qu = understand(req.question, self.svc.aliases, req.as_of_date, self.svc.doc_domains)
        if self.svc.corpus.is_regulated(req.domain):
            qu.domain = req.domain  # type: ignore[assignment]
        t["understand"] = int((time.perf_counter() - t0) * 1000)
        warnings: list[str] = []
        # query understanding v2 (D74): decides the pinned evidence with engine v2, or is
        # only reported (shadow); either way the v1 ranking below stays as it is
        v2: dict[str, Any] | None = None
        v2_hits: list[StructuredHit] | None = None
        v2_pins: list[tuple[str, str]] = []
        engine = req.understanding_engine or self.svc.settings.understanding_engine
        if engine == "v2" or self.svc.settings.understanding_v2_shadow:
            tv = time.perf_counter()
            v2, v2_hits, v2_pins = self._understand_v2(req, qu, apply=engine == "v2")
            t["understanding_v2"] = int((time.perf_counter() - tv) * 1000)
            if v2.get("warning"):
                warnings.append(v2["warning"])
        domains = self._domains_for(qu, req.domain) if req.domain_filter else None

        # structured fast path
        t1 = time.perf_counter()
        hits: list[StructuredHit] = []
        if (
            qu.intent in ("threshold", "applicability", "other")
            and qu.parameters
            and req.use_structured
        ):
            hits, w = lookup(self.s, qu)
            warnings += w
        t["structured"] = int((time.perf_counter() - t1) * 1000)
        if v2_hits is not None:  # v2 settled on the row(s): they replace the v1 lookup
            hits = v2_hits

        # lexical + vector candidates
        fused: dict[str, float] = {}
        sources: dict[str, set[str]] = {}
        t2 = time.perf_counter()
        if req.use_bm25:
            qtoks = tokenize(req.question) + (qu.expansions if req.use_expansions else [])
            for rank, (cid, _) in enumerate(
                self.svc.bm25.search(qtoks, k=req.candidates, domains=domains), start=1
            ):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (req.rrf_k + rank)
                sources.setdefault(cid, set()).add("bm25")
        t["bm25"] = int((time.perf_counter() - t2) * 1000)
        t3 = time.perf_counter()
        vi_used: Any = None  # the index that embedded the question (reused by graph-first)
        if req.use_vector:
            try:
                backend = req.vector_backend or self.svc.settings.vector_backend
                vi: VectorIndex | Neo4jVectorIndex
                if backend == "neo4j":
                    vi = Neo4jVectorIndex(self.svc.graph, self.svc.embedder(), self.svc.index_label)
                else:
                    vi = VectorIndex(self.s, self.svc.embedder())
                vi_used = vi
                for rank, (cid, _) in enumerate(
                    vi.search(req.question, k=req.candidates, domains=domains), start=1
                ):
                    fused[cid] = fused.get(cid, 0.0) + 1.0 / (req.rrf_k + rank)
                    sources.setdefault(cid, set()).add("vector")
            except Exception as e:  # noqa: BLE001 - embedding provider unavailable → lexical only
                # only our own graph-usability reason is shown; provider errors stay anonymous
                why = f": {e}" if isinstance(e, RuntimeError) else ""
                warnings.append(f"vector search unavailable: {type(e).__name__}{why}"[:200])
        t["vector"] = int((time.perf_counter() - t3) * 1000)
        for cid, why in v2_pins:  # second bases the hop catalogue resolved (G3 step 3.4)
            fused[cid] = fused.get(cid, 0.0) + 1.0
            sources.setdefault(cid, set()).add(f"v2-hop:{why}")
        boosted = hits if req.structured_cap is None else hits[: req.structured_cap]
        for h in boosted:  # structured rows first (lookup sorts them most specific first)
            fused[h.chunk_id] = fused.get(h.chunk_id, 0.0) + 1.0
            sources.setdefault(h.chunk_id, set()).add("structured")

        # intent-aware boost, effectivity filter, ranking
        t4 = time.perf_counter()
        metas = {cid: self.svc.bm25.meta(cid) for cid in fused}
        boosts = TYPE_BOOST.get(qu.intent, {}) if req.type_boost else {}
        eff = self._effective_dates() if req.as_of_date else {}

        def admit(cid: str) -> float | None:
            """Type boost of a candidate, None when a filter drops it. The domain filter is applied
            only to candidates PPR adds: BM25/vector ran with it, structured hits never had it."""
            mt = metas.get(cid) or self.svc.bm25.meta(cid)  # type: ignore[union-attr]
            if mt is None or (cid not in fused and domains and mt.domain not in domains):
                return None
            metas[cid] = mt
            if (
                req.as_of_date
                and req.effectivity_filter
                and qu.intent not in ("effectivity", "applicability")
            ):
                d = eff.get(mt.document_id)
                if d and req.as_of_date < d:
                    return None  # document not yet in force at as_of_date (PLAN §8 rule 5)
            return boosts.get(mt.chunk_type, 1.0)

        ranked: list[tuple[str, float]] = []
        for cid, score in fused.items():
            b = admit(cid)
            if b is not None:
                ranked.append((cid, score * b))
        ranked.sort(key=lambda x: -x[1])
        gf_info: dict[str, Any] | None = None
        gf_mode = req.graph_first or self.svc.settings.graph_first_mode
        if gf_mode in ("fuse", "filter"):
            t7 = time.perf_counter()
            ranked, gf_info = self._graph_first(req, qu, ranked, admit, sources, gf_mode, vi_used)
            if gf_info.get("error"):
                warnings.append(f"graph-first unavailable: {gf_info['error'][:100]}")
            t["graph_first"] = int((time.perf_counter() - t7) * 1000)
        ppr_info: dict[str, Any] | None = None
        if req.ppr:
            t6 = time.perf_counter()
            ranked, ppr_info = self._ppr(req, qu, ranked, admit, sources)
            if ppr_info.get("error"):
                warnings.append(f"ppr unavailable: {ppr_info['error'][:100]}")
            t["ppr"] = int((time.perf_counter() - t6) * 1000)
        grades: dict[str, int] = {}
        rerank_info: dict[str, Any] | None = None
        if req.rerank == "llm" and len(ranked) > 1:
            t5 = time.perf_counter()
            ranked, grades, rerank_info = self._rerank(req, ranked)
            if rerank_info.get("error"):
                warnings.append(f"rerank unavailable: {rerank_info['error'][:80]}")
            else:
                for cid in grades:
                    sources.setdefault(cid, set()).add("rerank")
            t["rerank"] = int((time.perf_counter() - t5) * 1000)
        ranking: list[RankedCandidate] = []
        if req.return_ranking:
            for cid, sc in ranked[:50]:
                mt = metas.get(cid)
                ranking.append(
                    RankedCandidate(
                        chunk_id=cid,
                        score=sc,
                        sources=sorted(sources.get(cid, ())),
                        chunk_type=mt.chunk_type if mt else None,
                        rerank_grade=grades.get(cid),
                    )
                )
        top = [cid for cid, _ in ranked[: req.k]]
        if req.expand:
            for cid, why in expand(
                self.s, top, qu, max_add=max(2, req.k // 2), corpus=self.svc.corpus
            ):
                top.append(cid)
                sources.setdefault(cid, set()).add(f"expansion:{why}")
        t["rank_expand"] = int((time.perf_counter() - t4) * 1000)

        # evidence pack within budget
        chunks = {
            c.chunk_id: c
            for c in self.s.execute(
                select(m.EvidenceChunk).where(m.EvidenceChunk.chunk_id.in_(top))
            ).scalars()
        }
        score_of = dict(ranked)
        evidence: list[EvidenceItem] = []
        used = 0
        for i, cid in enumerate(top, start=1):
            c = chunks.get(cid)
            if c is None:
                continue
            if used + len(c.text) > CHAR_BUDGET and evidence:
                warnings.append("evidence pack cắt bớt theo ngân sách token")
                break
            used += len(c.text)
            evidence.append(
                EvidenceItem(
                    rank=i,
                    chunk_id=cid,
                    citation_label=c.citation_label,
                    document_id=c.document_id,
                    document_code=self._code(c.document_id),
                    chunk_type=c.chunk_type,
                    domain=c.domain,
                    text_status=c.text_status,
                    score=round(score_of.get(cid, 0.0), 5),
                    sources=sorted(sources.get(cid, set())),
                    text=c.text,
                )
            )
        drafts = sorted({e.document_code for e in evidence if e.text_status == "draft"})
        if drafts:
            warnings.append("Căn cứ có văn bản dự thảo: " + ", ".join(drafts))
        if qu.domain == "out_of_scope":
            warnings.append("Câu hỏi ngoài phạm vi nước thải / khí thải công nghiệp (MVP)")
        if qu.policy == "M-01":
            warnings.append(
                "Tần suất quan trắc: hệ thống không có căn cứ (D10) — trả lời theo chính sách M-01"
            )
        if self.svc.index_label is None:
            warnings.append("Chưa có index_version ở trạng thái active (UC-08)")
        t["total"] = int((time.perf_counter() - t0) * 1000)
        return SearchResult(
            understanding=qu,
            structured_hits=hits,
            evidence=evidence,
            candidates_considered=len(fused),
            warnings=warnings,
            latency_ms=t,
            index_label=self.svc.index_label,
            ranking=ranking,
            rerank=rerank_info,
            ppr=ppr_info,
            understanding_v2=v2,
            graph_first=gf_info,
        )

    def _understand_v2(
        self, req: SearchRequest, qu: QueryUnderstanding, *, apply: bool
    ) -> tuple[dict[str, Any], list[StructuredHit] | None, list[tuple[str, str]]]:
        """Run query understanding v2 on the question. With `apply` its verdict pins the
        evidence: the rows it settled on (one answer, or a list of at most V2_LIST_MAX) replace
        the v1 structured lookup, the clauses its hops resolved are pinned like structured rows,
        and a domain or main intent the v1 rules could not read is filled in. Without `apply`
        (shadow) nothing changes and the frame is only reported. Any failure leaves v1 alone."""
        payload: dict[str, Any] = {"engine": "v2", "applied": {}}
        try:
            frame, info = self.svc.v2(self.s).understand(req.question)
        except Exception as e:  # noqa: BLE001 - v2 must never break the v1 path
            payload["error"] = f"{type(e).__name__}: {e}"[:200]
            return payload, None, []
        clar = frame.clarification or {}
        mode = clar.get("mode")
        payload.update(
            {
                "domain": frame.domain,
                "intents": frame.intents,
                "slots": [
                    {
                        "dimension": s.dimension,
                        "value": s.value,
                        "negated": s.negated,
                        "source": s.source,
                    }
                    for s in frame.slots
                ],
                "sub_questions": [sq.domain for sq in frame.sub_questions],
                "hops": [
                    {"type": h.type, "status": h.status, "chunk_ids": list(h.chunk_ids)}
                    for h in frame.hops
                ],
                "missing": list(frame.missing),
                "decision": mode,
                "n_candidates": clar.get("n_candidates", 0),
                "rule_codes": list(clar.get("rule_codes", [])),
                "unsure": list(frame.unsure),
                "source": frame.source,
                "llm_calls": info.get("calls", []),
                "llm_latency_s": round(info.get("latency_s", 0.0), 3),
                "cost_usd": round(info.get("cost_usd", 0.0), 6),
            }
        )
        if mode == "ask" and frame.missing:
            payload["warning"] = "Hiểu câu hỏi v2: còn thiếu dữ kiện " + ", ".join(frame.missing)
        if not apply:
            return payload, None, []
        hits: list[StructuredHit] | None = None
        if mode == "answer" or (mode == "list" and 0 < clar.get("n_candidates", 0) <= V2_LIST_MAX):
            codes = list(clar.get("rule_codes", []))
            if codes:
                hits = hits_for_codes(self.s, codes)
                payload["applied"]["structured"] = codes
        pins: list[tuple[str, str]] = []
        seen: set[str] = set()
        for h in frame.hops:
            if h.status != "resolved":
                continue
            for cid in h.chunk_ids:
                if cid not in seen:
                    seen.add(cid)
                    pins.append((cid, h.type))
        if pins:
            payload["applied"]["hops"] = [c for c, _ in pins]
        if qu.domain in (UNKNOWN, CROSS_DOMAIN) and self.svc.corpus.is_regulated(frame.domain):
            qu.domain = frame.domain  # type: ignore[assignment]
            payload["applied"]["domain"] = frame.domain
        if qu.intent == "other" and frame.intents:
            mapped = V2_TO_V1_INTENT.get(frame.intents[0], frame.intents[0])
            if mapped in V1_INTENTS and mapped != "other":
                qu.intent = mapped  # type: ignore[assignment]
                payload["applied"]["intent"] = mapped
        return payload, hits, pins

    def _graph_first(
        self,
        req: SearchRequest,
        qu: QueryUnderstanding,
        ranked: list[tuple[str, float]],
        admit: Any,
        sources: dict[str, set[str]],
        mode: str,
        vi: Any,
    ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        """Graph-first search (plan "quan hệ trước, vector sau"): the relations of the question's
        entities pick a candidate region inside Neo4j and the embeddings stored on those nodes rank
        it (`graph_candidates.neo4j_search`). `fuse` adds that ranking to RRF; `filter` also drops
        every candidate outside the region — pinned rows (structured, v2 hops) always stay and an
        empty region falls back to the whole corpus. Any graph problem leaves the ranking as it is
        with the reason in `error`."""
        info: dict[str, Any] = {
            "mode": mode,
            "seeds": 0,
            "candidates": 0,
            "scored": 0,
            "added": 0,
            "dropped": 0,
            "fallback": False,
            "error": None,
        }
        try:
            rt = self.svc.graph
            reason = rt.status().usable_for(self.svc.index_label, need_vectors=True)
            if reason:
                info["error"] = reason
                return ranked, info
            view = self.svc.graph_view(self.s)
            params = gc.lookup_params(view, qu)
            info["seeds"] = len(params["seeds"])
            if not (params["seeds"] or params["docs"] or params["topics"]):
                info["fallback"] = True
                return ranked, info
            if vi is not None and hasattr(vi, "embed"):
                vec = vi.embed(req.question)
            else:
                vec = self.svc.embedder().embed_query(req.question)
            found = gc.neo4j_search(rt, view, qu, vec)
        except Exception as e:  # noqa: BLE001 - graph-first must never break the hybrid path
            info["error"] = f"{type(e).__name__}: {e}"[:200]
            return ranked, info
        cand = {r["id"] for r in found}
        info["candidates"] = len(cand)
        if not cand:
            info["fallback"] = True
            return ranked, info
        gf = [(r["id"], float(r["score"])) for r in found if r["score"] is not None]
        info["scored"] = len(gf)
        if mode == "filter":
            pinned = {
                cid
                for cid, s in sources.items()
                if "structured" in s or any(x.startswith("v2-hop:") for x in s)
            }
            kept = [(c, s) for c, s in ranked if c in cand or c in pinned]
            info["dropped"] = len(ranked) - len(kept)
            ranked = kept
        out, added = fuse(
            ranked, gf, rrf_k=req.rrf_k, weight=self.svc.settings.graph_first_weight, admit=admit
        )
        info["added"] = len(added)
        for cid, _ in gf:
            sources.setdefault(cid, set()).add("graph-first")
        return out, info

    def _ppr(
        self,
        req: SearchRequest,
        qu: QueryUnderstanding,
        ranked: list[tuple[str, float]],
        admit: Any,
        sources: dict[str, set[str]],
    ) -> tuple[list[tuple[str, float]], dict[str, Any]]:
        """Personalized PageRank from the query entities + top chunks (plan 2026-09-29 §2.5).
        On any graph problem the ranking is returned unchanged with the reason in `error`."""
        assert req.ppr is not None
        p = PPRParams.from_settings(
            self.svc.settings,
            mode=req.ppr,
            variant=req.ppr_variant,
            seeds=req.ppr_seeds,
            seed_only=req.ppr_seed_only,
            exclude_seeds=req.ppr_exclude_seeds,
            damping=req.ppr_damping,
            entity_mass=req.ppr_entity_mass,
            weight=req.ppr_weight,
            seed_chunks=req.ppr_seed_chunks,
        )
        res = GraphRanker(self.svc.graph).rank(qu, ranked, p, self.svc.index_label)
        info = dict(res.info) | {"error": res.error, "added": 0}
        if res.error or not res.scores:
            return ranked, info
        if p.mode == "fuse":
            out, added = fuse(ranked, res.scores, rrf_k=req.rrf_k, weight=p.weight, admit=admit)
            info["added"] = len(added)
        else:
            out = rerank_head(ranked, res.scores, top_n=p.rerank_top_n, alpha=p.alpha)
        for cid, _ in res.scores[: req.candidates]:
            sources.setdefault(cid, set()).add("ppr")
        return out, info

    def _rerank(
        self, req: SearchRequest, ranked: list[tuple[str, float]]
    ) -> tuple[list[tuple[str, float]], dict[str, int], dict[str, Any]]:
        """Reorder the head (top `rerank_top_n`) by LLM grade; the tail and the scores stay as
        fused, so a failed call is exactly the RRF ranking."""
        head, tail = ranked[: req.rerank_top_n], ranked[req.rerank_top_n :]
        ids = [cid for cid, _ in head]
        rows = {
            c.chunk_id: c
            for c in self.s.execute(
                select(m.EvidenceChunk).where(m.EvidenceChunk.chunk_id.in_(ids))
            ).scalars()
        }
        cands = [
            RerankCandidate(
                chunk_id=cid,
                citation_label=rows[cid].citation_label,
                document_code=self._code(rows[cid].document_id),
                text_status=rows[cid].text_status,
                context_text=rows[cid].context_text,
            )
            for cid in ids
            if cid in rows
        ]
        out = self.svc.reranker().rerank(req.question, cands, model=req.rerank_model)
        score_of = dict(head)
        info = {
            "model": out.model,
            "candidates": len(cands),
            "cached": out.cached,
            "error": out.error,
            "latency_ms": out.latency_ms,
            **{k: v for k, v in out.usage.items() if v is not None},
        }
        if out.error:
            return ranked, {}, info
        order = out.order + [cid for cid in ids if cid not in rows]  # no chunk row: keep at end
        return [(cid, score_of[cid]) for cid in order] + tail, out.grades, info
