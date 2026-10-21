"""Graph-first candidate set: which evidence chunks do the relations of a question point at?

Evaluation-only for now (plan "graph-first", E1–E6; measured by `evaluate.py coverage`): the set is
computed on an in-memory view of the Neo4j projection, not yet wired into the retriever.

  core        chunks within two edges of the entities of the question (parameters, zone, fuel,
              receiving body, sector, documents, and the flow / fuel Condition they select)
  context     for every limit / method table a core row belongs to: its footnotes, the clauses
              that refer to it (TABLE_CONTEXT) and the transition / roadmap / default-zone clauses
              it is subject to (EFFECTIVE_UNDER)
  provisions  provisions of the documents in scope whose role fits the intent (HAS_PROVISION):
              "có hiệu lực từ ngày nào" → effectivity, transition, registry facts, preamble
  topics      monitoring requirements of the domain for sampling / method / frequency questions
              (ABOUT_TOPIC)

Typed two-step lookups replace a wider radius: three hops already reach a tenth of the corpus.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from app.graph import gds
from app.graph.runtime import GraphRuntime
from app.ontology import corpus as corpus_mod
from app.retrieval.graph_rank import entity_nodes
from app.retrieval.query import QueryUnderstanding

HOPS = 2
# the walk: the edges PPR uses plus the prose ↔ entity edges of schema v1.3
WALK_RELS = [*gds.RELS, "MENTIONS", "STATED_IN"]
LOOKUP_RELS = [
    "EXTRACTED_FROM",
    "CONTAINS",
    "HAS_EVIDENCE",
    "TABLE_CONTEXT",
    "EFFECTIVE_UNDER",
    "HAS_PROVISION",
    "ABOUT_TOPIC",
    "REPLACES",
    "REPEALS",
]
ROLES_BY_INTENT: dict[str, set[str]] = {
    "effectivity": {"effectivity", "transition", "roadmap", "registry", "preamble"},
    "applicability": {"scope", "subjects", "effectivity", "transition", "roadmap"},
    "scope": {"scope", "subjects"},
    "definition": {"definitions"},
    "method": {"method"},
    "sampling": {"method"},
}
TOPICS_BY_INTENT: dict[str, set[str]] = {
    "frequency": {"frequency_pointer"},
    "method": {"method_precedence"},
    "sampling": {"sampling_condition", "sampling_port", "direct_measurement"},
}
PARTS = ("core", "context", "provisions", "topics")

_EDGES = """
MATCH (a)-[r]->(b)
WHERE type(r) IN $types
RETURN a.id AS a, b.id AS b, type(r) AS t, r.role AS role, r.kind AS kind,
       a:LegalDocument AS a_doc
"""
_DOCS = """
MATCH (d:LegalDocument) WHERE NOT d:ExternalStub
RETURN d.id AS id, d.domain AS domain, d.document_type AS type
"""
_CONDITIONS = """
MATCH (c:Condition) WHERE c.type IN ['flow_regime', 'fuel_type']
RETURN c.id AS id, c.type AS type, c.op AS op, c.value AS value
"""


@dataclass
class GraphView:
    adj: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    out: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    inc: dict[tuple[str, str], list[str]] = field(default_factory=lambda: defaultdict(list))
    chunk_ids: set[str] = field(default_factory=set)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)  # corpus documents
    conditions: list[dict[str, Any]] = field(default_factory=list)
    edges_by_type: dict[str, int] = field(default_factory=dict)

    def add(self, a: str, b: str, rel: str, walk: bool = True, **props: Any) -> None:
        """One edge; `walk=False` keeps it out of the neighbourhood walk (typed lookups only)."""
        if walk:
            self.adj[a].add(b)
            self.adj[b].add(a)
        self.out[(a, rel)].append((b, props))
        self.inc[(b, rel)].append(a)
        self.edges_by_type[rel] = self.edges_by_type.get(rel, 0) + 1


def load_view(rt: GraphRuntime, chunk_ids: set[str], exclude: set[str] | None = None) -> GraphView:
    """Read the projection once. `exclude` drops relationship types (ablation of edge families)."""
    exclude = exclude or set()
    view = GraphView(chunk_ids=set(chunk_ids))
    types = [t for t in dict.fromkeys([*WALK_RELS, *LOOKUP_RELS]) if t not in exclude]
    for r in rt.run(_EDGES, timeout=max(rt.timeout, 60.0), types=types):
        # the document-wide evidence edge joins a document to all of its chunks: never walked
        walk = r["t"] in WALK_RELS and not (r["a_doc"] and r["t"] == "HAS_EVIDENCE")
        view.add(r["a"], r["b"], r["t"], walk=walk, role=r["role"], kind=r["kind"])
    for d in rt.run(_DOCS):
        view.documents[d["id"]] = d
    view.conditions = rt.run(_CONDITIONS)
    return view


def neighbourhood(adj: dict[str, set[str]], seed: str, hops: int) -> set[str]:
    """Node ids within `hops` edges of `seed` (the seed included)."""
    seen = {seed}
    queue = deque([(seed, 0)])
    while queue:
        node, d = queue.popleft()
        if d == hops:
            continue
        for nxt in adj.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, d + 1))
    return seen


def condition_seeds(view: GraphView, qu: QueryUnderstanding) -> list[str]:
    """Condition nodes the question selects: its flow band and its fuel."""
    out = []
    for c in view.conditions:
        if c["type"] == "fuel_type" and qu.fuel_type and c["value"] == qu.fuel_type:
            out.append(c["id"])
        elif c["type"] == "flow_regime" and qu.flow_m3_day is not None:
            try:
                bound = float(c["value"])
            except (TypeError, ValueError):
                continue
            below = qu.flow_m3_day <= bound
            if (c["op"] in ("<=", "<")) == below:
                out.append(c["id"])
    return out


def seeds(view: GraphView, qu: QueryUnderstanding) -> list[str]:
    return list(dict.fromkeys([*entity_nodes(qu), *condition_seeds(view, qu)]))


def documents_in_scope(view: GraphView, qu: QueryUnderstanding) -> list[str]:
    """Corpus documents the question is about: the ones it names, the ones that replaced a named
    older document, and the QCVN of its domain."""
    docs: list[str] = []
    for d in qu.document_ids:
        node = f"doc:{d}"
        if node in view.documents:
            docs.append(node)
        for rel in ("REPLACES", "REPEALS"):
            docs += [s for s in view.inc.get((node, rel), []) if s in view.documents]
    if corpus_mod.current().is_regulated(qu.domain):
        docs += [
            i for i, d in view.documents.items() if d["type"] == "qcvn" and d["domain"] == qu.domain
        ]
    return list(dict.fromkeys(docs))


def candidates(view: GraphView, qu: QueryUnderstanding) -> dict[str, set[str]]:
    """The candidate chunks of one question, by the part of the lookup that produced them."""
    core: set[str] = set()
    for s in seeds(view, qu):
        if s in view.adj:
            core |= neighbourhood(view.adj, s, HOPS) & view.chunk_ids

    tables = {
        t
        for c in core
        for src, _ in view.out.get((c, "EXTRACTED_FROM"), [])
        for t in view.inc.get((src, "CONTAINS"), [])
    }
    context = {
        c
        for t in tables
        for rel in ("TABLE_CONTEXT", "EFFECTIVE_UNDER")
        for c, _ in view.out.get((t, rel), [])
    }

    roles = ROLES_BY_INTENT.get(qu.intent, set())
    provisions = {
        c
        for d in (documents_in_scope(view, qu) if roles else [])
        for c, props in view.out.get((d, "HAS_PROVISION"), [])
        if props.get("role") in roles
    }

    domains = corpus_mod.current().candidates(qu.domain)
    topics = {
        c
        for dom in domains
        for kind in TOPICS_BY_INTENT.get(qu.intent, set())
        for req in view.inc.get((f"topic:{dom}:{kind}", "ABOUT_TOPIC"), [])
        for c, _ in view.out.get((req, "HAS_EVIDENCE"), [])
    }
    parts = {"core": core, "context": context, "provisions": provisions, "topics": topics}
    return {k: v & view.chunk_ids for k, v in parts.items()}


def union(parts: dict[str, set[str]], names: tuple[str, ...] = PARTS) -> set[str]:
    return set().union(*(parts[n] for n in names))


# ---------------------------------------------------------------- the same lookup inside Neo4j
# One round trip: the relations pick the candidate chunks, then the embeddings stored on those
# nodes rank them (exact cosine — the set is a few hundred chunks at most, no index needed).
CYPHER_SEARCH = """
CALL () {
  MATCH (s) WHERE s.id IN $seeds
  MATCH p = (s)-[*1..2]-(c:EvidenceChunk)
  WHERE all(r IN relationships(p) WHERE type(r) IN $walk
            AND NOT (startNode(r):LegalDocument AND type(r) = 'HAS_EVIDENCE'))
  RETURN collect(DISTINCT c) AS core
}
CALL (core) {
  UNWIND core AS c
  MATCH (c)-[:EXTRACTED_FROM]->()<-[:CONTAINS]-()-[:TABLE_CONTEXT|EFFECTIVE_UNDER]->(x:EvidenceChunk)
  RETURN collect(DISTINCT x) AS context
}
CALL () {
  MATCH (d:LegalDocument)-[r:HAS_PROVISION]->(x:EvidenceChunk)
  WHERE d.id IN $docs AND r.role IN $roles
  RETURN collect(DISTINCT x) AS provisions
}
CALL () {
  MATCH (t:Topic)<-[:ABOUT_TOPIC]-()-[:HAS_EVIDENCE]->(x:EvidenceChunk)
  WHERE t.id IN $topics
  RETURN collect(DISTINCT x) AS topics
}
UNWIND [[core, 'core'], [context, 'context'], [provisions, 'provisions'], [topics, 'topics']] AS part
UNWIND part[0] AS x
WITH x, collect(part[1]) AS parts
RETURN x.id AS id, parts,
       CASE WHEN $v IS NULL OR x.embedding IS NULL THEN null
            ELSE vector.similarity.cosine(x.embedding, $v) END AS score
ORDER BY score DESC, id
"""


def lookup_params(view: GraphView, qu: QueryUnderstanding) -> dict[str, Any]:
    """What the question contributes to the Cypher lookup (the small dictionaries of `view` —
    corpus documents, flow / fuel conditions — are read once, not per question)."""
    roles = ROLES_BY_INTENT.get(qu.intent, set())
    domains = corpus_mod.current().candidates(qu.domain)
    return {
        "seeds": seeds(view, qu),
        "walk": WALK_RELS,
        "docs": documents_in_scope(view, qu) if roles else [],
        "roles": sorted(roles),
        "topics": [
            f"topic:{d}:{k}" for d in domains for k in sorted(TOPICS_BY_INTENT.get(qu.intent, ()))
        ],
    }


def neo4j_search(
    rt: GraphRuntime, view: GraphView, qu: QueryUnderstanding, vector: list[float] | None = None
) -> list[dict[str, Any]]:
    """Graph-first search in the database: [{id, parts, score}] best first. `score` is the cosine
    of the chunk embedding with `vector` (None without a query vector: candidates only)."""
    rows = rt.run(CYPHER_SEARCH, timeout=max(rt.timeout, 10.0), v=vector, **lookup_params(view, qu))
    for r in rows:
        if r["score"] is not None:
            r["score"] = 2.0 * float(r["score"]) - 1.0  # Neo4j reports cosine as (1 + cos) / 2
    return rows
