"""Dev/test assignment and freezing of the gold set (plan §2.9).

Tier B: per stratum, seeded shuffle, first `quota_dev` → dev; `paired_with` twins co-assigned;
`v1_migrated` forced to dev; existing assignments are never changed without `force`.
Tier A: assignment by `cluster_id` (table row) so all cells of a row share a split."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.evaluation.gold_schema import GoldItem, Strata


def assign_splits(
    items: list[GoldItem],
    strata: Strata,
    seed: int | None = None,
    *,
    force: bool = False,
    dev_share_a: float = 0.2,
) -> list[GoldItem]:
    seed = strata.seed if seed is None else seed
    by_id = {it.id: it for it in items}
    quotas = {s.code: s.quota_dev for s in strata.strata}

    # tier B
    groups: dict[str, list[GoldItem]] = defaultdict(list)
    for it in items:
        if it.tier == "B":
            groups[it.stratum or "_"].append(it)
    for code, lst in groups.items():
        rng = random.Random(f"{seed}:{code}")
        lst = sorted(lst, key=lambda x: x.id)
        rng.shuffle(lst)
        fixed_dev = sum(1 for it in lst if it.split == "dev" and not force)
        need = max(0, quotas.get(code, 0) - fixed_dev)
        for it in lst:
            if it.split is not None and not force:
                continue
            if it.source == "v1_migrated":
                it.split = "dev"
                continue
            group = [it]
            tw = by_id.get(it.paired_with or "")
            if tw is not None and tw.tier == "B" and (tw.split is None or force) and tw is not it:
                group.append(tw)
            s = "dev" if need >= len(group) else "test"
            for g in group:
                g.split = s
            if s == "dev":
                need -= len(group)
    # twins follow the first-assigned partner (dev wins to keep test blind)
    for it in items:
        if it.paired_with and it.paired_with in by_id:
            tw = by_id[it.paired_with]
            if it.split != tw.split:
                s = "dev" if "dev" in (it.split, tw.split) else it.split
                it.split = tw.split = s

    # tier A by cluster (row)
    a_items = [it for it in items if it.tier == "A"]
    clusters = sorted({it.cluster_id or it.id for it in a_items})
    rng = random.Random(f"{seed}:A")
    rng.shuffle(clusters)
    n_dev = round(len(clusters) * dev_share_a)
    dev_clusters = set(clusters[:n_dev])
    for it in a_items:
        if it.split is None or force:
            it.split = "dev" if (it.cluster_id or it.id) in dev_clusters else "test"

    # tier C is test only
    for it in items:
        if it.tier == "C":
            it.split = "test"
    return items


def _git_commit(repo: Path) -> tuple[str | None, bool]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
        )
        return out.stdout.strip(), bool(dirty.stdout.strip())
    except Exception:  # noqa: BLE001 - git optional
        return None, False


def freeze(
    items: list[GoldItem],
    path: Path,
    version: str,
    *,
    repo: Path,
    index_label: str | None,
    document_hashes: dict[str, str] | None,
    strata: Strata,
) -> dict[str, Any]:
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    commit, dirty = _git_commit(repo)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for it in items:
        counts[f"{it.tier}:{it.stratum or '-'}"][it.split or "none"] += 1
    manifest = {
        "dataset_version": version,
        "file": path.name,
        "frozen_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "sha256": sha,
        "seed": strata.seed,
        "index_label": index_label,
        "document_hashes": document_hashes or {},
        "n_items": len(items),
        "counts": {k: dict(v) for k, v in sorted(counts.items())},
        "by_split": {s: sum(1 for it in items if it.split == s) for s in ("dev", "val", "test")},
    }
    mpath = path.with_suffix(".manifest.json")
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def manifest_matches(path: Path) -> bool | None:
    """None when no manifest; True when the jsonl sha256 equals the frozen one."""
    mpath = path.with_suffix(".manifest.json")
    if not mpath.exists():
        return None
    man = json.loads(mpath.read_text(encoding="utf-8"))
    return hashlib.sha256(path.read_bytes()).hexdigest() == man.get("sha256")
