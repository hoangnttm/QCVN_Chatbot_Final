"""Candidate-set coverage (plan graph-first, E0): the set logic and scores, without a graph server."""

from collections import defaultdict

from app.evaluation import coverage as cov


def _adj(edges):
    adj = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    return adj


# param:COD ─ rule1 ─ chunk:row1        zone:ww:B ─ rule1
#           └ rule2 ─ chunk:row2        zone:ww:B ─ def ─ chunk:defB
ADJ = _adj(
    [
        ("param:COD", "rule1"),
        ("param:COD", "rule2"),
        ("rule1", "chunk:row1"),
        ("rule2", "chunk:row2"),
        ("zone:ww:B", "rule1"),
        ("zone:ww:B", "def"),
        ("def", "chunk:defB"),
    ]
)
CHUNKS = {"chunk:row1", "chunk:row2", "chunk:defB", "chunk:far"}


def test_neighbourhood_respects_the_hop_limit():
    assert cov.neighbourhood(ADJ, "param:COD", 1) == {"param:COD", "rule1", "rule2"}
    assert "chunk:row1" in cov.neighbourhood(ADJ, "param:COD", 2)
    assert "chunk:defB" not in cov.neighbourhood(ADJ, "param:COD", 2)
    assert cov.neighbourhood(ADJ, "unknown", 3) == {"unknown"}


def test_or_is_a_union_and_and_intersects_entity_types():
    sets = cov.candidate_sets(ADJ, CHUNKS, ["param:COD", "zone:ww:B"], 2)
    assert sets["or"] == {"chunk:row1", "chunk:row2", "chunk:defB"}
    assert sets["and"] == {"chunk:row1"}  # the only chunk both the parameter and the zone reach
    # two entities of one type are alternatives, not a conjunction
    same_type = cov.candidate_sets(ADJ, CHUNKS, ["zone:ww:B", "zone:em:B"], 2)
    assert same_type["and"] == same_type["or"] == {"chunk:row1", "chunk:defB"}
    assert cov.candidate_sets(ADJ, CHUNKS, ["param:nowhere"], 2) == {"or": set(), "and": set()}


def test_scores_filter_prioritise_and_fallback():
    ranking = ["chunk:far", "chunk:row2", "chunk:row1", "chunk:defB"]
    must = ["chunk:row1", "chunk:defB"]
    s = cov.score_item(ranking, must, {"chunk:row1", "chunk:row2"})
    assert s["coverage"] == 0.5 and s["missed"] == ["chunk:defB"] and s["size"] == 2
    assert s["filter"] == 0.5  # defB is cut off by the filter …
    assert s["prioritise"] == 1.0  # … but only moved back when the set is a priority
    empty = cov.score_item(ranking, must, set())
    assert empty["fallback"] and empty["filter"] == empty["prioritise"] == 1.0
    assert cov.recall(ranking, must, k=2) == 0.0


def test_summary_counts_fallbacks_and_missed_types():
    def row(tier, variant):
        return {
            "tier": tier,
            "baseline": 1.0,
            "degraded": False,
            "entities_in_graph": ["param:COD"],
            "variants": {"or2": variant},
        }

    hit = cov.score_item(["chunk:row1"], ["chunk:row1"], {"chunk:row1"})
    miss = cov.score_item(["chunk:defB"], ["chunk:defB"], {"chunk:row1"})
    fall = cov.score_item(["chunk:row1"], ["chunk:row1"], set())
    summ = cov.summarize([row("A", hit), row("B", miss), row("B", fall)], {"chunk:defB": "point"})
    v = summ["all"]["variants"]["or2"]
    assert v["fallback"] == 1 and v["median_size"] == 1 and v["coverage"] == 0.5
    assert round(v["filter"], 3) == 0.667 and summ["tiers"]["B"]["n"] == 2
    assert summ["missed_types"]["or2"] == {"point": 1} and summ["degraded"] == 0
