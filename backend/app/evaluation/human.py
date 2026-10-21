"""Human check of the judge (plan §3.6): stratified 20 % sample (all judge failures + passes by
stratum), a blind rating sheet (judge fields and config hidden, rows shuffled), and agreement
statistics between judge and human."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any

from app.evaluation import stats
from app.evaluation.runner import read_jsonl


def sample_for_human(
    run_dir: Path, *, frac: float = 0.2, seed: int = 20260924, tag: str = "J1"
) -> list[dict[str, Any]]:
    qa = {(r["item_id"], r.get("repeat", 1)): r for r in read_jsonl(run_dir / "qa.jsonl")}
    judged = [
        v
        for v in read_jsonl(run_dir / "judge.jsonl")
        if v.get("tag") == tag and not v.get("judge_error")
    ]
    if not judged:
        return []
    n_target = max(1, round(len(judged) * frac))
    fails = [v for v in judged if not v.get("pass")]
    passes = [v for v in judged if v.get("pass")]
    rng = random.Random(seed)
    chosen = list(fails)
    by_stratum: dict[str, list[dict[str, Any]]] = {}
    for v in passes:
        st = str(qa.get((v["item_id"], v.get("repeat", 1)), {}).get("stratum"))
        by_stratum.setdefault(st, []).append(v)
    remaining = max(0, n_target - len(chosen))
    strata = sorted(by_stratum)
    while remaining > 0 and any(by_stratum.values()):
        for st in strata:
            lst = by_stratum[st]
            if lst and remaining > 0:
                chosen.append(lst.pop(rng.randrange(len(lst))))
                remaining -= 1
    rng.shuffle(chosen)
    return [{**v, "_qa": qa.get((v["item_id"], v.get("repeat", 1)))} for v in chosen]


def export_sheet(run_dir: Path, rows: list[dict[str, Any]], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "item_id",
                "repeat",
                "question",
                "as_of",
                "expected_answer",
                "answer_json",
                "cited_chunks",
                "human_overall (0-3)",
                "human_value_correct (1/0)",
                "human_faithful (1/0)",
                "human_abstain_ok (1/0)",
                "note",
            ]
        )
        for v in rows:
            q = v.get("_qa") or {}
            resp = q.get("response") or {}
            a = resp.get("answer", {})
            cited = set(
                a.get("citations", [])
                + [c for b in a.get("basis", []) for c in b.get("citations", [])]
            )
            chunks = "\n---\n".join(
                f"[{e.get('citation_label')}] {e.get('text', '')[:800]}"
                for e in resp.get("evidence", [])
                if e.get("chunk_id") in cited
            )
            w.writerow(
                [
                    v["item_id"],
                    v.get("repeat", 1),
                    resp.get("question", ""),
                    resp.get("as_of_date", ""),
                    q.get("expected_answer", ""),
                    json.dumps(
                        {
                            k: a.get(k)
                            for k in (
                                "conclusion",
                                "basis",
                                "conditions",
                                "warnings",
                                "missing_information",
                                "abstained",
                                "abstain_reason",
                            )
                        },
                        ensure_ascii=False,
                        indent=1,
                    ),
                    chunks,
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )
    return len(rows)


def agreement(run_dir: Path, sheet: Path, *, tag: str = "J1") -> dict[str, Any]:
    judged = {
        (v["item_id"], int(v.get("repeat", 1))): v
        for v in read_jsonl(run_dir / "judge.jsonl")
        if v.get("tag") == tag
    }
    pairs_pass: list[tuple[bool, bool]] = []
    pairs_ord: list[tuple[int, int]] = []
    conf: dict[str, dict[str, int]] = {}
    rated = 0
    with open(sheet, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            h = (r.get("human_overall (0-3)") or "").strip()
            if not h:
                continue
            key = (r["item_id"], int(r.get("repeat") or 1))
            v = judged.get(key)
            if v is None:
                continue
            rated += 1
            ho = int(h)
            jo = int(v.get("overall", 0))
            pairs_ord.append((jo, ho))
            pairs_pass.append((jo >= 2, ho >= 2))
            conf.setdefault(str(jo), {}).setdefault(str(ho), 0)
            conf[str(jo)][str(ho)] += 1
    if not rated:
        return {"n": 0}
    n = len(pairs_pass)
    judge_pass_human_fail = sum(1 for j, h in pairs_pass if j and not h)
    judge_fail_human_pass = sum(1 for j, h in pairs_pass if not j and h)
    human_fail = sum(1 for _, h in pairs_pass if not h)
    human_pass = n - human_fail
    kappa = stats.cohen_kappa(pairs_pass)
    return {
        "n": n,
        "percent_agreement": sum(1 for j, h in pairs_pass if j == h) / n,
        "kappa_pass": kappa,
        "kappa_pass_ci95": _kappa_ci(pairs_pass),
        "pabak_pass": stats.pabak(pairs_pass),
        "weighted_kappa_overall": stats.weighted_kappa(pairs_ord),
        "alpha_ordinal": stats.krippendorff_alpha_ordinal(pairs_ord),
        "judge_bias_mean": sum(j - h for j, h in pairs_ord) / n,
        "p_judge_pass_given_human_fail": judge_pass_human_fail / human_fail if human_fail else None,
        "p_judge_fail_given_human_pass": judge_fail_human_pass / human_pass if human_pass else None,
        "confusion_judge_x_human": conf,
    }


def _kappa_ci(
    pairs: list[tuple[bool, bool]], b: int = 1000, seed: int = 20260924
) -> list[float] | None:
    if len(pairs) < 5:
        return None
    rng = random.Random(seed)
    ks = []
    for _ in range(b):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        k = stats.cohen_kappa(sample)
        if k is not None:
            ks.append(k)
    ks.sort()
    return [ks[int(0.025 * (len(ks) - 1))], ks[int(0.975 * (len(ks) - 1))]] if ks else None
