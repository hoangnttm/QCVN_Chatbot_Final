"""Tier M generator (plan 2026-09-29 §4.6): deterministic, >= 2 grade-2 chunks per item, hops
anchored in the raw source, splits inherited from tier A rows."""

from pathlib import Path

import pytest
import yaml

from app.evaluation.gold_multihop import HopError, MultiHopGenerator, generate_multihop
from app.evaluation.gold_schema import load_gold

GOLD = Path(__file__).resolve().parents[2] / "data" / "gold"
TEMPLATES = GOLD / "templates_v2.yaml"
SPEC = GOLD / "multihop_v2.yaml"
TIER_A = GOLD / "gold_tables_v2.jsonl"


@pytest.fixture(scope="module")
def items(settings):
    return generate_multihop(settings, TEMPLATES, SPEC, TIER_A)


def test_every_item_is_multi_hop(items):
    assert len(items) >= 100
    for it in items:
        g2 = it.grade2()
        assert len({r.chunk_id for r in g2}) >= 2, it.id
        assert "multi_hop" in it.difficulty_factors and it.tier == "M"
        # the table row and at least one chunk outside that row
        assert any(r.chunk_id.startswith("chunk:QCVN") and ".r" in r.chunk_id for r in g2), it.id


def test_generation_is_deterministic(settings, items):
    again = generate_multihop(settings, TEMPLATES, SPEC, TIER_A)
    assert [(i.id, i.question, i.split) for i in items] == [
        (i.id, i.question, i.split) for i in again
    ]


def test_splits_follow_tier_a_rows(items):
    tier_a = {it.cluster_id: it.split for it in load_gold(TIER_A, tier="A")}
    shared = [it for it in items if it.cluster_id in tier_a]
    assert shared and all(it.split == tier_a[it.cluster_id] for it in shared)
    by_row: dict[str, set[str]] = {}
    for it in items:
        by_row.setdefault(it.cluster_id or it.id, set()).add(it.split or "")
    assert all(len(s) == 1 for s in by_row.values())


def test_zone_by_definition_never_names_the_column(items):
    for it in items:
        if it.stratum == "M-ZD":
            q = it.question.lower()
            assert "cột" not in q and "loại a" not in q and "loại b" not in q, it.id


def test_default_zone_items_are_column_b(items):
    assert all(it.expected_conditions.zone == "B" for it in items if it.stratum == "M-DZ")


def test_hop_quote_must_be_in_the_raw_lines(settings, tmp_path):
    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    spec["hops"]["DZ_WW"]["quote"] = "câu này không có trong văn bản"
    bad = tmp_path / "multihop.yaml"
    bad.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    with pytest.raises(HopError, match="DZ_WW"):
        MultiHopGenerator(settings, TEMPLATES, bad)
