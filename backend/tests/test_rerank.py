"""LLM reranker (research plan 2026-09-28): grade-based reordering, fail-safe, cache. Offline —
the LLM is a FakeLLM; the retrieval integration lives in test_retrieval.py."""

import re

from app.evaluation.cache import JsonCache
from app.llm.client import FakeLLM, LLMResult
from app.retrieval.rerank import (
    LLMReranker,
    RerankCandidate,
    RerankOutput,
    apply_grades,
    rerank_prompt,
)


def cands(n: int) -> list[RerankCandidate]:
    return [
        RerankCandidate(
            chunk_id=f"c{i}",
            citation_label=f"Bảng 1 dòng {i}",
            document_code="QCVN 19:2024/BTNMT",
            text_status="draft" if i == 2 else "official",
            context_text="x" * 1000,
        )
        for i in range(n)
    ]


def test_apply_grades_is_stable_and_ignores_unknown():
    ids = ["a", "b", "c", "d"]
    assert apply_grades(ids, {"c": 2, "a": 0, "zz": 2}) == ["c", "b", "d", "a"]
    assert apply_grades(ids, {}) == ids  # all neutral → RRF order


def test_prompt_marks_drafts_and_clips():
    p = rerank_prompt("CO của lò hơi?", cands(3), max_chars=50)
    assert p.count("<c id=") == 3 and "[BẢN DỰ THẢO]" in p and "x" * 51 not in p


def test_rerank_reorders_candidates_only():
    llm = FakeLLM(
        [
            {
                "grades": [
                    {"id": "c3", "grade": 2},
                    {"id": "c0", "grade": 0},
                    {"id": "bogus", "grade": 2},
                ]
            }
        ]
    )
    out = LLMReranker(llm, "m").rerank("q", cands(4))
    assert out.error is None and out.order == ["c3", "c1", "c2", "c0"]
    assert sorted(out.order) == ["c0", "c1", "c2", "c3"] and "bogus" not in out.grades
    assert llm.calls[0]["schema"] == "RerankOutput"


class Failing:
    def generate_json(self, *a, **k) -> LLMResult:
        return LLMResult(text="", parsed=None, model="m", thinking_level=None, error="503: down")


def test_rerank_failure_keeps_rrf_order():
    out = LLMReranker(Failing(), "m").rerank("q", cands(4))
    assert out.error == "503: down" and out.order == ["c0", "c1", "c2", "c3"]


def test_rerank_cache(tmp_path):
    cache = JsonCache(tmp_path)
    grade_last = lambda s, p: {  # noqa: E731
        "grades": [{"id": i, "grade": 2 if i == "c2" else 1} for i in re.findall(r'id="(c\d)"', p)]
    }
    llm = FakeLLM([grade_last])
    rr = LLMReranker(llm, "m", cache=cache)
    first = rr.rerank("q", cands(3))
    second = rr.rerank("q", cands(3))
    assert first.order == second.order == ["c2", "c0", "c1"]
    assert len(llm.calls) == 1 and second.cached and not first.cached
    assert RerankOutput.model_validate({"grades": []}).grades == []


def test_single_candidate_skips_llm():
    llm = FakeLLM([{"grades": []}])
    assert LLMReranker(llm, "m").rerank("q", cands(1)).order == ["c0"] and not llm.calls
