"""Gold set v3, round 1 (docs/plans/2026-10-06-xay-lai-bo-cau-hoi.md §0).

Carries the v2 items over to schema v3 and re-confirms them against the fact layer of the working
database: the former test split becomes `val`, every item gets its question `group`, the facts it
rests on (`fact_ids`) and their legal address. Nothing is rewritten silently: an item whose target
rule has no fact, or whose expected value is not the verbatim cell of that fact, is reported."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.evaluation.gold_schema import GoldItem, Strata
from app.evaluation.gold_source import normalize_raw

TIER_GROUP = {"A": "T1", "M": "T2", "C": "C"}


class FactIndex:
    """Facts of the working database by the record they point at and by the chunk that carries
    them."""

    def __init__(self, session: Session) -> None:
        self.by_ref: dict[str, dict[str, Any]] = {}
        self.by_chunk: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for f in session.scalars(select(m.Fact).order_by(m.Fact.fact_id)):
            d = {
                "fact_id": f.fact_id,
                "fact_type": f.fact_type,
                "chunk_id": f.chunk_id,
                "address_label": f.address_label,
                "quote": f.quote,
                "trust": f.trust,
                "ref_id": f.ref_id,
            }
            self.by_ref[f.ref_id] = d
            if f.chunk_id:
                self.by_chunk[f.chunk_id].append(d)

    def for_item(self, it: GoldItem) -> tuple[list[str], list[str]]:
        """(`fact_ids`, `legal_address`) of the must-cite basis: the facts of the target rules,
        and for a grade-2 chunk without a target rule the facts stated in that chunk (cells of a
        table row are left out, a row holds one per column). A chunk no fact points at keeps its
        citation label as address."""
        ids: list[str] = []
        address: list[str] = []
        covered: set[str] = set()
        for code in it.target_rule_codes:
            f = self.by_ref.get(code)
            if f:
                ids.append(f["fact_id"])
                address.append(f["address_label"] or "")
                if f["chunk_id"]:
                    covered.add(f["chunk_id"])
        for rc in it.grade2():
            if rc.chunk_id in covered:
                continue
            facts = [f for f in self.by_chunk.get(rc.chunk_id, []) if f["fact_type"] != "limit"]
            ids += [f["fact_id"] for f in facts]
            address.append(rc.citation_label)
        return list(dict.fromkeys(ids)), [a for a in dict.fromkeys(address) if a]


def migrate_items(
    items: list[GoldItem], strata: Strata, facts: FactIndex
) -> tuple[list[GoldItem], list[str]]:
    """v2 → v3 in place. Returns the items and the findings a person has to look at."""
    groups = {s.code: s.group for s in strata.strata}
    findings: list[str] = []
    for it in items:
        it.schema_version = 3
        if it.split == "test":
            it.split = "val"
        it.group = TIER_GROUP.get(it.tier) or groups.get(it.stratum or "")
        if it.group is None:
            findings.append(f"{it.id}: stratum {it.stratum!r} has no group in strata_v3.yaml")
        it.fact_ids, it.legal_address = facts.for_item(it)
        for code in it.target_rule_codes:
            f = facts.by_ref.get(code)
            if f is None:
                findings.append(f"{it.id}: target {code} has no fact in the working database")
                continue
            if f["fact_type"] != "limit" or it.intent != "threshold":
                continue
            cell = normalize_raw(f["quote"] or "").replace(" ", "")
            for en in it.expected_numbers:
                if en.role not in ("limit", "range_min", "range_max") or not en.display:
                    continue
                if normalize_raw(en.display).replace(" ", "") not in cell:
                    findings.append(
                        f"{it.id}: expected {en.display!r} is not in the verbatim cell"
                        f" {f['quote']!r} of {f['fact_id']}"
                    )
        if it.expected_behaviour.mode in ("answer", "pointer") and not it.legal_address:
            findings.append(f"{it.id}: no legal address for an answerable item")
    return items, findings


def summary(items: list[GoldItem]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = defaultdict(Counter)
    for it in items:
        out[it.group or "-"][it.split or "none"] += 1
    return {g: dict(c) for g, c in sorted(out.items())}


def write_frozen_manifest(paths: list[Path], out: Path, version: str) -> dict[str, Any]:
    """sha256 of the v2 files at the moment they stop being edited."""
    man = {
        "dataset_version": version,
        "note": "v2 files are no longer edited; v3 files are derived from them (gold migrate-v3)",
        "files": {
            p.name: {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "n_items": sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln),
            }
            for p in paths
            if p.exists()
        },
    }
    out.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return man
