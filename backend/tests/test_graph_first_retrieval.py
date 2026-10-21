"""Graph-first search on the answer path (PO 2026-10-20): the relations of the question pick a
candidate region in Neo4j, the embeddings on those nodes rank it, and the ranking joins RRF
(`fuse`) or replaces the corpus-wide candidates (`filter`). Needs the working database and its
Neo4j projection with vectors; skipped otherwise."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.retrieval.retriever import RetrievalService, Retriever, SearchRequest

QUESTION = (
    "Nhà máy xả 1.500 m³/ngày nước thải ra nguồn tiếp nhận thuộc cột B, BOD5 tối đa là bao nhiêu"
    " theo QCVN 40:2025?"
)


@pytest.fixture(scope="module")
def env():
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.session import get_sessionmaker

    settings = get_settings()
    try:
        session = get_sessionmaker(settings)()
        svc = RetrievalService(settings)
        svc.ensure(session)
    except SQLAlchemyError as e:
        pytest.skip(f"no database: {type(e).__name__}")
    reason = svc.graph.status(force=True).usable_for(svc.index_label, need_vectors=True)
    if reason:
        pytest.skip(f"graph not usable: {reason}")
    return settings, session, svc


def _search(env, **kw):
    settings, session, svc = env
    return Retriever(session, svc).search(SearchRequest(question=QUESTION, k=8, **kw))


def test_off_by_default(env):
    res = _search(env)
    assert res.graph_first is None and "graph_first" not in res.latency_ms


def test_fuse_keeps_the_pinned_row_first_and_reports_the_region(env):
    base = _search(env)
    res = _search(env, graph_first="fuse")
    gf = res.graph_first
    assert gf is not None and gf["error"] is None and not gf["fallback"]
    assert gf["seeds"] > 0 and gf["candidates"] > 0 and gf["scored"] > 0
    assert res.evidence[0].chunk_id == base.evidence[0].chunk_id  # structured row still first
    assert any("graph-first" in e.sources for e in res.evidence)
    assert res.latency_ms["graph_first"] >= 0


def test_filter_keeps_only_the_region_and_the_pins(env):
    res = _search(env, graph_first="filter", return_ranking=True)
    gf = res.graph_first
    assert gf is not None and gf["error"] is None and not gf["fallback"]
    for c in res.ranking:
        assert "graph-first" in c.sources or "structured" in c.sources, c
    assert gf["dropped"] >= 0


def test_graph_first_with_understanding_v2(env):
    res = _search(env, graph_first="fuse", understanding_engine="v2")
    assert res.understanding_v2 is not None and res.graph_first is not None
    assert res.graph_first["error"] is None
    assert [h.rule_code for h in res.structured_hits] == res.understanding_v2["rule_codes"]
