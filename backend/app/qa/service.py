"""QA pipeline (PLAN.md §8): understand → retrieve → (policy short-circuits) → LLM answer with
structured output → validate (regenerate once) → audit log. Emits stage events for SSE.

Deterministic paths never call the LLM: frequency questions (policy M-01, D10), out-of-scope
questions and empty evidence packs answer from templates with the pointer chunks as citations.
Every answer (and every failure, via `log_error`) is written to `query_logs` with its client id,
token usage and list-price cost, and rolled up into `usage_daily` (M7).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.pricing import cost_of_usage
from app.db import models as m
from app.llm.client import LLMClient, LLMResult, get_llm
from app.ops import usage as usage_mod
from app.ops.state import STATE
from app.qa import graph_verify
from app.qa import prompts as P
from app.qa.models import (
    Answer,
    Citation,
    EvalOptions,
    LLMUnderstanding,
    QARequest,
    QAResponse,
    ValidationResult,
    understanding_schema,
)
from app.qa.validator import validate
from app.retrieval.query import QueryUnderstanding
from app.retrieval.retriever import RetrievalService, Retriever, SearchRequest, SearchResult

EventCb = Callable[[str, dict[str, Any]], None]
logger = logging.getLogger(__name__)


def _noop(_: str, __: dict[str, Any]) -> None:
    return None


class QAService:
    def __init__(
        self,
        session: Session,
        retrieval: RetrievalService,
        llm: LLMClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.s = session
        self.settings = settings or get_settings()
        self.retriever = Retriever(session, retrieval)
        self._llm = llm

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = get_llm(self.settings)
        return self._llm

    def _call_llm(self, *args: Any, **kwargs: Any) -> LLMResult:
        r = self.llm.generate_json(*args, **kwargs)
        STATE.record_llm(r.error)
        return r

    # ------------------------------------------------------------------ public

    def answer(
        self,
        req: QARequest,
        on_event: EventCb = _noop,
        *,
        query_id: str | None = None,
        client_id: str | None = None,
        client_key: str | None = None,
        options: EvalOptions | None = None,
    ) -> QAResponse:
        t: dict[str, int] = {}
        t0 = time.perf_counter()
        query_id = query_id or str(uuid.uuid4())
        on_event("stage", {"stage": "understanding", "query_id": query_id})

        # 1. retrieval (rule-based understanding inside)
        res = self.retriever.search(self._search_request(req, options))
        qu = res.understanding
        llm_u: LLMUnderstanding | None = None
        usage_total: dict[str, float] = {}
        model_used: str | None = None
        level_used: str | None = None

        # 1b. LLM understanding only when rules are unsure (domain unknown/cross or intent other)
        if qu.needs_llm and qu.policy is None and (options is None or options.understanding_llm):
            r = self._call_llm(
                P.UNDERSTANDING_SYSTEM,
                P.understanding_prompt(req.question),
                understanding_schema(self.retriever.svc.corpus.domain_codes()),
                model=self.settings.gemini_understanding_model,
                thinking_level=None,
            )
            _add_usage(usage_total, r)
            if r.parsed is not None:
                llm_u = r.parsed
                changed = False
                if qu.domain in ("unknown", "cross_domain") and llm_u.domain != "cross_domain":
                    qu.domain = llm_u.domain  # type: ignore[assignment]
                    changed = True
                if qu.intent == "other" and llm_u.intent != "other":
                    qu.intent = llm_u.intent  # type: ignore[assignment]
                    changed = True
                if changed and self.retriever.svc.corpus.is_regulated(qu.domain):
                    res = self.retriever.search(
                        self._search_request(req, options, domain=qu.domain)
                    )
                    res.understanding.intent = qu.intent
                    qu = res.understanding
        t["retrieval"] = int((time.perf_counter() - t0) * 1000)
        on_event("understanding", {"understanding": qu.model_dump(mode="json")})
        on_event(
            "evidence",
            {
                "evidence": [e.model_dump(mode="json") for e in res.evidence],
                "structured_hits": [h.model_dump(mode="json") for h in res.structured_hits],
                "warnings": res.warnings,
            },
        )

        # 2. deterministic short-circuits
        policy = self._policy_answer(qu, res, llm_u)
        attempts = 0
        validation: ValidationResult
        if policy is not None:
            ans = policy
            validation, ans = validate(ans, res.evidence, qu, req.question)
            validation = self._graph_check(validation, ans, res, qu, req, options)
            path = "policy"
        else:
            on_event("stage", {"stage": "generating", "model": self.settings.gemini_answer_model})
            ans, validation, attempts, results = self._generate(req, qu, res, on_event, options)
            observe = options is not None and options.validator_mode == "observe"
            path = "llm" if (validation.ok or observe) else "fallback"
            model_used, level_used = results[-1].model, results[-1].thinking_level
            for r in results:  # every attempt is billed, not only the last one
                _add_usage(usage_total, r)
        t["total"] = int((time.perf_counter() - t0) * 1000)
        on_event("stage", {"stage": "validating", "ok": validation.ok, "errors": validation.errors})

        if path == "fallback":
            status = "failed"  # validator rejected both attempts → standard abstention, audited as failure
        else:
            status = "abstained" if ans.abstained else ("answered" if validation.ok else "failed")
        graph = dict(validation.graph or {})
        evidence_paths = graph.pop("paths", [])
        validation.graph = graph or None
        by_id = {e.chunk_id: e for e in res.evidence}
        citations = [
            Citation(
                chunk_id=c,
                citation_label=by_id[c].citation_label,
                document_code=by_id[c].document_code,
                text_status=by_id[c].text_status,
            )
            for c in ans.all_citations()
            if c in by_id
        ]
        resp = QAResponse(
            query_id=query_id,
            question=req.question,
            as_of_date=req.as_of_date,
            understanding=qu,
            llm_understanding=llm_u,
            understanding_v2=res.understanding_v2,
            structured_hits=res.structured_hits,
            evidence=res.evidence,
            answer=ans,
            citations=citations,
            validation=validation,
            evidence_paths=evidence_paths,
            status=status,
            path=path,
            attempts=attempts,
            model=model_used,
            thinking_level=level_used,
            prompt_version=P.PROMPT_VERSION,
            usage={k: (int(v) if k != "cost_usd" else v) for k, v in usage_total.items()},
            cost_usd=round(float(usage_total.get("cost_usd", 0.0)), 6),
            latency_ms={**res.latency_ms, **t},
            disclaimer=P.DISCLAIMER,
            ranking=res.ranking if (options is not None and options.return_ranking) else None,
        )
        if options is None or options.audit:
            self._log(resp, client_id=client_id, client_key=client_key)
        logger.info(
            "qa.answer",
            extra={
                "query_id": query_id,
                "status": status,
                "path": path,
                "domain": qu.domain,
                "intent": qu.intent,
                "attempts": attempts,
                "latency_ms": t["total"],
                "cost_usd": resp.cost_usd,
                "client_key": client_key,
            },
        )
        on_event("answer", resp.model_dump(mode="json"))
        return resp

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _search_request(
        req: QARequest, options: EvalOptions | None, domain: str | None = None
    ) -> SearchRequest:
        base = {"question": req.question, "as_of_date": req.as_of_date, "k": req.k}
        if domain:
            base["domain"] = domain
        if options is not None:
            base["return_ranking"] = options.return_ranking
            if options.retrieval is not None:
                ro = options.retrieval.model_dump()
                k = ro.pop("k", None)
                base.update(ro)
                if k:
                    base["k"] = k
        return SearchRequest(**base)

    def _policy_answer(
        self, qu: QueryUnderstanding, res: SearchResult, llm_u: LLMUnderstanding | None
    ) -> Answer | None:
        if qu.domain == "out_of_scope":
            hint = P.HINT_OUT_OF_SCOPE
            return Answer(
                conclusion=P.ABSTAIN_OUT_OF_SCOPE.format(hint=hint),
                abstained=True,
                abstain_reason="Ngoài phạm vi corpus (nước thải / khí thải công nghiệp)",
            )
        if qu.policy == "M-01":
            cites = [
                e.chunk_id
                for e in res.evidence
                if "Điều 16, Khoản 3" in e.citation_label or "Điều 17, Khoản 5" in e.citation_label
            ]
            return Answer(
                conclusion=P.ABSTAIN_FREQUENCY,
                citations=cites,
                missing_information=[P.MISSING_FREQUENCY],
                warnings=[P.WARN_M01],
                abstained=True,
                abstain_reason=P.REASON_FREQUENCY,
            )
        if llm_u is not None and llm_u.needs_clarification and llm_u.clarification_question:
            return Answer(
                conclusion=P.ABSTAIN_CLARIFY.format(question=llm_u.clarification_question),
                abstained=True,
                abstain_reason="Cần làm rõ câu hỏi",
                missing_information=[llm_u.clarification_question],
            )
        if not res.evidence:
            return Answer(
                conclusion=P.ABSTAIN_NO_EVIDENCE, abstained=True, abstain_reason="Không có evidence"
            )
        return None

    def _generate(
        self,
        req: QARequest,
        qu: QueryUnderstanding,
        res: SearchResult,
        on_event: EventCb,
        options: EvalOptions | None = None,
    ) -> tuple[Answer, ValidationResult, int, list[LLMResult]]:
        evidence = P.evidence_block(res.evidence)
        llm_kwargs: dict[str, Any] = {}
        if options is not None and options.model:
            llm_kwargs["model"] = options.model
        errors: list[str] = []
        results: list[LLMResult] = []
        ans: Answer | None = None
        validation = ValidationResult(ok=False, errors=["no attempt"])
        for attempt in (1, 2):
            level = self.settings.gemini_answer_thinking
            medium = qu.domain == "cross_domain" or qu.intent in ("applicability", "effectivity")
            if medium:
                level = "medium"
            if (
                options is not None
                and options.thinking_level
                and (options.thinking_override_all or not medium)
            ):
                level = options.thinking_level
            as_of = str(req.as_of_date) if req.as_of_date else None
            last = self._call_llm(
                P.ANSWER_SYSTEM,
                P.answer_prompt(req.question, as_of, evidence, errors or None),
                Answer,
                thinking_level=level,
                on_delta=lambda piece: on_event("delta", {"text": piece}),
                **llm_kwargs,
            )
            results.append(last)
            if last.parsed is None:
                errors = [last.error or "LLM không trả về JSON hợp lệ"]
                validation = ValidationResult(ok=False, errors=errors)
                continue
            ans = last.parsed
            validation, ans = validate(ans, res.evidence, qu, req.question)
            validation = self._graph_check(validation, ans, res, qu, req, options)
            if options is not None and options.validator_mode == "observe":
                # evaluation only: record the verdict, never reject or regenerate
                validation = ValidationResult(
                    ok=validation.ok,
                    errors=validation.errors,
                    warnings=validation.warnings,
                    rules_checked=validation.rules_checked,
                    enforced=False,
                    graph=validation.graph,
                )
                ans.warnings.append("EVAL-ONLY: validator not enforced")
                return ans, validation, attempt, results
            if validation.ok:
                return ans, validation, attempt, results
            errors = validation.errors
            on_event("stage", {"stage": "regenerating", "errors": errors})
        # both attempts rejected → standard abstention (rule 7) keeping the evidence for the user
        fallback = Answer(
            conclusion=P.ABSTAIN_VALIDATION,
            citations=[],
            warnings=["Validator từ chối câu trả lời của mô hình: " + "; ".join(errors)],
            abstained=True,
            abstain_reason="Không tạo được câu trả lời có trích dẫn hợp lệ",
        )
        v, fallback = validate(fallback, res.evidence, qu, req.question)
        result = ValidationResult(
            ok=False, errors=errors, warnings=v.warnings, rules_checked=v.rules_checked
        )
        return fallback, result, 2, results

    def _graph_check(
        self,
        validation: ValidationResult,
        ans: Answer,
        res: SearchResult,
        qu: QueryUnderstanding,
        req: QARequest,
        options: EvalOptions | None,
    ) -> ValidationResult:
        """Rule 8 (plan 2026-09-29 §3): recorded on the validation result; rejects only in
        `enforce` mode (evaluation configs — production observes, PO decision D31)."""
        mode = (options.graph_verify if options is not None else None) or (
            self.settings.graph_verify_mode
        )
        if mode not in ("off", "observe", "enforce"):
            mode = "observe"
        svc = self.retriever.svc
        gc = graph_verify.check(
            ans, res.evidence, qu, req.question, svc.graph, mode=mode, active_label=svc.index_label
        )
        if mode != "off":
            validation.rules_checked.append("8-graph-grounded")
        validation.graph = gc.model_dump(mode="json")
        if mode == "enforce" and not gc.ok:
            validation.ok = False
            validation.errors += [f"đồ thị: {e}" for e in gc.errors]
        return validation

    def _log(
        self, resp: QAResponse, *, client_id: str | None = None, client_key: str | None = None
    ) -> None:
        try:
            log = m.QueryLog(
                id=resp.query_id,
                question=resp.question,
                as_of_date=resp.as_of_date,
                domain=resp.understanding.domain,
                intent=resp.understanding.intent,
                understanding=_understanding_for_log(resp),
                retrieved=[
                    {"chunk_id": e.chunk_id, "rank": e.rank, "sources": e.sources, "score": e.score}
                    for e in resp.evidence
                ],
                structured_hits=[h.rule_code for h in resp.structured_hits],
                status=resp.status,
                path=resp.path,
                prompt_version=resp.prompt_version,
                model_id=resp.model,
                thinking_level=resp.thinking_level,
                usage=resp.usage,
                latency_ms=resp.latency_ms,
                validation=_validation_for_log(resp),
                attempts=resp.attempts,
                client_id=client_id,
                cost_usd=resp.cost_usd,
            )
            self.s.add(log)
            self.s.flush()  # no ORM relationships → flush parents before children
            ans = m.AnswerRecord(
                id=str(uuid.uuid4()),
                query_id=resp.query_id,
                answer=resp.answer.model_dump(mode="json"),
                abstained=resp.answer.abstained,
            )
            self.s.add(ans)
            self.s.flush()
            for c in resp.citations:
                self.s.add(
                    m.AnswerCitation(
                        answer_id=ans.id, chunk_id=c.chunk_id, citation_label=c.citation_label
                    )
                )
            u = resp.usage
            usage_mod.add_usage(
                self.s,
                client_key or "unknown",
                questions=1,
                llm_calls=int(u.get("llm_calls", 0)),
                prompt_tokens=int(u.get("prompt_tokens", 0)),
                output_tokens=int(u.get("output_tokens", 0)),
                thinking_tokens=int(u.get("thinking_tokens", 0)),
                cost_usd=resp.cost_usd,
                abstained=1 if resp.answer.abstained else 0,
            )
            self.s.commit()
        except Exception:  # pragma: no cover - audit must never break the answer
            logger.exception("audit log failed for query %s", resp.query_id)
            self.s.rollback()

    @staticmethod
    def log_error(
        maker: sessionmaker[Session],
        query_id: str,
        req: QARequest,
        client_id: str | None,
        client_key: str | None,
        exc: BaseException,
    ) -> None:
        """Audit a failed request (status=error) in a fresh session; never raises."""
        try:
            with maker() as session:
                session.add(
                    m.QueryLog(
                        id=query_id,
                        question=req.question,
                        as_of_date=req.as_of_date,
                        domain="unknown",
                        intent="other",
                        understanding={},
                        retrieved=[],
                        structured_hits=[],
                        status="error",
                        path="error",
                        prompt_version=P.PROMPT_VERSION,
                        usage={},
                        latency_ms={},
                        validation={},
                        attempts=0,
                        client_id=client_id,
                        error=f"{type(exc).__name__}: {exc}"[:2000],
                    )
                )
                usage_mod.add_usage(session, client_key or "unknown", questions=1, errors=1)
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("error audit failed for query %s", query_id)


def _understanding_for_log(resp: QAResponse) -> dict[str, Any]:
    """The v1 understanding as before, plus the v2 frame under 'v2' (engine v2 or shadow)."""
    out = resp.understanding.model_dump(mode="json")
    if resp.understanding_v2 is not None:
        out["v2"] = resp.understanding_v2
    return out


def _validation_for_log(resp: QAResponse) -> dict[str, Any]:
    """Validation as audited: rule 8's evidence paths are kept under `graph.paths`, so the
    admin log view and reopened chat history can show them (the response carries them apart)."""
    v = resp.validation.model_dump(mode="json")
    if resp.evidence_paths:
        v["graph"] = {**(v.get("graph") or {}), "paths": resp.evidence_paths}
    return v


def _add_usage(total: dict[str, float], r: LLMResult) -> None:
    for k, v in (r.usage or {}).items():
        if v:
            total[k] = total.get(k, 0) + int(v)
    total["llm_calls"] = total.get("llm_calls", 0) + 1
    total["llm_latency_ms"] = total.get("llm_latency_ms", 0) + int(r.latency_s * 1000)
    total["cost_usd"] = round(total.get("cost_usd", 0.0) + cost_of_usage(r.model, r.usage), 8)


def today() -> dt.date:
    return dt.date.today()
