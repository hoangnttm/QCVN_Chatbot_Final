"""Gold ↔ retrieved matching: `chunk_id` first, separator-aware label fallback for v1 items."""

from __future__ import annotations

from app.evaluation.gold_schema import GoldItem
from app.evaluation.gold_validate import label_matches


def grade_of(chunk_id: str, chunk_label: str | None, item: GoldItem) -> int:
    """2 must-cite · 1 supporting · 0 acceptable · -1 not relevant."""
    for r in item.relevant:
        if r.chunk_id == chunk_id:
            return r.grade
    if chunk_label and not item.relevant:
        for lbl in item.expected_citation_labels:
            if label_matches(lbl, chunk_label):
                return 2
    return -1


def grades_for(chunks: list[tuple[str, str | None]], item: GoldItem) -> list[int]:
    return [grade_of(cid, lbl, item) for cid, lbl in chunks]


def must_ids(item: GoldItem) -> set[str]:
    return {r.chunk_id for r in item.relevant if r.grade == 2}


def relevant_ids(item: GoldItem, min_grade: int = 1) -> set[str]:
    return {r.chunk_id for r in item.relevant if r.grade >= min_grade}
