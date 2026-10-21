"""Evaluation runner (plan §3.9): retrieval runs (two stages, deterministic), QA runs through
`QAService.answer` with `EvalOptions` (audit off, validator per config, cached answers), judge
runs, manifests and JSON artifacts under `docs/reports/eval/<run_id>/`."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as m
from app.evaluation import metrics_answer, metrics_retrieval, stats
from app.evaluation.cache import JsonCache
from app.evaluation.configs import EvalConfig
from app.evaluation.gold_schema import GoldItem
from app.evaluation.judge import JUDGE_PROMPT_VERSION, judge_item
from app.llm.client import LLMClient
from app.qa import prompts as P
from app.qa.models import EvalOptions, QARequest
from app.qa.service import QAService
from app.retrieval.retriever import RetrievalService, Retriever, SearchRequest

REPO = Path(__file__).resolve().parents[3]
EVAL_ROOT = REPO / "docs" / "reports" / "eval"


def make_run_id(config: str, split: str, repeat: int | None = None, tag: str | None = None) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    rid = f"{stamp}-{config}-{split}"
    if repeat:
        rid += f"-r{repeat}"
    if tag:
        rid += f"-{tag}"
    return rid


def _git(repo: Path) -> tuple[str | None, bool]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        return head, bool(dirty)
    except Exception:  # noqa: BLE001
        return None, False


def manifest(
    *,
    run_id: str,
    config: EvalConfig,
    split: str,
    items: list[GoldItem],
    gold_path: Path,
    settings: Settings,
    svc: RetrievalService,
    session: Session,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    commit, dirty = _git(REPO)
    active = session.execute(
        select(m.IndexVersion).where(m.IndexVersion.status == "active")
    ).scalar_one_or_none()
    return {
        "run_id": run_id,
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "git_commit": commit,
        "git_dirty": dirty,
        "gold_file": str(gold_path.relative_to(REPO))
        if gold_path.is_relative_to(REPO)
        else str(gold_path),
        "gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest()
        if gold_path.exists()
        else None,
        "split": split,
        "n_items": len(items),
        "tiers": sorted({it.tier for it in items}),
        "config": config.model_dump(),
        "index_label": svc.index_label,
        "document_hashes": (active.document_hashes if active else {}),
        "embedding_model": (active.embedding_model if active else None),
        "prompt_version": P.PROMPT_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "answer_model": config.model or settings.gemini_answer_model,
        "answer_thinking": config.thinking_level or settings.gemini_answer_thinking,
        "understanding_model": settings.gemini_understanding_model,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        **(extra or {}),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# ---------------------------------------------------------------- retrieval


def _domains_of(session: Session, chunk_ids: set[str]) -> dict[str, str]:
    if not chunk_ids:
        return {}
    rows = session.execute(
        select(
            m.EvidenceChunk.chunk_id, m.EvidenceChunk.domain, m.EvidenceChunk.citation_label
        ).where(m.EvidenceChunk.chunk_id.in_(chunk_ids))
    ).all()
    return {cid: dom for cid, dom, _ in rows}, {cid: lbl for cid, _, lbl in rows}  # type: ignore[return-value]


def run_retrieval(
    items: list[GoldItem],
    config: EvalConfig,
    session: Session,
    svc: RetrievalService,
    *,
    ks: tuple[int, ...] = (5, 10, 20),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    retriever = Retriever(session, svc)
    rows: list[dict[str, Any]] = []
    routing: list[dict[str, Any]] = []
    for it in items:
        ro = config.retrieval.model_dump()
        ro.pop("k", None)
        res = retriever.search(
            SearchRequest(
                question=it.question,
                as_of_date=it.as_of_date,
                k=config.k,
                return_ranking=True,
                **ro,
            )
        )
        ids = {c.chunk_id for c in res.ranking} | {e.chunk_id for e in res.evidence}
        domains, labels = _domains_of(session, ids)
        stage_a = [
            (c.chunk_id, labels.get(c.chunk_id), domains.get(c.chunk_id)) for c in res.ranking
        ]
        stage_b = [(e.chunk_id, e.citation_label, e.domain) for e in res.evidence]
        ra = metrics_retrieval.score_list(stage_a, it, ks=ks, stage="A")
        rb = metrics_retrieval.score_list(stage_b, it, ks=ks, stage="B")
        for r in (ra, rb):
            r.update(
                {
                    "tier": it.tier,
                    "stratum": it.stratum,
                    "domain": it.domain,
                    "intent": it.intent,
                    "difficulty": it.difficulty,
                    "cluster_id": it.cluster_id,
                    "trap_type": it.trap_type,
                    "latency_ms": res.latency_ms.get("total"),
                }
            )
        ra["top"] = [c.chunk_id for c in res.ranking[:10]]
        if res.rerank is not None:
            # LLM latency as measured when the grades were produced (also for cache hits)
            ra["rerank"] = res.rerank
            ra["top_grades"] = [c.rerank_grade for c in res.ranking[:10]]
        # per-item audit: a silent fallback (embedding API down, graph unreachable) must be
        # visible in the run, not only in lower scores
        ra["warnings"] = list(res.warnings)
        ra["stage_ms"] = dict(res.latency_ms)
        if res.ppr is not None:
            ra["ppr"] = res.ppr
        rb["pack"] = [e.chunk_id for e in res.evidence]
        rb["structured_hits"] = len(res.structured_hits)
        rows += [ra, rb]
        qu = res.understanding
        routing.append(
            {
                "item_id": it.id,
                "tier": it.tier,
                "stratum": it.stratum,
                "gold_domain": it.domain,
                "gold_intent": it.intent,
                "domain": qu.domain,
                "intent": qu.intent,
                "policy": qu.policy,
                "needs_llm": qu.needs_llm,
                "routing_strict": float(qu.domain == it.domain),
                "routing_lenient": float(
                    qu.domain == it.domain
                    or (it.domain in ("wastewater", "emission") and qu.domain == "cross_domain")
                ),
                "intent_ok": float(qu.intent == it.intent),
                "abstain_policy_ok": float(bool(qu.policy) or qu.domain == "out_of_scope")
                if it.must_abstain
                else None,
            }
        )
    return rows, routing


def summarize_retrieval(
    rows: list[dict[str, Any]], routing: list[dict[str, Any]], items: dict[str, GoldItem]
) -> dict[str, Any]:
    out: dict[str, Any] = {"stages": {}}
    for stage in ("A", "B"):
        srows = [r for r in rows if r["stage"] == stage and r.get("applicable")]
        metrics = defaultdict(list)
        clusters = []
        for r in srows:
            clusters.append(r.get("cluster_id") or r["item_id"])
            for k, v in r.items():
                if (
                    k.startswith(
                        (
                            "hit",
                            "recall",
                            "precision",
                            "ndcg",
                            "mrr",
                            "completeness",
                            "cross_domain",
                        )
                    )
                    and v is not None
                ):
                    metrics[k].append(float(v))
        summ = {
            k: stats.summarize_metric(v, clusters=clusters if len(v) == len(clusters) else None)
            for k, v in metrics.items()
        }
        summ["n"] = len(srows)
        summ["by_stratum"] = metrics_retrieval.aggregate(srows, "stratum", items)
        summ["by_intent"] = metrics_retrieval.aggregate(srows, "intent", items)
        summ["by_difficulty"] = metrics_retrieval.aggregate(srows, "difficulty", items)
        summ["by_tier"] = metrics_retrieval.aggregate(srows, "tier", items)
        out["stages"][stage] = summ
    a_rows = [r for r in rows if r["stage"] == "A"]
    out["degraded"] = {
        "vector_unavailable": sum(
            any("vector search unavailable" in w for w in r.get("warnings", [])) for r in a_rows
        ),
        "ppr_error": sum(1 for r in a_rows if (r.get("ppr") or {}).get("error")),
        "rerank_error": sum(1 for r in a_rows if (r.get("rerank") or {}).get("error")),
    }
    ppr_ms = sorted(r["stage_ms"]["ppr"] for r in a_rows if "ppr" in r.get("stage_ms", {}))
    if ppr_ms:
        out["ppr_latency_ms"] = {
            "p50": ppr_ms[len(ppr_ms) // 2],
            "p95": ppr_ms[max(0, int(len(ppr_ms) * 0.95) - 1)],
        }
    rr = [r["rerank"] for r in rows if r["stage"] == "A" and r.get("rerank")]
    if rr:
        lat = sorted(x.get("latency_ms", 0) for x in rr if not x.get("error"))
        out["rerank"] = {
            "n": len(rr),
            "model": rr[0].get("model"),
            "errors": sum(1 for x in rr if x.get("error")),
            "cached": sum(1 for x in rr if x.get("cached")),
            "latency_ms": {
                "p50": lat[len(lat) // 2],
                "p95": lat[min(len(lat) - 1, int(0.95 * len(lat)))],
                "max": lat[-1],
            }
            if lat
            else None,
            "prompt_tokens": sum(x.get("prompt_tokens", 0) for x in rr),
            "output_tokens": sum(x.get("output_tokens", 0) for x in rr),
        }
    conf: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    conf_i: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in routing:
        conf[r["gold_domain"]][r["domain"]] += 1
        conf_i[r["gold_intent"]][r["intent"]] += 1
    out["routing"] = {
        "n": len(routing),
        "routing_strict": stats.summarize_metric([r["routing_strict"] for r in routing]),
        "routing_lenient": stats.summarize_metric([r["routing_lenient"] for r in routing]),
        "intent_ok": stats.summarize_metric([r["intent_ok"] for r in routing]),
        "abstain_policy_ok": stats.summarize_metric([r["abstain_policy_ok"] for r in routing]),
        "confusion_domain": {k: dict(v) for k, v in conf.items()},
        "confusion_intent": {k: dict(v) for k, v in conf_i.items()},
    }
    return out


# ---------------------------------------------------------------- qa


def run_qa(
    items: list[GoldItem],
    config: EvalConfig,
    session: Session,
    svc: RetrievalService,
    llm: LLMClient,
    *,
    settings: Settings,
    cache: JsonCache | None,
    repeat: int = 1,
    audit: bool = False,
    run_id: str,
    warmup: bool = False,
    progress: Any = None,
) -> list[dict[str, Any]]:
    qa = QAService(session, svc, llm, settings)
    opts = EvalOptions(
        retrieval=config.retrieval,
        model=config.model,
        thinking_level=config.thinking_level,
        thinking_override_all=config.thinking_override_all,
        validator_mode=config.validator_mode,
        understanding_llm=config.understanding_llm,
        graph_verify=config.graph_verify,
        audit=audit,
        return_ranking=True,
    )
    if warmup and items:
        qa.answer(
            QARequest(question=items[0].question, as_of_date=items[0].as_of_date, k=config.k),
            options=opts.model_copy(update={"audit": False}),
        )
    rows: list[dict[str, Any]] = []
    for i, it in enumerate(items, start=1):
        key = JsonCache.key(
            config=config.model_dump(),
            prompt=P.PROMPT_VERSION,
            q=it.question,
            as_of=str(it.as_of_date),
            k=config.k,
            repeat=repeat,
            model=config.model or settings.gemini_answer_model,
            thinking=config.thinking_level or settings.gemini_answer_thinking,
        )
        cached = cache.get("answers", key) if cache is not None else None
        regen: list[str] = []
        if cached is not None:
            resp_d = cached["response"]
            regen = cached.get("regen_errors", [])
        else:

            def on_event(e: str, d: dict[str, Any], regen: list[str] = regen) -> None:
                if e == "stage" and d.get("stage") == "regenerating":
                    regen.extend(d.get("errors", []))

            resp = qa.answer(
                QARequest(question=it.question, as_of_date=it.as_of_date, k=config.k),
                on_event=on_event,
                query_id=None,
                client_id=None,
                client_key=f"eval:{run_id}"[:36],
                options=opts,
            )
            resp_d = resp.model_dump(mode="json")
            if cache is not None:
                cache.put("answers", key, {"response": resp_d, "regen_errors": regen})
        scored = metrics_answer.score_answer(it, resp_d, regen_errors=regen)
        scored.update(
            {
                "repeat": repeat,
                "cached": cached is not None,
                "query_id": resp_d.get("query_id"),
                "response": resp_d,
            }
        )
        rows.append(scored)
        if progress:
            progress(i, len(items), it.id, scored["status"])
    return rows


ANSWER_METRICS = (
    "citation_valid",
    "citation_precision",
    "citation_recall",
    "citation_complete",
    "nums_grounded",
    "nums_exact",
    "forbidden_hit",
    "abstain_correct",
    "abstain_tp",
    "abstain_fp",
    "abstain_fn",
    "abstain_tn",
    "warnings_ok",
    "warnings_spurious",
    "validator_ok",
    "first_pass",
    "regenerated",
    "fallback",
    "violation",
    "safe",
    "correct_safe",
    "unsafe",
    "routing_strict",
    "intent_ok",
    "cost_usd",
    "total_tokens",
    "latency_total_ms",
    "latency_retrieval_ms",
)


def summarize_qa(rows: list[dict[str, Any]], items: dict[str, GoldItem]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    clusters = [r.get("cluster_id") or r["item_id"] for r in rows]
    for mname in ANSWER_METRICS:
        vals = [r.get(mname) for r in rows]
        if all(v is None for v in vals):
            continue
        out[mname] = stats.summarize_metric(vals, clusters=clusters)
    tp = sum(r.get("abstain_tp", 0) for r in rows)
    fp = sum(r.get("abstain_fp", 0) for r in rows)
    fn = sum(r.get("abstain_fn", 0) for r in rows)
    tn = sum(r.get("abstain_tn", 0) for r in rows)
    out["abstention"] = {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else None,
        "over_abstain_rate": fp / (fp + tn) if fp + tn else None,
        "under_abstain_rate": fn / (tp + fn) if tp + fn else None,
        "by_path": _by_path(rows),
    }
    lat = sorted(r["latency_total_ms"] for r in rows if r.get("latency_total_ms"))
    if lat:
        out["latency_ms"] = {
            "p50": lat[len(lat) // 2],
            "p95": lat[min(len(lat) - 1, int(0.95 * (len(lat) - 1)))],
            "max": lat[-1],
        }
    out["by_status"] = dict(_count(rows, "status"))
    out["by_path"] = dict(_count(rows, "path"))
    out["validator_errors"] = dict(_count_list(rows, "validator_errors"))
    out["by_stratum"] = _group(rows, "stratum")
    out["by_tier"] = _group(rows, "tier")
    out["by_difficulty"] = _group(rows, "difficulty")
    out["by_trap_type"] = _group([r for r in rows if r.get("trap_type")], "trap_type")
    judged = [r for r in rows if r.get("judge") and not r["judge"].get("judge_error")]
    if judged:
        out["judge"] = {
            "n": len(judged),
            "pass_rate": stats.summarize_metric(
                [float(r["judge"].get("pass", False)) for r in judged]
            ),
            "overall_mean": stats.mean([float(r["judge"].get("overall", 0)) for r in judged]),
            "faithful_rate": stats.mean([float(r["judge"].get("faithful", False)) for r in judged]),
            "cost_usd": sum(float(r["judge"].get("cost_usd", 0) or 0) for r in judged),
        }
    out["cost_total_usd"] = sum(float(r.get("cost_usd", 0) or 0) for r in rows)
    return out


def _count(rows, key):
    c: dict[str, int] = defaultdict(int)
    for r in rows:
        c[str(r.get(key))] += 1
    return c


def _count_list(rows, key):
    c: dict[str, int] = defaultdict(int)
    for r in rows:
        for v in r.get(key) or []:
            c[str(v)] += 1
    return c


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    out = {}
    for g, lst in sorted(groups.items()):
        entry: dict[str, Any] = {"n": len(lst)}
        for mname in (
            "correct_safe",
            "safe",
            "citation_complete",
            "nums_exact",
            "abstain_correct",
            "violation",
            "routing_strict",
        ):
            vals = [r.get(mname) for r in lst if r.get(mname) is not None]
            if vals:
                k = int(sum(vals))
                entry[mname] = {
                    "mean": sum(vals) / len(vals),
                    "wilson95": list(stats.wilson_ci(k, len(vals))),
                    "n": len(vals),
                }
        out[g] = entry
    return out


def _by_path(rows):
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        for k in ("abstain_tp", "abstain_fp", "abstain_fn", "abstain_tn"):
            out[str(r.get("path"))][k] += int(r.get(k, 0))
    return {k: dict(v) for k, v in out.items()}


# ---------------------------------------------------------------- judge


def run_judge(
    run_dir: Path,
    items: dict[str, GoldItem],
    llm: LLMClient,
    *,
    model: str,
    thinking_level: str | None,
    cache: JsonCache | None,
    tag: str = "J1",
    progress: Any = None,
) -> list[dict[str, Any]]:
    qa_rows = read_jsonl(run_dir / "qa.jsonl")
    out: list[dict[str, Any]] = []
    for i, r in enumerate(qa_rows, start=1):
        it = items.get(r["item_id"])
        if it is None or r.get("path") == "policy" and not it.must_abstain:
            continue
        verdict = judge_item(
            llm, it, r["response"], cache=cache, model=model, thinking_level=thinking_level, tag=tag
        )
        verdict["item_id"] = it.id
        verdict["repeat"] = r.get("repeat", 1)
        out.append(verdict)
        if progress:
            progress(i, len(qa_rows), it.id, verdict.get("overall"))
    existing = [v for v in read_jsonl(run_dir / "judge.jsonl") if v.get("tag") != tag]
    write_jsonl(run_dir / "judge.jsonl", existing + out)
    if tag == "J1":
        by_key = {(v["item_id"], v.get("repeat", 1)): v for v in out}
        for r in qa_rows:
            v = by_key.get((r["item_id"], r.get("repeat", 1)))
            if v is not None:
                it = items[r["item_id"]]
                r["judge"] = {
                    k: v.get(k)
                    for k in (
                        "overall",
                        "pass",
                        "faithful",
                        "value_correct",
                        "column_or_condition_correct",
                        "conditions_complete",
                        "basis_complete",
                        "abstain_appropriate",
                        "warnings_ok",
                        "cost_usd",
                        "judge_error",
                    )
                }
                r.update(
                    metrics_answer.composite(
                        it,
                        r,
                        {"nums_grounded": r.get("nums_grounded", 1.0)},
                        r,
                        r,
                        {"violations": r.get("violations", [])},
                        r.get("nums_exact"),
                        r["judge"],
                        r["status"],
                    )
                )
        write_jsonl(run_dir / "qa.jsonl", qa_rows)
        summ = (
            json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            if (run_dir / "summary.json").exists()
            else {}
        )
        summ["qa"] = summarize_qa(qa_rows, items)
        (run_dir / "summary.json").write_text(
            json.dumps(summ, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    return out


def judge_agreement(run_dir: Path) -> dict[str, Any]:
    rows = read_jsonl(run_dir / "judge.jsonl")
    tags = sorted({r["tag"] for r in rows})
    if len(tags) < 2:
        return {"tags": tags}
    a = {(r["item_id"], r.get("repeat", 1)): r for r in rows if r["tag"] == tags[0]}
    b = {(r["item_id"], r.get("repeat", 1)): r for r in rows if r["tag"] == tags[1]}
    keys = sorted(set(a) & set(b))
    pairs_pass = [(bool(a[k].get("pass")), bool(b[k].get("pass"))) for k in keys]
    pairs_ord = [(int(a[k].get("overall", 0)), int(b[k].get("overall", 0))) for k in keys]
    return {
        "tags": tags[:2],
        "n": len(keys),
        "percent_agreement": sum(1 for x, y in pairs_pass if x == y) / len(keys) if keys else None,
        "kappa_pass": stats.cohen_kappa(pairs_pass),
        "pabak_pass": stats.pabak(pairs_pass),
        "weighted_kappa_overall": stats.weighted_kappa(pairs_ord),
        "alpha_ordinal": stats.krippendorff_alpha_ordinal(pairs_ord),
    }


# ---------------------------------------------------------------- artifacts


def save_run(
    run_dir: Path,
    *,
    manifest_d: dict[str, Any],
    config: EvalConfig,
    retrieval_rows: list[dict[str, Any]] | None,
    routing: list[dict[str, Any]] | None,
    qa_rows: list[dict[str, Any]] | None,
    items: dict[str, GoldItem],
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest_d, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (run_dir / "config.json").write_text(config.model_dump_json(indent=2), encoding="utf-8")
    summary: dict[str, Any] = {
        "run_id": manifest_d["run_id"],
        "config": config.name,
        "split": manifest_d["split"],
        "n_items": manifest_d["n_items"],
    }
    if retrieval_rows is not None:
        write_jsonl(run_dir / "retrieval.jsonl", retrieval_rows)
        write_jsonl(run_dir / "routing.jsonl", routing or [])
        summary["retrieval"] = summarize_retrieval(retrieval_rows, routing or [], items)
    if qa_rows is not None:
        write_jsonl(run_dir / "qa.jsonl", qa_rows)
        summary["qa"] = summarize_qa(qa_rows, items)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return summary


def load_summary(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
