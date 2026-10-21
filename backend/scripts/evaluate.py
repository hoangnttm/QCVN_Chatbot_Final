"""Evaluation CLI (plan §3.9). Runs never write to the production audit tables (audit off).

python scripts/evaluate.py gold-check  [--tier A|B|C|all]
python scripts/evaluate.py retrieval   --config C4 [--tier all] [--split dev] [--limit N] [--ids ...] [--gate]
python scripts/evaluate.py qa          --config C4 [--tier B] [--split dev] [--repeats 1] [--limit N]
                                       [--no-cache] [--warmup] [--confirm-test] [--llm gemini|fake]
python scripts/evaluate.py judge       --run RUN_ID [--judge-model M] [--judge-thinking high] [--tag J1]
python scripts/evaluate.py ablation    --configs C0,C1,C2,C3,C4 [--split dev] [--tier all]
python scripts/evaluate.py human-sheet --run RUN_ID [--frac 0.2] [--seed S]
python scripts/evaluate.py agreement   --run RUN_ID --sheet path.csv
python scripts/evaluate.py compare     --runs RUN_A,RUN_B --metric ndcg@10 [--stage A] | --family RQ1
python scripts/evaluate.py report      --run RUN_ID [--runs ...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings, get_settings  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.evaluation import human, report, runner  # noqa: E402
from app.evaluation.cache import JsonCache  # noqa: E402
from app.evaluation.configs import CI_GATES, CONFIGS  # noqa: E402
from app.evaluation.gold_resolver import DerivedResolver  # noqa: E402
from app.evaluation.gold_schema import GoldItem, load_gold, load_strata  # noqa: E402
from app.evaluation.gold_source import RawSource  # noqa: E402
from app.evaluation.gold_validate import validate  # noqa: E402
from app.llm.client import FakeLLM, get_llm  # noqa: E402
from app.retrieval.retriever import RetrievalService  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GOLD_DIR = REPO / "data" / "gold"
TIER_FILES = {
    "A": GOLD_DIR / "gold_tables_v3.jsonl",
    "B": GOLD_DIR / "gold_qa_v3.jsonl",
    "C": GOLD_DIR / "gold_natural_v3.jsonl",
    "M": GOLD_DIR / "gold_multihop_v3.jsonl",
}


def parse_tiers(tier: str) -> list[str]:
    """`all` = A,B,C (tier M is opt-in so earlier baselines stay comparable); else a comma list."""
    tiers = ["A", "B", "C"] if tier == "all" else [t.strip().upper() for t in tier.split(",")]
    bad = [t for t in tiers if t not in TIER_FILES]
    if bad:
        raise SystemExit(f"unknown tier(s) {bad}; use all or a comma list of {sorted(TIER_FILES)}")
    return tiers


TEST_LOG = GOLD_DIR / "test_runs.log"


def _print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))


def _items(
    tier: str,
    split: str | None,
    limit: int | None,
    ids: list[str] | None,
    ci_eligible: bool = False,
) -> tuple[list[GoldItem], Path]:
    items: list[GoldItem] = []
    tiers = parse_tiers(tier)
    path = TIER_FILES[tiers[0]]
    for t in tiers:
        p = TIER_FILES[t]
        if p.exists():
            items += load_gold(p, tier=t, split=split)
    if ids:
        items = [it for it in items if it.id in set(ids)]
    if ci_eligible:
        items = [it for it in items if _is_ci_eligible(it)]
    if limit:
        items = items[:limit]
    return items, path


CI_DOCS = ("TT_06_2025", "QCVN_40_2025", "QCVN_19_2024", "QCVN40_2025", "QCVN19_2024")


def _is_ci_eligible(it: GoldItem) -> bool:
    """Every relevant chunk (grade >= 1) belongs to a Markdown document (no docling in CI)."""
    for r in it.relevant:
        if r.grade < 1:
            continue
        body = r.chunk_id.removeprefix("chunk:")
        if not any(body.startswith(d) for d in CI_DOCS):
            return False
    return True


def _service(settings: Settings, cache: JsonCache | None = None) -> tuple[RetrievalService, object]:
    maker = get_sessionmaker(settings)
    svc = RetrievalService(settings)
    svc.rerank_cache = cache  # LLM rerank grades (configs C9/C10/C4-rr)
    session = maker()
    svc.ensure(session)
    return svc, session


def _confirm_test(a: argparse.Namespace, purpose: str) -> bool:
    if a.split != "test":
        return True
    if not getattr(a, "confirm_test", False):
        _print("refusing to touch split=test without --confirm-test (blind test policy)")
        return False
    TEST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TEST_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{dt.datetime.now().isoformat(timespec='seconds')}\t{purpose}\n")
    return True


def _cache(a: argparse.Namespace, settings: Settings) -> JsonCache | None:
    if getattr(a, "no_cache", False):
        return None
    return JsonCache(settings.data_dir / "derived" / "eval_cache")


# ---------------------------------------------------------------- commands


def cmd_gold_check(a: argparse.Namespace) -> int:
    settings = get_settings()
    items, _ = _items(a.tier, None, None, None, getattr(a, "ci_eligible", False))
    strata = load_strata(GOLD_DIR / "strata_v3.yaml")
    rep = validate(items, DerivedResolver(settings), RawSource(settings), strata)
    _print(f"items={rep.n_items} errors={len(rep.errors)} warnings={len(rep.warnings)}")
    for f in rep.errors[:50]:
        _print(f"  {f}")
    return 0 if rep.ok else 1


def _with_overrides(cfg, sets: list[str] | None):
    """`--set ppr_weight=0.5 --set ppr_damping=0.7` → a copy of the config named
    `C11+ppr_weight=0.5+ppr_damping=0.7` (dev sensitivity grids; never change a config in place)."""
    if not sets:
        return cfg
    upd: dict[str, object] = {}
    for kv in sets:
        k, _, v = kv.partition("=")
        if k not in type(cfg.retrieval).model_fields:
            raise SystemExit(f"unknown retrieval option {k!r}")
        val: object = v
        if v.lower() in ("true", "false"):
            val = v.lower() == "true"
        elif v.lower() == "none":
            val = None
        else:
            try:
                val = int(v)
            except ValueError:
                try:
                    val = float(v)
                except ValueError:
                    val = v
        upd[k] = val
    retrieval = type(cfg.retrieval).model_validate(cfg.retrieval.model_dump() | upd)
    name = cfg.name + "".join(f"+{k}={upd[k]}" for k in upd)
    return cfg.model_copy(update={"name": name, "retrieval": retrieval})


def cmd_retrieval(a: argparse.Namespace) -> int:
    settings = get_settings()
    cfg = _with_overrides(CONFIGS[a.config], getattr(a, "set", None))
    items, gold_path = _items(a.tier, a.split, a.limit, a.ids, getattr(a, "ci_eligible", False))
    if not items:
        _print("no items")
        return 1
    if not _confirm_test(a, f"retrieval {cfg.name}"):
        return 2
    svc, session = _service(settings, _cache(a, settings))
    rows, routing = runner.run_retrieval(items, cfg, session, svc)
    run_id = a.run_id or runner.make_run_id(cfg.name, a.split or "all")
    man = runner.manifest(
        run_id=run_id,
        config=cfg,
        split=a.split or "all",
        items=items,
        gold_path=gold_path,
        settings=settings,
        svc=svc,
        session=session,
        extra={"level": "retrieval", "tier": a.tier},
    )
    by_id = {it.id: it for it in items}
    summ = runner.save_run(
        runner.EVAL_ROOT / run_id,
        manifest_d=man,
        config=cfg,
        retrieval_rows=rows,
        routing=routing,
        qa_rows=None,
        items=by_id,
    )
    (runner.EVAL_ROOT / run_id / "report.md").write_text(
        report.render_run(runner.EVAL_ROOT / run_id), encoding="utf-8"
    )
    r = summ["retrieval"]
    b = r["stages"]["B"]
    _print(
        f"{run_id}: n={r['stages']['B']['n']} hit@10={b['hit@10']['mean']:.3f} recall_must@10={b.get('recall_must@10', {}).get('mean', float('nan')):.3f} ndcg@10(A)={r['stages']['A']['ndcg@10']['mean']:.3f} routing_strict={r['routing']['routing_strict']['mean']:.3f}"  # noqa: E501
    )
    deg = {k: v for k, v in r.get("degraded", {}).items() if v}
    if deg:
        _print(f"  DEGRADED items: {deg} — the run is not comparable, rerun it")
    if "ppr_latency_ms" in r:
        _print(f"  ppr latency ms: {r['ppr_latency_ms']}")
    if a.gate:
        failed = []
        if b["hit@10"]["mean"] < CI_GATES["hit@10"]:
            failed.append(f"hit@10 {b['hit@10']['mean']:.3f} < {CI_GATES['hit@10']}")
        if r["routing"]["routing_strict"]["mean"] < CI_GATES["routing_strict"]:
            failed.append(
                f"routing_strict {r['routing']['routing_strict']['mean']:.3f} < {CI_GATES['routing_strict']}"
            )
        if failed:
            _print("GATE FAILED: " + "; ".join(failed))
            return 1
        _print("GATE OK")
    return 0


def cmd_qa(a: argparse.Namespace) -> int:
    settings = get_settings()
    cfg = CONFIGS[a.config]
    if a.model:
        cfg = cfg.model_copy(update={"model": a.model})
    items, gold_path = _items(a.tier, a.split, a.limit, a.ids)
    if not items:
        _print("no items")
        return 1
    if not _confirm_test(a, f"qa {cfg.name} repeats={a.repeats}"):
        return 2
    svc, session = _service(settings, _cache(a, settings))
    llm = (
        FakeLLM(
            [
                {
                    "conclusion": "Không đủ căn cứ (fake LLM).",
                    "basis": [],
                    "conditions": [],
                    "citations": [],
                    "missing_information": [],
                    "warnings": [],
                    "abstained": True,
                    "abstain_reason": "fake",
                }
            ]
        )
        if a.llm == "fake"
        else get_llm(settings)
    )
    cache = _cache(a, settings)
    by_id = {it.id: it for it in items}
    for rep in range(1, (a.repeats or cfg.repeats or 1) + 1):
        run_id = runner.make_run_id(
            cfg.name, a.split or "all", repeat=rep if (a.repeats or cfg.repeats) > 1 else None
        )
        rows = runner.run_qa(
            items,
            cfg,
            session,
            svc,
            llm,
            settings=settings,
            cache=cache,
            repeat=rep,
            run_id=run_id,
            warmup=a.warmup and rep == 1,
            progress=lambda i, n, iid, st: _print(f"  [{i}/{n}] {iid} {st}") if a.verbose else None,
        )
        ret_rows, routing = runner.run_retrieval(items, cfg, session, svc)
        man = runner.manifest(
            run_id=run_id,
            config=cfg,
            split=a.split or "all",
            items=items,
            gold_path=gold_path,
            settings=settings,
            svc=svc,
            session=session,
            extra={
                "level": "qa",
                "repeat": rep,
                "tier": a.tier,
                "llm": a.llm,
                "cache_hits": cache.hits if cache else 0,
            },
        )
        summ = runner.save_run(
            runner.EVAL_ROOT / run_id,
            manifest_d=man,
            config=cfg,
            retrieval_rows=ret_rows,
            routing=routing,
            qa_rows=rows,
            items=by_id,
        )
        (runner.EVAL_ROOT / run_id / "report.md").write_text(
            report.render_run(runner.EVAL_ROOT / run_id), encoding="utf-8"
        )
        q = summ["qa"]
        _print(
            f"{run_id}: n={q['n']} correct_safe={q['correct_safe']['mean']:.3f} citation_valid={q['citation_valid']['mean']:.3f} nums_grounded={q['nums_grounded']['mean']:.3f} abstain_correct={q['abstain_correct']['mean']:.3f} cost={q['cost_total_usd']:.4f} USD status={q['by_status']}"  # noqa: E501
        )
    return 0


def cmd_judge(a: argparse.Namespace) -> int:
    settings = get_settings()
    run_dir = runner.EVAL_ROOT / a.run
    if not run_dir.exists():
        _print(f"run not found: {run_dir}")
        return 1
    items, _ = _items("all", None, None, None)
    by_id = {it.id: it for it in items}
    llm = (
        FakeLLM(
            [
                {
                    "value_correct": True,
                    "column_or_condition_correct": True,
                    "conditions_complete": True,
                    "basis_complete": "complete",
                    "faithful": True,
                    "unsupported_claims": [],
                    "abstain_appropriate": "not_applicable",
                    "warnings_ok": True,
                    "overall": 3,
                    "rationale": "fake",
                }
            ]
        )
        if a.llm == "fake"
        else get_llm(settings)
    )
    model = a.judge_model or settings.gemini_judge_model
    thinking = a.judge_thinking or settings.gemini_judge_thinking
    out = runner.run_judge(
        run_dir,
        by_id,
        llm,
        model=model,
        thinking_level=thinking,
        cache=_cache(a, settings),
        tag=a.tag,
        progress=lambda i, n, iid, o: (
            _print(f"  [{i}/{n}] {iid} overall={o}") if a.verbose else None
        ),
    )
    passed = sum(1 for v in out if v.get("pass"))
    cost = sum(float(v.get("cost_usd") or 0) for v in out if not v.get("cached"))
    _print(f"{a.run} {a.tag}: judged={len(out)} pass={passed} cost={cost:.3f} USD (uncached)")
    agree = runner.judge_agreement(run_dir)
    if agree.get("n"):
        _print(
            f"judge agreement {agree['tags']}: κ={agree['kappa_pass']:.3f} weighted κ={agree['weighted_kappa_overall']:.3f} α={agree['alpha_ordinal']:.3f}"  # noqa: E501
        )
        (run_dir / "judge_agreement.json").write_text(
            json.dumps(agree, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (run_dir / "report.md").write_text(report.render_run(run_dir), encoding="utf-8")
    return 0


def cmd_ablation(a: argparse.Namespace) -> int:
    names = a.configs.split(",")
    run_ids = []
    for name in names:
        cfg = CONFIGS[name]
        ns = argparse.Namespace(
            config=name,
            tier=a.tier,
            split=a.split,
            limit=a.limit,
            ids=None,
            gate=False,
            run_id=None,
            confirm_test=a.confirm_test,
        )
        if cfg.level == "qa" and a.level == "auto":
            ns.level = "retrieval"
        rc = cmd_retrieval(ns)
        if rc:
            return rc
        run_ids.append(sorted(runner.EVAL_ROOT.glob(f"*-{name}-{a.split or 'all'}"))[-1].name)
    _print("runs: " + ", ".join(run_ids))
    return 0


def cmd_human_sheet(a: argparse.Namespace) -> int:
    run_dir = runner.EVAL_ROOT / a.run
    rows = human.sample_for_human(run_dir, frac=a.frac, seed=a.seed, tag=a.tag)
    out = run_dir / "human_sheet.csv"
    n = human.export_sheet(run_dir, rows, out)
    _print(f"{n} items -> {out}")
    return 0


def cmd_agreement(a: argparse.Namespace) -> int:
    run_dir = runner.EVAL_ROOT / a.run
    res = human.agreement(run_dir, Path(a.sheet), tag=a.tag)
    (run_dir / "agreement.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    if a.family:
        runs = {}
        for spec in a.runs.split(","):
            name, rid = spec.split("=", 1)
            runs[name] = runner.EVAL_ROOT / rid
        res = report.family_table(runs, a.family)
        _print(report.render_compare(res))
        return 0
    ra, rb = (runner.EVAL_ROOT / r for r in a.runs.split(","))
    res = report.compare(ra, rb, a.metric, a.stage)
    _print(report.render_compare(res))
    return 0


def cmd_perturb(a: argparse.Namespace) -> int:
    """RQ7(a): controlled wrong answers → rules 1–7 vs rule 8 (plan 2026-09-29 §4.3)."""
    from app.evaluation import perturb

    settings = get_settings()
    items, _ = _items(a.tier, a.split, a.limit, a.ids)
    if not _confirm_test(a, "perturb"):
        return 2
    svc, session = _service(settings, None)
    rows, summ = perturb.run(session, svc, items, seed=a.seed)
    run_id = a.run_id or runner.make_run_id("RQ7a-perturb", a.split or "all")
    out = runner.EVAL_ROOT / run_id
    out.mkdir(parents=True, exist_ok=True)
    runner.write_jsonl(out / "perturb.jsonl", rows)
    summ |= {"split": a.split, "tier": a.tier, "n_items": len(items), "seed": a.seed}
    (out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), "utf-8")
    lines = [
        f"# RQ7(a) perturbation — {run_id}",
        "",
        "| kind | n | rules 1–7 | rule 8 | rules 1–8 | rule 8 skipped |",
        "|---|---|---|---|---|---|",
    ]
    for k, v in summ["kinds"].items():
        lines.append(
            f"| {k} | {v['n']} | {v['rules_1_7']:.3f} | {v['rule_8']:.3f} "
            f"| {v['rules_1_8']:.3f} | {v['v8_skipped']:.3f} |"
        )
    lines += ["", f"rule 8 latency: {summ.get('v8_latency_ms')}"]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text, "utf-8")
    _print(text)
    return 0


def cmd_understanding(a: argparse.Namespace) -> int:
    """Query understanding alone against the frame labels (plan G3 section 4): no retrieval, no
    answer, no LLM call. `--split dev,val` pools the splits (D58)."""
    from app.evaluation import gold_frame

    settings = get_settings()
    items: list[GoldItem] = []
    for split in a.split.split(","):
        if split == "test":
            _print("the test split is sealed until G8")
            return 2
        items += _items(a.tier, split, None, a.ids)[0]
    unlabelled = [it.id for it in items if it.frame is None]
    items = [it for it in items if it.frame is not None]
    make = gold_frame.frame_predictor if a.engine == "v2" else gold_frame.production_predictor
    predict = make(settings, get_sessionmaker(settings)())
    rows = [gold_frame.score_item(it, predict(it.question, it.as_of_date)) for it in items]
    summ = gold_frame.summarize(rows)
    name = f"understanding-{a.engine}-rules"
    run_id = a.run_id or runner.make_run_id(name, a.split.replace(",", "+"))
    variant = "bản v2, đường quy tắc" if a.engine == "v2" else "quy tắc đang vận hành"
    title = f"Hiểu câu hỏi — {variant} — {a.split} ({len(items)} câu) — {run_id}"
    text = gold_frame.render_markdown(title, summ, rows)
    if unlabelled:
        text += f"\nChưa có nhãn frame: {len(unlabelled)} câu.\n"
    if not a.no_save:
        out = runner.EVAL_ROOT / run_id
        out.mkdir(parents=True, exist_ok=True)
        runner.write_jsonl(out / "understanding.jsonl", rows)
        (out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), "utf-8")
        (out / "report.md").write_text(text, "utf-8")
    _print(text)
    if a.errors:
        for r in rows:
            e = r.get("slot_errors") or {}
            if e.get("extra") or e.get("missed"):
                _print(f"{r['item_id']} extra={e['extra']} missed={e['missed']}")
    return 0


def cmd_reasoning(a: argparse.Namespace) -> int:
    """Plan step 3.4 (sub-questions, missing facts, hops) against its three measurement sets and
    against the questions that need none of it. Rules and fact store only, no LLM call."""
    from app.evaluation import gold_frame
    from app.evaluation import gold_reasoning as gr

    settings = get_settings()
    splits = a.split.split(",")
    if "test" in splits:
        _print("the test split is sealed until G8")
        return 2
    resolver, codes = gold_frame.frame_resolver(settings, get_sessionmaker(settings)())
    items = [it for s in splits for it in _items("A,B,M", s, None, None)[0] if it.frame]
    frames = {it.id: resolver.understand(it.question) for it in items}
    under = settings.data_dir / "gold" / "understanding"
    partial = [r for r in gr.read_jsonl(under / "partial_v1.jsonl") if r["split"] in splits]
    composite = [r for r in gr.read_jsonl(under / "composite_v1.jsonl") if r["split"] in splits]
    p_rows = [gr.score_partial(r, resolver.understand(r["question"])) for r in partial]
    c_rows = [gr.score_composite(r, resolver.understand(r["question"]), codes) for r in composite]
    h_rows = [gr.score_hops(it, frames[it.id]) for it in items]
    multi = [r for r in h_rows if "expected_type" in r]
    single = [r for r in h_rows if r["group"] == "T1"]
    whole = [it for it in items if not (it.frame and it.frame.sub_questions)]
    wrongly_split = [it.id for it in whole if frames[it.id].sub_questions]
    hand_split = [it for it in items if it.frame and it.frame.sub_questions]
    hand_ok = sum(
        1
        for it in hand_split
        if sorted(s.domain for s in frames[it.id].sub_questions) == sorted(it.frame.sub_questions)
    )

    def rate(rows: list, key: str) -> str:
        m = gr.mean(rows, key)
        if not m.get("n"):
            return "–"
        lo, hi = m.get("wilson95") or m.get("ci95") or (m["mean"], m["mean"])
        return f"{m['mean']:.3f} ({lo:.2f}–{hi:.2f}), n={m['n']}"

    lines = [
        f"# Bước 3.4 — tách câu, dữ kiện thiếu, bước nối — {a.split}",
        "",
        f"## Dữ kiện thiếu ({len(p_rows)} câu sinh từ bảng thô)",
        "",
        "| Chỉ số | Kết quả |",
        "|---|---|",
        f"| Đúng chế độ (trả lời / liệt kê / hỏi lại) | {rate(p_rows, 'mode_ok')} |",
        f"| Trả lời một giá trị khi còn nhiều đáp án | {int(sum(r['unsafe_answer'] for r in p_rows))} câu |",
        f"| Đúng số đáp án | {rate(p_rows, 'n_answers_ok')} |",
        f"| F1 tập chiều thiếu | {rate(p_rows, 'missing_f1')} |",
        f"| Chiều hỏi đầu tiên đúng (câu phải hỏi lại) | {rate(p_rows, 'first_missing_ok')} |",
        f"| Tập phương án khớp nguyên vẹn (câu trả lời / liệt kê) | {rate(p_rows, 'options_ok')} |",
        "",
        f"## Tách câu ({len(c_rows)} câu ghép hai lĩnh vực)",
        "",
        "| Chỉ số | Kết quả |",
        "|---|---|",
        f"| Đúng số câu con và lĩnh vực | {rate(c_rows, 'split_ok')} |",
        f"| Đúng quan hệ (so sánh / nối tiếp) | {rate(c_rows, 'relation_ok')} |",
        f"| F1 slot theo câu con | {gr.micro_f1(c_rows) or 0:.3f} |",
        f"| Câu một vế bị tách nhầm | {len(wrongly_split)}/{len(whole)} |",
        f"| Câu soạn tay có câu con (B4) tách đúng lĩnh vực | {hand_ok}/{len(hand_split)} |",
        "",
        f"## Bước nối ({len(multi)} câu nhiều bước T2)",
        "",
        "| Kiểu | Số câu | Nhận ra | Giải đúng căn cứ thứ hai | Tác động đúng | Đủ cả hai căn cứ |",
        "|---|---|---|---|---|---|",
    ]
    kinds = sorted({r["expected_type"] for r in multi})
    for kind in [*kinds, None]:
        rows = [r for r in multi if kind is None or r["expected_type"] == kind]
        lines.append(
            f"| {kind or '**cộng**'} | {len(rows)} | {gr.mean(rows, 'detected')['mean']:.2f}"
            f" | {gr.mean(rows, 'resolved')['mean']:.2f}"
            f" | {_fmt(gr.mean(rows, 'effect_ok').get('mean'))}"
            f" | {gr.mean(rows, 'both_bases')['mean']:.2f} |"
        )
    extra = sum(1 for r in multi if r["extra_types"])
    lines += [
        "",
        f"Câu T2 có thêm bước nối khác kiểu mong đợi: {extra}/{len(multi)}.",
        f"Câu T1 (không cần bước nối) bị gắn bước nối làm đổi slot: "
        f"{int(sum(r['slot_changing'] for r in single))}/{len(single)}.",
        f"Câu T1 bị gắn bất kỳ bước nối nào: {sum(1 for r in single if r['types'])}/{len(single)}.",
    ]
    text = "\n".join(lines) + "\n"
    run_id = a.run_id or runner.make_run_id("reasoning-v2-rules", a.split.replace(",", "+"))
    if not a.no_save:
        out = runner.EVAL_ROOT / run_id
        out.mkdir(parents=True, exist_ok=True)
        runner.write_jsonl(out / "partial.jsonl", p_rows)
        runner.write_jsonl(out / "composite.jsonl", c_rows)
        runner.write_jsonl(out / "hops.jsonl", h_rows)
        (out / "report.md").write_text(text, "utf-8")
    _print(text)
    if a.errors:
        for r in p_rows:
            if not r["mode_ok"] or r["options_ok"] == 0.0 or (r["missing_f1"] or 1.0) < 1.0:
                _print(f"partial {r['id']}: {r['expected_mode']} -> {r['mode']} {r['detail']}")
        for r in c_rows:
            if not r["split_ok"]:
                _print(f"composite {r['id']}: {r['detail']}")
        for r in multi:
            if not r["both_bases"] or r["extra_types"]:
                _print(
                    f"hop {r['id']}: want {r['expected_type']} got {r['types']} resolved={r['resolved']}"
                )
        for iid in wrongly_split:
            _print(f"split by mistake: {iid}")
    return 0


def cmd_reasoning_llm(a: argparse.Namespace) -> int:
    """Plan step 3.5: the constrained LLM path over the same sets as `reasoning`. Calls the
    answer model; stops calling when `--budget` (USD) is spent and reports what was done."""
    from app.evaluation import gold_frame, reasoning_llm
    from app.evaluation import gold_reasoning as gr
    from app.llm.client import get_llm
    from app.retrieval.frame_llm import LLMAssist

    settings = get_settings()
    splits = a.split.split(",")
    if "test" in splits:
        _print("the test split is sealed until G8")
        return 2
    resolver, codes = gold_frame.frame_resolver(settings, get_sessionmaker(settings)())
    token_cost = [0.0]
    assist = LLMAssist(
        resolver,
        get_llm(settings),
        repair_model=settings.gemini_understanding_model,
        choice_model=settings.gemini_answer_model,
        choice_thinking=settings.gemini_answer_thinking,
        budget_usd=a.budget,
        call=None,
    )
    assist._call = reasoning_llm.cached_call(assist.llm, _cache(a, settings), token_cost)
    items = [it for s in splits for it in _items(a.tier, s, None, a.ids)[0] if it.frame]
    under = settings.data_dir / "gold" / "understanding"
    partial = composite = []
    if not a.ids:
        partial = [r for r in gr.read_jsonl(under / "partial_v1.jsonl") if r["split"] in splits]
        composite = [r for r in gr.read_jsonl(under / "composite_v1.jsonl") if r["split"] in splits]
    if a.limit:
        items, partial, composite = items[: a.limit], partial[: a.limit], composite[: a.limit]
    text, rows = reasoning_llm.run(assist, codes, items, partial, composite, token_cost)
    run_id = a.run_id or runner.make_run_id("reasoning-v2-llm", a.split.replace(",", "+"))
    if not a.no_save:
        reasoning_llm.write(runner.EVAL_ROOT / run_id, text, rows)
    _print(text)
    return 0


def _fmt(v: float | None) -> str:
    return "–" if v is None else f"{v:.2f}"


def cmd_coverage(a: argparse.Namespace) -> int:
    """Graph-first check: do the entity neighbourhoods contain the must-have evidence?"""
    from app.evaluation import coverage

    settings = get_settings()
    items, _ = _items(a.tier, a.split, a.limit, a.ids)
    if not _confirm_test(a, "coverage"):
        return 2
    svc, session = _service(settings, None)
    hops = tuple(int(h) for h in a.hops.split(","))
    exclude = {t for t in (a.exclude or "").split(",") if t}
    rows, summ = coverage.run(session, svc, items, hops=hops, exclude=exclude)
    run_id = a.run_id or runner.make_run_id("coverage", a.split or "all", tag=a.tag)
    out = runner.EVAL_ROOT / run_id
    out.mkdir(parents=True, exist_ok=True)
    runner.write_jsonl(out / "coverage.jsonl", rows)
    summ |= {"split": a.split, "tier": a.tier, "n_items": len(rows), "hops": list(hops)}
    (out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), "utf-8")
    text = coverage.render(run_id, summ)
    (out / "report.md").write_text(text, "utf-8")
    _print(text)
    return 1 if summ["degraded"] else 0


def cmd_graph_first(a: argparse.Namespace) -> int:
    """Relations first, then the vectors on the nodes — executed in Neo4j, against the hybrid."""
    from app.evaluation import graphfirst

    settings = get_settings()
    items, _ = _items(a.tier, a.split, a.limit, a.ids)
    if not _confirm_test(a, "graph-first"):
        return 2
    svc, session = _service(settings, None)
    rows, summ = graphfirst.run(session, svc, items)
    run_id = a.run_id or runner.make_run_id("graph-first", a.split or "all")
    out = runner.EVAL_ROOT / run_id
    out.mkdir(parents=True, exist_ok=True)
    runner.write_jsonl(out / "graph_first.jsonl", rows)
    summ |= {"split": a.split, "tier": a.tier, "n_items": len(rows)}
    (out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), "utf-8")
    text = graphfirst.render(run_id, summ)
    (out / "report.md").write_text(text, "utf-8")
    _print(text)
    return 1 if summ["degraded"] else 0


def cmd_report(a: argparse.Namespace) -> int:
    runs = [a.run] if a.run else []
    if a.runs:
        runs += a.runs.split(",")
    for rid in runs:
        run_dir = runner.EVAL_ROOT / rid
        text = report.render_run(run_dir)
        (run_dir / "report.md").write_text(text, encoding="utf-8")
        _print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="QCVN chatbot evaluation")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("gold-check")
    s.add_argument("--tier", default="all", help="all (= A,B,C) or a comma list, e.g. A,B,M")
    s.add_argument("--ci-eligible", action="store_true")
    s.set_defaults(fn=cmd_gold_check)

    for name, fn in (("retrieval", cmd_retrieval), ("qa", cmd_qa)):
        s = sub.add_parser(name)
        s.add_argument("--config", default="C4", choices=sorted(CONFIGS))
        s.add_argument("--tier", default="all", help="all (= A,B,C) or a comma list, e.g. A,B,M")
        s.add_argument("--split", default="dev", choices=["dev", "val", "test"])
        s.add_argument("--limit", type=int)
        s.add_argument("--ids", nargs="*")
        s.add_argument("--confirm-test", action="store_true")
        s.add_argument("--run-id")
        if name == "retrieval":
            s.add_argument(
                "--set", action="append", help="override a retrieval option, e.g. ppr_weight=0.5"
            )
            s.add_argument("--gate", action="store_true")
            s.add_argument("--ci-eligible", action="store_true")
        else:
            s.add_argument("--repeats", type=int)
            s.add_argument("--no-cache", action="store_true")
            s.add_argument("--warmup", action="store_true")
            s.add_argument("--llm", default="gemini", choices=["gemini", "fake"])
            s.add_argument("--model")
            s.add_argument("--verbose", action="store_true")
        s.set_defaults(fn=fn)

    s = sub.add_parser("judge")
    s.add_argument("--run", required=True)
    s.add_argument("--judge-model")
    s.add_argument("--judge-thinking")
    s.add_argument("--tag", default="J1")
    s.add_argument("--llm", default="gemini", choices=["gemini", "fake"])
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=cmd_judge)

    s = sub.add_parser("ablation")
    s.add_argument("--configs", default="C0,C1,C2,C3,C4,C4-nofilter,C7")
    s.add_argument("--tier", default="all", help="all (= A,B,C) or a comma list, e.g. A,B,M")
    s.add_argument("--split", default="dev", choices=["dev", "val", "test"])
    s.add_argument("--limit", type=int)
    s.add_argument("--level", default="auto")
    s.add_argument("--confirm-test", action="store_true")
    s.set_defaults(fn=cmd_ablation)

    s = sub.add_parser("human-sheet")
    s.add_argument("--run", required=True)
    s.add_argument("--frac", type=float, default=0.2)
    s.add_argument("--seed", type=int, default=20260924)
    s.add_argument("--tag", default="J1")
    s.set_defaults(fn=cmd_human_sheet)

    s = sub.add_parser("agreement")
    s.add_argument("--run", required=True)
    s.add_argument("--sheet", required=True)
    s.add_argument("--tag", default="J1")
    s.set_defaults(fn=cmd_agreement)

    s = sub.add_parser("compare")
    s.add_argument("--runs", required=True, help="RUN_A,RUN_B or for --family: C4=run,C0=run,...")
    s.add_argument("--metric", default="correct_safe")
    s.add_argument("--stage")
    s.add_argument("--family")
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("perturb")
    s.add_argument("--tier", default="A,M", help="comma list; table items of tiers A and M")
    s.add_argument("--split", default="dev", choices=["dev", "val", "test"])
    s.add_argument("--limit", type=int)
    s.add_argument("--ids", nargs="*")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--confirm-test", action="store_true")
    s.add_argument("--run-id")
    s.set_defaults(fn=cmd_perturb)

    s = sub.add_parser("understanding")
    s.add_argument("--tier", default="A,B,M", help="comma list of gold tiers")
    s.add_argument("--split", default="dev", help="dev, val or dev,val")
    s.add_argument("--ids", nargs="*")
    s.add_argument("--engine", default="v1", choices=["v1", "v2"])
    s.add_argument("--errors", action="store_true", help="list slot errors per item")
    s.add_argument("--run-id")
    s.add_argument("--no-save", action="store_true")
    s.set_defaults(fn=cmd_understanding)

    s = sub.add_parser("reasoning")
    s.add_argument("--split", default="dev,val", help="dev, val or dev,val")
    s.add_argument("--errors", action="store_true")
    s.add_argument("--run-id")
    s.add_argument("--no-save", action="store_true")
    s.set_defaults(fn=cmd_reasoning)

    s = sub.add_parser("reasoning-llm")
    s.add_argument("--split", default="dev,val", help="dev, val or dev,val")
    s.add_argument("--tier", default="A,B,M")
    s.add_argument("--budget", type=float, default=5.0, help="USD; no call once it is spent")
    s.add_argument("--limit", type=int, help="first N of each set (smoke run)")
    s.add_argument("--ids", nargs="*")
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--run-id")
    s.add_argument("--no-save", action="store_true")
    s.set_defaults(fn=cmd_reasoning_llm)

    s = sub.add_parser("coverage")
    s.add_argument("--tier", default="A,B,M", help="comma list of gold tiers")
    s.add_argument("--split", default="dev", choices=["dev", "val", "test"])
    s.add_argument("--hops", default="2,3", help="neighbourhood radii, comma separated")
    s.add_argument("--exclude", help="relationship types left out of the graph, comma separated")
    s.add_argument("--tag", help="suffix of the run id")
    s.add_argument("--limit", type=int)
    s.add_argument("--ids", nargs="*")
    s.add_argument("--confirm-test", action="store_true")
    s.add_argument("--run-id")
    s.set_defaults(fn=cmd_coverage)

    s = sub.add_parser("graph-first")
    s.add_argument("--tier", default="A,B,M", help="comma list of gold tiers")
    s.add_argument("--split", default="dev", choices=["dev", "val", "test"])
    s.add_argument("--limit", type=int)
    s.add_argument("--ids", nargs="*")
    s.add_argument("--confirm-test", action="store_true")
    s.add_argument("--run-id")
    s.set_defaults(fn=cmd_graph_first)

    s = sub.add_parser("report")
    s.add_argument("--run")
    s.add_argument("--runs")
    s.set_defaults(fn=cmd_report)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
