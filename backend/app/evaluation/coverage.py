"""Candidate-set coverage — can the graph go first? (plan "graph-first", step E0)

For every gold item the entities of the question (`graph_rank.entity_nodes`, production rules) are
looked up in the Neo4j projection and the evidence chunks within `h` hops form a candidate set:

- `or<h>`   union of the neighbourhoods of all entities
- `and<h>`  chunks reachable from every *type* of entity the question names (parameter AND zone …);
            entities of one type are alternatives
- `ctx`     the two-hop core plus the context of the tables it touches (footnotes, referring
            clauses, transition / roadmap clauses) — `app.retrieval.graph_candidates`
- `typed`   `ctx` plus the provisions and monitoring topics the intent asks for

Three numbers per variant decide whether lexical + vector search may run inside that set:

- coverage    share of the must-have chunks (grade 2) that are in the set — the ceiling of any
              retriever restricted to it
- filter      recall_must@10 of the hybrid ranking with everything outside the set removed
- prioritise  recall_must@10 with the set moved to the front and the rest kept behind it

against the same hybrid ranking untouched (`baseline`). An item with no entity in the graph or an
empty set falls back to the baseline and is counted in `fallback`. Deterministic; no LLM. The
ranking is BM25 + vector without the structured fast path (which is itself a relational lookup),
cut at the 50 candidates the ranker reports.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.evaluation.gold_schema import GoldItem
from app.retrieval import graph_candidates as gc
from app.retrieval.graph_candidates import neighbourhood
from app.retrieval.graph_rank import entity_nodes
from app.retrieval.retriever import RetrievalService, Retriever, SearchRequest

K = 10
Adjacency = dict[str, set[str]]


def candidate_sets(
    adj: Adjacency, chunk_ids: set[str], entities: list[str], hops: int
) -> dict[str, set[str]]:
    """`or` / `and` candidate chunks for the entity nodes of one question."""
    per = {e: neighbourhood(adj, e, hops) & chunk_ids for e in entities if e in adj}
    by_type: dict[str, set[str]] = defaultdict(set)
    for e, chunks in per.items():
        by_type[e.split(":", 1)[0]] |= chunks
    union = set().union(*per.values()) if per else set()
    inter = set.intersection(*by_type.values()) if by_type else set()
    return {"or": union, "and": inter}


def recall(ranking: list[str], must: list[str], k: int = K) -> float:
    return len(set(ranking[:k]) & set(must)) / len(must)


def score_item(ranking: list[str], must: list[str], cand: set[str]) -> dict[str, Any]:
    """Coverage / filter / prioritise for one candidate set; an empty set falls back to baseline."""
    base = recall(ranking, must)
    if not cand:
        return {"fallback": True, "size": 0, "coverage": None, "filter": base, "prioritise": base}
    inside = [c for c in ranking if c in cand]
    return {
        "fallback": False,
        "size": len(cand),
        "coverage": len(cand & set(must)) / len(must),
        "filter": recall(inside, must),
        "prioritise": recall(inside + [c for c in ranking if c not in cand], must),
        "missed": sorted(set(must) - cand),
    }


def run(
    session: Session,
    svc: RetrievalService,
    items: list[GoldItem],
    hops: tuple[int, ...] = (2, 3),
    exclude: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`exclude` drops relationship types from the graph view (ablation of an edge family)."""
    svc.ensure(session)
    rt = svc.graph
    st = rt.status(force=True)
    reason = st.usable_for(svc.index_label)
    if reason:  # a silent fallback would report the baseline as if it were the graph
        raise RuntimeError(f"coverage needs the graph projection: {reason}")
    types = dict(
        session.execute(select(m.EvidenceChunk.chunk_id, m.EvidenceChunk.chunk_type)).all()
    )
    chunk_ids = set(types)
    view = gc.load_view(rt, chunk_ids, exclude)
    adj = view.adj
    retriever = Retriever(session, svc)
    rows: list[dict[str, Any]] = []
    for it in items:
        must = [r.chunk_id for r in it.relevant if r.grade == 2]
        if not must:
            continue
        res = retriever.search(
            SearchRequest(
                question=it.question,
                as_of_date=it.as_of_date,
                k=K,
                candidates=200,
                use_structured=False,
                type_boost=False,
                expand=False,
                return_ranking=True,
            )
        )
        ranking = [c.chunk_id for c in res.ranking]
        entities = entity_nodes(res.understanding)
        row: dict[str, Any] = {
            "item_id": it.id,
            "tier": it.tier,
            "stratum": it.stratum,
            "intent": it.intent,
            "must": must,
            "entities": entities,
            "entities_in_graph": [e for e in entities if e in adj],
            "baseline": recall(ranking, must),
            "degraded": any("vector search unavailable" in w for w in res.warnings),
            "variants": {},
        }
        for h in hops:
            for mode, cand in candidate_sets(adj, chunk_ids, entities, h).items():
                row["variants"][f"{mode}{h}"] = score_item(ranking, must, cand)
        parts = gc.candidates(view, res.understanding)
        row["understood"] = {"intent": res.understanding.intent, "zone": res.understanding.zone}
        row["parts"] = {k: len(v) for k, v in parts.items()}
        row["variants"]["ctx"] = score_item(ranking, must, gc.union(parts, ("core", "context")))
        row["variants"]["typed"] = score_item(ranking, must, gc.union(parts))
        rows.append(row)
    summ = summarize(rows, types)
    summ["graph"] = {
        "projection_version": st.projection_version,
        "index_label": st.index_label,
        "edges": sum(view.edges_by_type.values()),
        "edges_by_type": dict(sorted(view.edges_by_type.items())),
        "excluded": sorted(exclude or ()),
        "chunks": len(chunk_ids),
    }
    return rows, summ


def _mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 4) if xs else None


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "n": len(rows),
        "with_entities": sum(bool(r["entities_in_graph"]) for r in rows),
        "baseline": _mean([r["baseline"] for r in rows]),
        "variants": {},
    }
    for name in rows[0]["variants"] if rows else []:
        vs = [r["variants"][name] for r in rows]
        sized = [v["size"] for v in vs if not v["fallback"]]
        out["variants"][name] = {
            "fallback": sum(v["fallback"] for v in vs),
            "median_size": int(statistics.median(sized)) if sized else None,
            "coverage": _mean([v["coverage"] for v in vs if not v["fallback"]]),
            "filter": _mean([v["filter"] for v in vs]),
            "prioritise": _mean([v["prioritise"] for v in vs]),
        }
    return out


def summarize(rows: list[dict[str, Any]], chunk_types: dict[str, str]) -> dict[str, Any]:
    summ: dict[str, Any] = {"all": _summary(rows), "tiers": {}, "missed_types": {}}
    for tier in sorted({r["tier"] for r in rows}):
        summ["tiers"][tier] = _summary([r for r in rows if r["tier"] == tier])
    for name in rows[0]["variants"] if rows else []:
        missed = Counter(
            chunk_types.get(c, "?") for r in rows for c in r["variants"][name].get("missed", [])
        )
        summ["missed_types"][name] = dict(missed.most_common())
    summ["degraded"] = sum(r["degraded"] for r in rows)
    return summ


def render(run_id: str, summ: dict[str, Any]) -> str:
    g = summ["graph"]
    lines = [
        f"# Candidate-set coverage — {run_id}",
        "",
        f"Graph {g['projection_version']} ({g['edges']} edges read, {g['chunks']} chunks"
        + (f", without {', '.join(g['excluded'])}" if g.get("excluded") else "")
        + "). "
        f"recall = recall_must@{K}; baseline = BM25 + vector without the graph.",
        "",
    ]
    if summ["degraded"]:
        lines += [
            f"**DEGRADED: vector search failed on {summ['degraded']} item(s) — do not use.**",
            "",
        ]
    for label, s in [("all", summ["all"])] + [(f"tier {t}", v) for t, v in summ["tiers"].items()]:
        lines += [
            f"## {label} (n = {s['n']}, with entities in the graph {s['with_entities']}, "
            f"baseline {s['baseline']})",
            "",
            "| set | fallback | median size | coverage | filter | prioritise |",
            "|---|---|---|---|---|---|",
        ]
        for name, v in s["variants"].items():
            lines.append(
                f"| {name} | {v['fallback']} | {v['median_size']} | {v['coverage']} "
                f"| {v['filter']} | {v['prioritise']} |"
            )
        lines.append("")
    lines += ["## Must-have chunks outside the set, by chunk type", ""]
    for name, missed in summ["missed_types"].items():
        lines.append(f"- {name}: {missed or 'none'}")
    return "\n".join(lines) + "\n"
