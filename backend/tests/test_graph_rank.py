"""PPR retrieval pieces that need no Neo4j: seeds, fusion, head rerank, rewiring, fallbacks."""

from collections import Counter

from app.core.config import Settings
from app.graph.gds import shuffle_edges
from app.graph.runtime import GraphRuntime
from app.retrieval.graph_rank import (
    GraphRanker,
    PPRParams,
    build_seeds,
    entity_nodes,
    fuse,
    rerank_head,
)
from app.retrieval.query import QueryUnderstanding


def _qu(**kw) -> QueryUnderstanding:
    base = {"question": "q", "domain": "emission", "intent": "threshold"}
    return QueryUnderstanding(**(base | kw))


def test_entity_nodes_use_projection_ids():
    qu = _qu(parameters=["SO2"], zone="B", fuel_type="solid", document_ids=["TT_45_2024"])
    assert entity_nodes(qu) == ["param:SO2", "zone:QCVN_19_2024:B", "fuel:solid", "doc:TT_45_2024"]
    cross = _qu(domain="cross_domain", zone="A", receiving_body="river", sector_id="s12")
    assert entity_nodes(cross) == [
        "zone:QCVN_40_2025:A",
        "zone:QCVN_19_2024:A",
        "rbt:river",
        "sector:s12",
    ]


def test_seed_mass_split():
    ranked = [("chunk:a", 3.0), ("chunk:b", 1.0), ("chunk:c", 0.5)]
    p = PPRParams(entity_mass=0.5, seed_chunks=2)
    seeds = build_seeds(_qu(parameters=["CO"], zone="B"), ranked, p)
    assert abs(sum(seeds.values()) - 1.0) < 1e-9
    assert seeds["param:CO"] == seeds["zone:QCVN_19_2024:B"] == 0.25
    assert abs(seeds["chunk:a"] - 0.375) < 1e-9 and abs(seeds["chunk:b"] - 0.125) < 1e-9
    assert "chunk:c" not in seeds
    # no entities → all mass on chunks; entity-only → all mass on entities
    only_chunks = build_seeds(_qu(), ranked, p)
    assert abs(sum(only_chunks.values()) - 1.0) < 1e-9
    only_ents = build_seeds(_qu(parameters=["CO"]), ranked, PPRParams(seeds="entity"))
    assert only_ents == {"param:CO": 1.0}
    assert build_seeds(_qu(), [], p) == {}


def test_fuse_adds_candidates_through_the_filter():
    ranked = [("chunk:a", 0.05), ("chunk:b", 0.04)]
    ppr = [("chunk:x", 0.9), ("chunk:other_domain", 0.8), ("chunk:b", 0.7)]
    boosts = {"chunk:x": 1.5, "chunk:b": 1.0, "chunk:a": 1.0}
    out, added = fuse(ranked, ppr, rrf_k=60, weight=1.0, admit=boosts.get)
    scores = dict(out)
    assert added == {"chunk:x"} and "chunk:other_domain" not in scores
    assert abs(scores["chunk:x"] - 1.5 / 61) < 1e-12  # rank 1, boosted
    assert abs(scores["chunk:b"] - (0.04 + 1.0 / 62)) < 1e-12  # rank 2 (filtered one skipped)
    assert [c for c, _ in out][0] == "chunk:b"


def test_rerank_head_only_reorders_the_head():
    ranked = [("a", 1.0), ("b", 0.9), ("c", 0.8), ("tail", 0.1)]
    ppr = [("c", 1.0), ("tail", 1.0)]
    out = rerank_head(ranked, ppr, top_n=3, alpha=0.5)
    assert [c for c, _ in out] == ["c", "a", "b", "tail"]
    assert dict(out) == dict(ranked)  # scores untouched, nothing added


def test_shuffle_keeps_degrees_and_weights():
    edges = [("n1", "n2", 1.0), ("n1", "n3", 0.2), ("n2", "n3", 1.0), ("n3", "n4", 1.0)]
    out = shuffle_edges(edges, seed=1)
    deg = Counter(x for a, b, _ in edges for x in (a, b))
    assert Counter(x for a, b, _ in out for x in (a, b)) == deg
    assert sorted(w for *_, w in out) == sorted(w for *_, w in edges)
    assert out == shuffle_edges(edges, seed=1) and out != edges


def test_seed_only_needs_no_graph():
    rt = GraphRuntime(Settings(_env_file=None, graph_backend="off"))
    res = GraphRanker(rt).rank(
        _qu(parameters=["CO"]), [("chunk:a", 2.0), ("chunk:b", 1.0)], PPRParams(seed_only=True), "x"
    )
    assert res.error is None and [c for c, _ in res.scores] == ["chunk:a", "chunk:b"]


def test_graph_unavailable_is_reported_not_raised():
    rt = GraphRuntime(Settings(_env_file=None, graph_backend="off"))
    res = GraphRanker(rt).rank(_qu(parameters=["CO"]), [("chunk:a", 1.0)], PPRParams(), "x")
    assert res.scores == [] and "disabled" in (res.error or "")
