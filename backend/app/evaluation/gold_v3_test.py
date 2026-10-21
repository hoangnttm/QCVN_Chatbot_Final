"""Template part of the new test split, gold v3 round 1 (plan 2026-10-06 §0, PO decision D53).

Every row of QCVN 40:2025 Bảng 1 / Bảng 2 was already in dev or val, so the new test split cannot
be drawn from unseen wastewater cells. D53: half of the QCVN 40 rows leave `val` and are reserved
for test (`reserve_rows`), asked again with new template sentences written by the author. Emission
cells come from QCVN 19:2024 rows no earlier item touched.

`generate_test` builds T1 (one cell) and T2 (cell + hop) items for those rows with the sentences
of sheet «Mẫu câu». Values, conditions, adjacent cells and hops come from the same raw-table
reader and hop list as tiers A and M; no LLM is involved."""

from __future__ import annotations

import copy
import hashlib
import random
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings
from app.evaluation.gold_generate import Generator, norm_question, sample_cells
from app.evaluation.gold_multihop import TABLES, MultiHopGenerator
from app.evaluation.gold_schema import GoldItem
from app.evaluation.gold_v3 import FactIndex
from app.evaluation.rawtables import RawCell

WW_TABLES = ("QCVN40_2025.B1", "QCVN40_2025.B2")
T1_QUOTA = {"wastewater": 50, "emission": 50}
T2_QUOTA = {
    "DZ": {"wastewater": 6, "emission": 6},
    "ZD": {"wastewater": 6, "emission": 6},
    "TR": {"wastewater": 6, "emission": 6},
    "RP": {"wastewater": 10},
    "FN_TOC": {"wastewater": 4},
    "FN_O2": {"emission": 10},
}
T1_KINDS = ("flow_zone", "zone", "equipment_zone")


def _row(cell: RawCell) -> str:
    return f"{cell.table_code}.r{cell.row_key}"


# ---------------------------------------------------------------- reservation (D53)


def reserve_rows(
    items: list[GoldItem], hand: dict[str, dict[str, str]], *, seed: int, share: float = 0.5
) -> dict[str, Any]:
    """Rows that leave `val` for the new test split: per QCVN 40 table `share` of the val rows
    (seeded order), always including the rows the hand-written test items of group B1 sit on —
    for QCVN 19 only those. Rows of hand-written dev items are never taken."""
    split = {it.cluster_id: it.split for it in items if it.cluster_id}
    hand_rows = {
        s: sorted({h["row"] for h in hand.values() if h["split"] == s}) for s in ("dev", "test")
    }
    clash = [r for r in hand_rows["test"] if split.get(r) == "dev"]
    if clash:
        raise ValueError(f"hand-written test cells on dev rows: {clash}")
    reserved = {r for r in hand_rows["test"] if split.get(r) == "val"}
    for table in WW_TABLES:
        val_rows = sorted(r for r, s in split.items() if s == "val" and r.startswith(f"{table}.r"))
        pool = [r for r in val_rows if r not in reserved and r not in hand_rows["dev"]]
        pool.sort(key=lambda r: hashlib.sha1(f"{seed}:{r}".encode()).hexdigest())
        need = round(len(val_rows) * share) - len(reserved & set(val_rows))
        reserved |= set(pool[: max(0, need)])
    return {
        "version": 1,
        "decision": "D53 (PO 2026-10-07): half of the QCVN 40 rows leave val for the new test",
        "seed": seed,
        "reserved_rows": sorted(reserved),
        "hand_rows": hand_rows,
        "hand_cells": sorted(hand),
    }


def drop_reserved(items: list[GoldItem], reserve: dict[str, Any]) -> list[GoldItem]:
    """Old-phrasing val items on reserved rows are taken out (they stay in the frozen v2 files)."""
    rows = set(reserve["reserved_rows"])
    return [it for it in items if not (it.split == "val" and it.cluster_id in rows)]


# ---------------------------------------------------------------- generation


def _t1_templates(base: dict[str, Any], new: dict[str, list[str]]) -> dict[str, Any]:
    t = copy.deepcopy(base)
    for kind in T1_KINDS:
        t["templates"][kind] = [
            {"id": f"v3_{kind}_{i}", "text": text} for i, text in enumerate(new[f"T1:{kind}"], 1)
        ]
    return t


class _TestMultiHop(MultiHopGenerator):
    def __init__(
        self,
        settings: Settings,
        templates_a: Path,
        spec: Path,
        *,
        seed: int,
        new: dict[str, list[str]],
        allowed: set[str],
    ) -> None:
        super().__init__(settings, templates_a, spec)
        self.seed = seed
        self.rng = random.Random(f"M:{seed}")
        self.base.rng = random.Random(seed)
        self.base.t = _t1_templates(self.base.t, new)
        self.allowed = allowed
        self.spec["quota"] = T2_QUOTA
        self.spec["templates"] = {
            pattern: {
                domain: [
                    {"id": f"v3_{pattern.lower()}_{domain[0]}{i}", "text": text}
                    for i, text in enumerate(new[f"T2:{pattern}:{domain}"], 1)
                ]
                for domain in per_domain
            }
            for pattern, per_domain in T2_QUOTA.items()
        }

    def _eligible(self, pattern: str, domain: str) -> list[RawCell]:
        return [c for c in super()._eligible(pattern, domain) if c.cell_key in self.allowed]


def cell_pool(
    gen: Generator, existing: list[GoldItem], reserve: dict[str, Any]
) -> dict[str, list[RawCell]]:
    """Cells the template test items may use: wastewater = reserved QCVN 40 rows; emission = QCVN
    19 rows no dev/val item and no hand-written dev item sits on. Cells of hand-written items are
    left out (the author asks about them)."""
    used = {it.cluster_id for it in existing if it.split in ("dev", "val") and it.cluster_id}
    reserved = set(reserve["reserved_rows"])
    hand = set(reserve["hand_cells"])
    dev_rows = set(reserve["hand_rows"]["dev"])
    pool: dict[str, list[RawCell]] = {"wastewater": [], "emission": []}
    for domain, tables in TABLES.items():
        for t in tables:
            for c in gen.reader.cells(t):
                row = _row(c)
                if c.cell_key in hand or row in dev_rows or row in used:
                    continue
                if domain == "wastewater" and row not in reserved:
                    continue
                if gen._rule_for(c) is None:
                    continue
                pool[domain].append(c)
    return pool


def generate_test(
    settings: Settings,
    spec: Path,
    new: dict[str, list[str]],
    reserve: dict[str, Any],
    existing: list[GoldItem],
    facts: FactIndex | None,
    *,
    seed: int,
) -> tuple[list[GoldItem], list[GoldItem], list[str]]:
    """(T1 items, T2 items, notes). Every item is split=test."""
    templates_a = settings.data_dir / "gold" / "templates_v2.yaml"
    gen = Generator(settings, templates_a, seed)
    gen.t = _t1_templates(gen.t, new)
    pool = cell_pool(gen, existing, reserve)
    notes: list[str] = []
    seen = {norm_question(it.question) for it in existing}

    def finish(it: GoldItem, group: str, idx: int, extra: dict[str, Any] | None = None) -> GoldItem:
        it = it.model_copy(
            update={
                "schema_version": 3,
                "id": f"G-{group}-{idx:04d}",
                "group": group,
                "split": "test",
                "tags": [*it.tags, "v3_round1"],
            }
            | (extra or {})
        )
        if facts is not None:
            it.fact_ids, it.legal_address = facts.for_item(it)
        return it

    t1: list[GoldItem] = []
    for domain, n in T1_QUOTA.items():
        # sub-rows of Bảng 2 need a row phrase; the hand-written group B1 covers them
        cells = [c for c in pool[domain] if not (c.table_code == WW_TABLES[1] and "." in c.row_key)]
        chosen = sample_cells(cells, n, random.Random(f"{seed}:T1:{domain}"))
        if len(chosen) < n:
            notes.append(f"T1 {domain}: {len(chosen)}/{n} cells available")
        siblings = {t: gen.reader.cells(t) for t in TABLES[domain]}
        for cell in chosen:
            item = None
            for _ in range(8):
                item = gen.item_for(cell, siblings[cell.table_code], 1)
                if norm_question(item.question) not in seen:
                    break
            assert item is not None
            if norm_question(item.question) in seen:
                notes.append(f"T1: no unused wording for {cell.cell_key}")
                continue
            seen.add(norm_question(item.question))
            t1.append(finish(item, "T1", len(t1) + 1))
    if gen.findings:
        notes.append(f"T1: {len(gen.findings)} raw-table / parser differences")

    allowed = {c.cell_key for cells in pool.values() for c in cells}
    mh = _TestMultiHop(settings, templates_a, spec, seed=seed, new=new, allowed=allowed)
    t2 = [finish(it, "T2", i) for i, it in enumerate(mh.generate(), 1)]
    got = Counter(it.stratum for it in t2)
    for pattern, per_domain in T2_QUOTA.items():
        want = sum(per_domain.values())
        if got[f"M-{pattern}"] < want:
            notes.append(f"T2 {pattern}: {got[f'M-{pattern}']}/{want} items")
    return t1, t2, notes


def load_reserve(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
