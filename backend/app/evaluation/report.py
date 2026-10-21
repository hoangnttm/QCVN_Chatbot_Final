"""Markdown reports for runs and paired comparisons (plan §3.10)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.evaluation import stats
from app.evaluation.configs import FAMILIES
from app.evaluation.runner import load_summary, read_jsonl


def _f(v: Any, pct: bool = True) -> str:
    if v is None:
        return "–"
    if isinstance(v, dict):
        m = v.get("mean")
        if m is None:
            return "–"
        ci = v.get("wilson95") or v.get("ci95")
        s = f"{m:.1%}" if pct else f"{m:.3f}"
        if ci:
            s += f" [{ci[0]:.1%}–{ci[1]:.1%}]" if pct else f" [{ci[0]:.3f}–{ci[1]:.3f}]"
        return s
    if isinstance(v, float):
        return f"{v:.1%}" if pct else f"{v:.3f}"
    return str(v)


def render_run(run_dir: Path) -> str:
    s = load_summary(run_dir)
    man = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    lines = [f"# Run `{s['run_id']}` — {s['config']} ({s['split']}, n = {s['n_items']})", ""]
    lines.append(
        f"Commit `{man.get('git_commit')}`{' (dirty)' if man.get('git_dirty') else ''} · index `{man.get('index_label')}` · prompt `{man.get('prompt_version')}` · model `{man.get('answer_model')}` @ {man.get('answer_thinking')} · gold sha `{(man.get('gold_sha256') or '')[:12]}`"  # noqa: E501
    )
    lines.append("")
    if "retrieval" in s:
        r = s["retrieval"]
        lines += [
            "## Retrieval",
            "",
            "| Metric | Stage A (ranker) | Stage B (pack) |",
            "|---|---|---|",
        ]
        for mname in (
            "hit@5",
            "hit@10",
            "hit_must@10",
            "recall_must@10",
            "recall_all@10",
            "precision@10",
            "mrr",
            "ndcg@10",
            "ndcg@5",
            "completeness",
            "cross_domain_share",
        ):
            a = r["stages"]["A"].get(mname)
            b = r["stages"]["B"].get(mname)
            if a or b:
                pct = not mname.startswith(("ndcg", "mrr"))
                lines.append(f"| {mname} | {_f(a, pct)} | {_f(b, pct)} |")
        rt = r["routing"]
        lines += [
            "",
            f"Routing strict {_f(rt['routing_strict'])} · lenient {_f(rt['routing_lenient'])} · intent {_f(rt['intent_ok'])} · abstain policy {_f(rt['abstain_policy_ok'])}",  # noqa: E501
            "",
        ]
        rr = r.get("rerank")
        if rr:
            lat = rr.get("latency_ms") or {}
            lines += [
                f"Rerank {rr['model']}: n={rr['n']} errors={rr['errors']} cached={rr['cached']} · latency p50 {lat.get('p50')} ms · p95 {lat.get('p95')} ms · tokens in/out {rr['prompt_tokens']}/{rr['output_tokens']}",  # noqa: E501
                "",
            ]
        lines += ["Confusion (gold → predicted domain):", ""]
        for g, row in rt["confusion_domain"].items():
            lines.append(f"- {g}: {row}")
        lines += [
            "",
            "### Stage B by stratum",
            "",
            "| Stratum | n | hit@10 | recall_must@10 | ndcg@10 | completeness |",
            "|---|---|---|---|---|---|",
        ]
        for st, v in sorted(r["stages"]["B"]["by_stratum"].items()):
            lines.append(
                f"| {st} | {v['n']} | {_f(v.get('hit@10'))} | {_f(v.get('recall_must@10'))} | {_f(v.get('ndcg@10'), False)} | {_f(v.get('completeness'))} |"  # noqa: E501
            )
    if "qa" in s:
        q = s["qa"]
        lines += ["", "## Answer quality", "", "| Metric | Value [95 % CI] |", "|---|---|"]
        for mname in (
            "correct_safe",
            "safe",
            "unsafe",
            "citation_valid",
            "citation_precision",
            "citation_recall",
            "citation_complete",
            "nums_grounded",
            "nums_exact",
            "forbidden_hit",
            "abstain_correct",
            "warnings_ok",
            "warnings_spurious",
            "first_pass",
            "regenerated",
            "fallback",
            "violation",
            "routing_strict",
            "intent_ok",
        ):
            if mname in q:
                lines.append(f"| {mname} | {_f(q[mname])} |")
        ab = q.get("abstention", {})
        lines += [
            "",
            f"Abstention: TP {ab.get('tp')} FP {ab.get('fp')} FN {ab.get('fn')} TN {ab.get('tn')} · precision {_f(ab.get('precision'))} · recall {_f(ab.get('recall'))} · F1 {_f(ab.get('f1'))} · over-abstain {_f(ab.get('over_abstain_rate'))} · under-abstain {_f(ab.get('under_abstain_rate'))}",  # noqa: E501
        ]
        lines.append(f"By path: {ab.get('by_path')}")
        lat = q.get("latency_ms", {})
        lines.append(
            f"Latency p50 {lat.get('p50')} ms · p95 {lat.get('p95')} ms · max {lat.get('max')} ms · cost {q.get('cost_total_usd', 0):.4f} USD · status {q.get('by_status')} · path {q.get('by_path')}"  # noqa: E501
        )
        if q.get("validator_errors"):
            lines.append(f"Validator errors: {q['validator_errors']}")
        if q.get("judge"):
            j = q["judge"]
            lines.append(
                f"Judge: n {j['n']} · pass {_f(j['pass_rate'])} · overall mean {j['overall_mean']:.2f} · faithful {_f(j['faithful_rate'])} · cost {j['cost_usd']:.3f} USD"  # noqa: E501
            )
        lines += [
            "",
            "### By stratum",
            "",
            "| Stratum | n | correct_safe | citation_complete | nums_exact | abstain_correct |",
            "|---|---|---|---|---|---|",
        ]
        for st, v in q.get("by_stratum", {}).items():
            lines.append(
                f"| {st} | {v['n']} | {_f(v.get('correct_safe'))} | {_f(v.get('citation_complete'))} | {_f(v.get('nums_exact'))} | {_f(v.get('abstain_correct'))} |"  # noqa: E501
            )
        if q.get("by_trap_type"):
            lines += [
                "",
                "### Robustness by trap type",
                "",
                "| trap_type | n | violation | correct_safe |",
                "|---|---|---|---|",
            ]
            for st, v in q["by_trap_type"].items():
                lines.append(
                    f"| {st} | {v['n']} | {_f(v.get('violation'))} | {_f(v.get('correct_safe'))} |"
                )
    return "\n".join(lines) + "\n"


def _per_item(run_dir: Path, metric: str, stage: str | None) -> dict[str, float]:
    if stage:
        rows = [
            r
            for r in read_jsonl(run_dir / "retrieval.jsonl")
            if r["stage"] == stage and r.get("applicable") and r.get(metric) is not None
        ]
        return {r["item_id"]: float(r[metric]) for r in rows}
    rows = [
        r
        for r in read_jsonl(run_dir / "qa.jsonl")
        if r.get(metric) is not None and r.get("repeat", 1) == 1
    ]
    return {r["item_id"]: float(r[metric]) for r in rows}


def compare(
    run_a: Path, run_b: Path, metric: str, stage: str | None = None, seed: int = 20260924
) -> dict[str, Any]:
    a = _per_item(run_a, metric, stage)
    b = _per_item(run_b, metric, stage)
    keys = sorted(set(a) & set(b))
    if not keys:
        return {"n": 0}
    xa = [a[k] for k in keys]
    xb = [b[k] for k in keys]
    diffs = [xa[i] - xb[i] for i in range(len(keys))]
    out: dict[str, Any] = {
        "metric": metric,
        "stage": stage,
        "n": len(keys),
        "mean_a": sum(xa) / len(xa),
        "mean_b": sum(xb) / len(xb),
        "delta": sum(diffs) / len(diffs),
        "delta_ci95": list(stats.paired_bootstrap(diffs, seed=seed)),
    }
    if all(v in (0.0, 1.0) for v in xa + xb):
        bb = sum(1 for i in range(len(keys)) if xa[i] == 1 and xb[i] == 0)
        cc = sum(1 for i in range(len(keys)) if xa[i] == 0 and xb[i] == 1)
        out.update(
            {
                "test": "mcnemar_exact",
                "discordant_b": bb,
                "discordant_c": cc,
                "p": stats.mcnemar_exact(bb, cc),
                "odds_ratio": (bb / cc) if cc else None,
                "cohens_h": stats.cohens_h(out["mean_a"], out["mean_b"]),
            }
        )
    else:
        out.update(
            {
                "test": "sign_flip_permutation",
                "p": stats.permutation_p(diffs, seed=seed),
                "cohens_dz": stats.cohens_dz(diffs),
            }
        )
    return out


def family_table(runs: dict[str, Path], family: str) -> dict[str, Any]:
    spec = FAMILIES[family]
    base = runs.get(str(spec["baseline"]))
    if base is None:
        return {"error": f"baseline {spec['baseline']} run missing"}
    results = {}
    for c in spec["contrasts"]:  # type: ignore[union-attr]
        if c in runs:
            results[c] = compare(base, runs[c], str(spec["metric"]), spec.get("stage"))  # type: ignore[arg-type]
    pvals = {c: r["p"] for c, r in results.items() if "p" in r}
    adj = stats.holm(pvals)
    for c, r in results.items():
        if c in adj:
            r.update({"p_holm": adj[c]["p_holm"], "reject": adj[c]["reject"]})
    return {
        "family": family,
        "metric": spec["metric"],
        "baseline": spec["baseline"],
        "contrasts": results,
    }


def render_compare(res: dict[str, Any]) -> str:
    if "contrasts" in res:
        lines = [
            f"## {res['family']} — {res['metric']} vs {res['baseline']} (Holm)",
            "",
            "| Contrast | n | baseline | contrast | Δ [CI] | test | p | p_holm | reject |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for c, r in res["contrasts"].items():
            if r.get("n", 0) == 0:
                lines.append(f"| {c} | 0 | – | – | – | – | – | – | – |")
                continue
            lines.append(
                f"| {c} | {r['n']} | {r['mean_a']:.3f} | {r['mean_b']:.3f} | {r['delta']:+.3f} [{r['delta_ci95'][0]:+.3f}, {r['delta_ci95'][1]:+.3f}] | {r['test']} | {r['p']:.4f} | {r.get('p_holm', float('nan')):.4f} | {r.get('reject')} |"  # noqa: E501
            )
        return "\n".join(lines) + "\n"
    r = res
    if r.get("n", 0) == 0:
        return "no common items\n"
    return (
        f"{r['metric']}{' stage ' + r['stage'] if r.get('stage') else ''}: n={r['n']} A={r['mean_a']:.3f} B={r['mean_b']:.3f} "  # noqa: E501
        f"Δ={r['delta']:+.3f} [{r['delta_ci95'][0]:+.3f}, {r['delta_ci95'][1]:+.3f}] {r['test']} p={r['p']:.4f}\n"
    )
