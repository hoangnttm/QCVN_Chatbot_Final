"""Retrieval metrics on graded relevance (plan §3.2): Hit@k, Recall@k (must / all), Precision@k,
MRR, nDCG@k (gain 2^g-1), pack completeness, cross-domain share. Computed on two lists per item:
stage A = ranker top-k (before graph expansion), stage B = the evidence pack the LLM sees."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from app.evaluation.gold_schema import GoldItem
from app.evaluation.matching import grades_for, must_ids, relevant_ids

GAIN = {2: 3.0, 1: 1.0, 0: 0.0, -1: 0.0}
OPPOSITE = {"wastewater": "emission", "emission": "wastewater"}


def _dcg(grades: list[int]) -> float:
    return sum(GAIN.get(g, 0.0) / math.log2(i + 2) for i, g in enumerate(grades))


def score_list(
    chunks: list[tuple[str, str | None, str | None]],
    item: GoldItem,
    *,
    ks: tuple[int, ...] = (5, 10, 20),
    stage: str,
) -> dict[str, Any]:
    """`chunks` = [(chunk_id, citation_label, domain)] in rank order."""
    ids = [c[0] for c in chunks]
    grades = grades_for([(c[0], c[1]) for c in chunks], item)
    r2 = must_ids(item)
    r1 = relevant_ids(item, 1)
    if not r1 and item.expected_citation_labels and not item.relevant:
        r1 = {cid for cid, g in zip(ids, grades, strict=False) if g >= 1}  # v1 label fallback
        r2 = set(r1)
    row: dict[str, Any] = {
        "item_id": item.id,
        "stage": stage,
        "n_list": len(ids),
        "n_must": len(r2),
        "n_relevant": len(r1),
        "grades": grades,
        "applicable": bool(r1),
    }
    if not r1:
        return row
    first = next((i for i, g in enumerate(grades) if g >= 1), None)
    row["mrr"] = 1.0 / (first + 1) if first is not None else 0.0
    ideal = sorted((r.grade for r in item.relevant if r.grade >= 1), reverse=True) or [2] * len(r1)
    for k in ks:
        top = ids[:k]
        g_top = grades[:k]
        row[f"hit@{k}"] = float(any(g >= 1 for g in g_top))
        row[f"hit_must@{k}"] = float(any(g == 2 for g in g_top)) if r2 else None
        row[f"recall_must@{k}"] = (len(set(top) & r2) / len(r2)) if r2 else None
        row[f"recall_all@{k}"] = len(set(top) & r1) / len(r1)
        row[f"precision@{k}"] = (len(set(top) & r1) / min(k, len(ids))) if ids else 0.0
        idcg = _dcg([2 if g == 2 else 1 for g in ideal[:k]])
        row[f"ndcg@{k}"] = (_dcg(g_top) / idcg) if idcg > 0 else 0.0
    row["completeness"] = float(r2 <= set(ids)) if r2 else None
    opp = OPPOSITE.get(item.domain)
    if opp and chunks:
        row["cross_domain_share"] = sum(1 for c in chunks if c[2] == opp) / len(chunks)
    return row


def aggregate(
    rows: list[dict[str, Any]], by: str | None = None, items: dict[str, GoldItem] | None = None
) -> dict[str, Any]:
    """Mean of every numeric metric over applicable rows, optionally grouped by an item field."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if not r.get("applicable"):
            continue
        key = "all"
        if by and items:
            key = str(getattr(items[r["item_id"]], by, None))
        groups[key].append(r)
    out: dict[str, Any] = {}
    for key, lst in groups.items():
        metrics: dict[str, list[float]] = defaultdict(list)
        for r in lst:
            for k, v in r.items():
                if (
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and k not in ("n_list", "n_must", "n_relevant")
                ):
                    metrics[k].append(float(v))
        out[key] = {"n": len(lst), **{k: sum(v) / len(v) for k, v in metrics.items() if v}}
    return out
