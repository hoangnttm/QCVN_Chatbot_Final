"""Query understanding v2 on the answer path (plan G3 / D74, 2026-10-20).

Runs against the working database named in backend/.env (skipped when its fact store is empty):
with engine `v2` the rows the frame settles on replace the v1 structured lookup and the clauses
its hops resolve are pinned; with the default engine, or in shadow mode, the v1 ranking is
untouched. The LLM is a stub that never answers, so only the rule path is exercised."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.llm.client import LLMResult
from app.retrieval.retriever import RetrievalService, Retriever, SearchRequest

SINGLE_ROW = (
    "Nhà máy xả 1.500 m³/ngày nước thải ra nguồn tiếp nhận thuộc cột B, BOD5 tối đa là bao nhiêu"
    " theo QCVN 40:2025?"
)
UNZONED = (
    "Cơ sở chưa xác định được phân vùng xả thải nước thải, lưu lượng 1.500 m³/ngày, BOD5 tối đa"
    " bao nhiêu theo QCVN 40:2025?"
)


class _MuteLLM:
    """A model that never returns anything usable: the v2 rule path must stand on its own."""

    def generate_json(self, system, prompt, schema, **kw):  # noqa: ANN001
        return LLMResult(text="", parsed=None, model="mute", thinking_level=None, error="mute")


@pytest.fixture(scope="module")
def env():
    from sqlalchemy import func, select
    from sqlalchemy.exc import SQLAlchemyError

    from app.db import models as m
    from app.db.session import get_sessionmaker

    settings = get_settings()
    try:
        session = get_sessionmaker(settings)()
        if not session.execute(select(func.count()).select_from(m.RuleCondition)).scalar():
            pytest.skip("fact store is empty")
    except SQLAlchemyError as e:
        pytest.skip(f"no database: {type(e).__name__}")
    svc = RetrievalService(settings)
    svc.assist_llm_override = _MuteLLM()
    svc.ensure(session)
    return settings, session, svc


def _search(env, question: str, **kw):
    settings, session, svc = env
    return Retriever(session, svc).search(
        SearchRequest(question=question, k=8, use_vector=False, **kw)
    )


def test_default_engine_reports_nothing_and_matches_v1(env):
    res = _search(env, SINGLE_ROW)
    assert res.understanding_v2 is None
    assert "understanding_v2" not in res.latency_ms
    again = _search(env, SINGLE_ROW, understanding_engine="v1")
    assert [e.chunk_id for e in again.evidence] == [e.chunk_id for e in res.evidence]


def test_v2_settles_on_one_row_and_pins_it(env):
    v1 = _search(env, SINGLE_ROW)
    res = _search(env, SINGLE_ROW, understanding_engine="v2")
    v2 = res.understanding_v2
    assert v2 is not None and v2.get("error") is None
    assert v2["decision"] == "answer" and len(v2["rule_codes"]) == 1
    assert v2["applied"]["structured"] == v2["rule_codes"]
    assert [h.rule_code for h in res.structured_hits] == v2["rule_codes"]
    hit = res.structured_hits[0]
    assert hit.parameter_id == "BOD5" and hit.zone == "B"
    assert res.evidence[0].chunk_id == hit.chunk_id
    assert "structured" in res.evidence[0].sources
    assert "understanding_v2" in res.latency_ms
    # the v1 lookup for the same question already pointed at this row: v2 narrows, never widens
    assert hit.rule_code in {h.rule_code for h in v1.structured_hits}


def test_v2_pins_the_clause_a_hop_resolves(env):
    res = _search(env, UNZONED, understanding_engine="v2")
    v2 = res.understanding_v2
    assert v2 is not None and v2.get("error") is None
    resolved = [h for h in v2["hops"] if h["status"] == "resolved"]
    assert resolved, v2["hops"]
    pinned = set(v2["applied"].get("hops", []))
    assert pinned
    in_pack = [e for e in res.evidence if e.chunk_id in pinned]
    assert in_pack and any(s.startswith("v2-hop:") for e in in_pack for s in e.sources)


def test_shadow_reports_the_frame_without_changing_the_ranking(env, monkeypatch):
    settings, session, svc = env
    base = _search(env, SINGLE_ROW)
    monkeypatch.setattr(svc.settings, "understanding_v2_shadow", True)
    try:
        res = _search(env, SINGLE_ROW)
    finally:
        monkeypatch.setattr(svc.settings, "understanding_v2_shadow", False)
    assert res.understanding_v2 is not None and res.understanding_v2["applied"] == {}
    assert [e.chunk_id for e in res.evidence] == [e.chunk_id for e in base.evidence]
    assert [h.rule_code for h in res.structured_hits] == [h.rule_code for h in base.structured_hits]
