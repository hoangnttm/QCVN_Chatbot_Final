"""Neo4j Graph Data Science: in-memory graphs for Personalized PageRank (plan 2026-09-29 §2).

Three variants of the same projection, named after the projection version so a rebuild never
serves a stale in-memory graph:

- `full`    — every relationship that carries retrieval meaning (RELS), undirected, weighted
- `struct`  — document structure only (control C11-struct)
- `shuffle` — `full` with endpoints rewired at random, every node keeping its degree
              (configuration model, fixed seed; control C11-shuffle)

Relationship weights are fixed a priori (not tuned on the gold set): hub-like edges that would
spread mass over whole tables get a low weight; document-wide fan-outs are left out entirely.
"""

from __future__ import annotations

import random
import threading
from typing import Any, Literal

from app.graph.runtime import GraphRuntime

Variant = Literal["full", "struct", "shuffle"]

# relationship type -> weight (types not listed are not projected)
RELS: dict[str, float] = {
    "CONTAINS": 1.0,
    "EXTRACTED_FROM": 1.0,
    # rule / fact / dictionary entity / applicability rule -> chunk (role on the edge); the
    # document-wide LegalDocument edges are left out by the projection queries
    "HAS_EVIDENCE": 1.0,
    "FOR_PARAMETER": 1.0,
    "QUALIFIED_BY": 1.0,
    "DEFINED_BY_TABLE_ROW": 1.0,
    "APPLIES_WHEN": 1.0,
    "EXCEPT_WHEN": 1.0,
    "APPLIES_TO": 1.0,
    "HAS_DEFINITION": 1.0,
    "PARENT_OF": 1.0,
    "HAS_TRANSITION_RULE": 1.0,
    "HAS_REGISTRY_FACT": 1.0,
    "HAS_MONITORING_REQUIREMENT": 1.0,
    "REPLACES": 1.0,
    "REPEALS": 1.0,
    "PROMULGATES": 1.0,
    "PROMULGATED_BY": 1.0,
    "SUPPLEMENTS": 1.0,
    "REFERENCES": 1.0,
    "USES_FUEL": 1.0,
    "HAS_CHARACTERISTIC_PARAMETER": 1.0,
    "IN_ZONE": 0.2,  # a Zone node joins 71–304 limit rules
}
STRUCT_RELS = ("CONTAINS", "EXTRACTED_FROM", "QUALIFIED_BY")
# left out: HAS_METHOD / SPECIFIES_METHOD / HAS_UNIT / IN_UNIT (shared by hundreds of rows),
# HAS_LIMIT_RULE and
# LegalDocument-HAS_EVIDENCE (one document to all its rules/chunks), HAS_VERSION, GOVERNS_DOMAIN
SHUFFLE_SEED = 20260929

_EDGE_QUERY = """
MATCH (a)-[r]->(b)
WHERE type(r) IN $types AND NOT (a:LegalDocument AND type(r) = 'HAS_EVIDENCE')
RETURN elementId(a) AS a, elementId(b) AS b, type(r) AS t
"""

_PROJECT_FROM_MATCH = """
MATCH (a)-[r]->(b)
WHERE type(r) IN $types AND NOT (a:LegalDocument AND type(r) = 'HAS_EVIDENCE')
WITH a, b, coalesce($weights[type(r)], 1.0) AS w
WITH gds.graph.project($name, a, b, {relationshipProperties: {w: w}},
                       {undirectedRelationshipTypes: ['*']}) AS g
RETURN g.nodeCount AS nodes, g.relationshipCount AS rels
"""

_PROJECT_FROM_LIST = """
UNWIND $edges AS e
MATCH (a) WHERE elementId(a) = e[0]
MATCH (b) WHERE elementId(b) = e[1]
WITH gds.graph.project($name, a, b, {relationshipProperties: {w: e[2]}},
                       {undirectedRelationshipTypes: ['*']}) AS g
RETURN g.nodeCount AS nodes, g.relationshipCount AS rels
"""

_PPR = """
UNWIND $seeds AS s
MATCH (n {id: s[0]})
WITH collect([n, s[1]]) AS src
CALL gds.pageRank.stream($name, {
  sourceNodes: src, relationshipWeightProperty: 'w',
  dampingFactor: $damping, maxIterations: $iterations, tolerance: 1e-7
})
YIELD nodeId, score
WITH nodeId, score ORDER BY score DESC LIMIT $scan
WITH gds.util.asNode(nodeId) AS n, score WHERE n:EvidenceChunk
RETURN n.id AS id, score
"""

_MEMBERS_QUERY = """
MATCH (a)-[r]->(b)
WHERE type(r) IN $types AND NOT (a:LegalDocument AND type(r) = 'HAS_EVIDENCE')
UNWIND [a.id, b.id] AS id
RETURN collect(DISTINCT id) AS ids
"""

_lock = threading.Lock()
_members: dict[str, set[str]] = {}  # graph name -> node ids it contains (seeds must be members)


def graph_name(projection_version: str, variant: Variant) -> str:
    return f"qcvn_ppr_{variant}_{projection_version}"


def shuffle_edges(
    edges: list[tuple[str, str, float]], seed: int = SHUFFLE_SEED
) -> list[tuple[str, str, float]]:
    """Configuration-model rewiring of an undirected edge list: the multiset of endpoints (and
    so every node's degree) and the multiset of weights are kept, the pairing is random."""
    stubs = [x for a, b, _ in edges for x in (a, b)]
    rng = random.Random(seed)
    rng.shuffle(stubs)
    weights = [w for _, _, w in edges]
    rng.shuffle(weights)
    return [(stubs[2 * i], stubs[2 * i + 1], weights[i]) for i in range(len(edges))]


def ensure_graph(rt: GraphRuntime, projection_version: str, variant: Variant) -> str:
    """Create the in-memory graph once per projection version (idempotent, thread-safe)."""
    name = graph_name(projection_version, variant)
    types = list(STRUCT_RELS) if variant == "struct" else list(RELS)
    with _lock:
        if name not in _members:  # the rewired graph keeps the node set of `full`
            _members[name] = set(rt.run(_MEMBERS_QUERY, timeout=60, types=types)[0]["ids"])
        if rt.run("CALL gds.graph.exists($name) YIELD exists", name=name)[0]["exists"]:
            return name
        if variant == "shuffle":
            rows = rt.run(_EDGE_QUERY, timeout=60, types=types)
            edges = shuffle_edges([(r["a"], r["b"], RELS.get(r["t"], 1.0)) for r in rows])
            rt.run(_PROJECT_FROM_LIST, timeout=120, name=name, edges=[list(e) for e in edges])
        else:
            rt.run(_PROJECT_FROM_MATCH, timeout=60, name=name, types=types, weights=RELS)
    return name


def personalized_pagerank(
    rt: GraphRuntime,
    name: str,
    seeds: dict[str, float],
    *,
    damping: float,
    iterations: int,
    scan: int = 3000,
    timeout: float | None = None,
) -> list[tuple[str, float]]:
    """[(chunk_id, score)] best first; `seeds` maps node `id` properties to seed weights. Seeds
    that are not nodes of the projected graph (no projected relationship) are dropped."""
    members = _members.get(name)
    if members is not None:
        seeds = {k: v for k, v in seeds.items() if k in members}
    if not seeds:
        return []
    rows = rt.run(
        _PPR,
        timeout=timeout,
        name=name,
        seeds=[[k, float(v)] for k, v in seeds.items()],
        damping=damping,
        iterations=iterations,
        scan=scan,
    )
    return [(r["id"], float(r["score"])) for r in rows]


def graph_info(rt: GraphRuntime) -> list[dict[str, Any]]:
    return rt.run(
        "CALL gds.graph.list() YIELD graphName, nodeCount, relationshipCount "
        "WHERE graphName STARTS WITH 'qcvn_' RETURN graphName, nodeCount, relationshipCount"
    )
