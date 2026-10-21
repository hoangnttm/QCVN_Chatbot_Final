"""Gold set v2 tooling (plan §2.5).

python scripts/gold.py validate   [--dataset ...] [--tier A|B|C|all] [--resolver derived|db]
                                  [--strict] [--item ID ...] [--split dev|test] [--report out.md]
python scripts/gold.py migrate-v1 [--in data/gold/gold_qa_v1.jsonl] [--out data/gold/gold_qa_v2.jsonl]
python scripts/gold.py candidates --stratum CODE [--n N] [--seed S] [--exclude-used] [--out file.md]
python scripts/gold.py source     --ref '<json>' | --chunk CHUNK_ID | --rule RULE_CODE
python scripts/gold.py map        --ref '<json>' | --tier A|B|C
python scripts/gold.py resolve    --rule CODE | --chunk ID | --label PREFIX | --mr TT10.MR.nn
                                  | --transition RULE_ID [--json]
python scripts/gold.py stats      [--show-test] [--out docs/reports/gold-stats-<date>.md]
python scripts/gold.py split      [--seed S] [--dry-run] [--force]
python scripts/gold.py freeze     --version 2.0.0 [--index-label ...]
python scripts/gold.py export     --out file.xlsx [--for expert|second-check] [--split all|dev|test] [--tier B]
python scripts/gold.py import     file.xlsx --reviewer NAME --role expert|second [--dry-run]
python scripts/gold.py agreement  [--second data/gold/second_check_v2.jsonl] [--out report.md]
python scripts/gold.py generate   [--tables ...] [--templates data/gold/templates_v2.yaml]
python scripts/gold.py generate-multihop   # tier M from data/gold/multihop_v2.yaml
                                  [--seed S] [--out data/gold/gold_tables_v2.jsonl]
python scripts/gold.py crosscheck [--tier A] [--out data/gold/parser_findings.jsonl]
python scripts/gold.py sample-check --n 60 [--seed S] [--out file.csv]
python scripts/gold.py import-logs --csv export.csv [--since YYYY-MM-DD] [--out data/gold/work/natural-candidates.jsonl]

v3, round 1 (docs/plans/2026-10-06-xay-lai-bo-cau-hoi.md §0):
python scripts/gold.py migrate-v3      [--dry-run]            # v2 files -> v3 files, test -> val
python scripts/gold.py authoring-kit   [--out file.xlsx]      # workbook for the author
python scripts/gold.py import-authoring file.xlsx --author NAME [--dry-run]
python scripts/gold.py reserve-test-rows [--kit file.xlsx] [--dry-run]   # D53: val rows -> test
python scripts/gold.py generate-test   [--kit file.xlsx] [--dry-run]     # T1/T2 with new sentences
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings, get_settings  # noqa: E402
from app.evaluation import gold_stats  # noqa: E402
from app.evaluation.gold_candidates import (  # noqa: E402
    candidate_chunks,
    candidate_rows,
    render_markdown,
    used_keys,
)
from app.evaluation.gold_migrate import migrate_v1  # noqa: E402
from app.evaluation.gold_resolver import DerivedResolver  # noqa: E402
from app.evaluation.gold_schema import (  # noqa: E402
    GoldItem,
    SourceRef,
    dump_gold,
    load_gold,
    load_strata,
)
from app.evaluation.gold_source import Derived, RawSource  # noqa: E402
from app.evaluation.gold_split import assign_splits, freeze  # noqa: E402
from app.evaluation.gold_validate import freeze_check, validate  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
GOLD_DIR = REPO / "data" / "gold"
# v3 files are the working set; the v2 files stay as they were (gold_v2_frozen.manifest.json)
DEFAULT_DATASET = GOLD_DIR / "gold_qa_v3.jsonl"
TABLES_DATASET = GOLD_DIR / "gold_tables_v3.jsonl"
NATURAL_DATASET = GOLD_DIR / "gold_natural_v3.jsonl"
MULTIHOP_DATASET = GOLD_DIR / "gold_multihop_v3.jsonl"
MULTIHOP_SPEC = GOLD_DIR / "multihop_v2.yaml"
STRATA = GOLD_DIR / "strata_v3.yaml"
V2_FILES = {
    "A": GOLD_DIR / "gold_tables_v2.jsonl",
    "B": GOLD_DIR / "gold_qa_v2.jsonl",
    "M": GOLD_DIR / "gold_multihop_v2.jsonl",
}
WORK_V3 = GOLD_DIR / "work" / "v3"
UNDERSTANDING_DIR = GOLD_DIR / "understanding"
AUTHORING_SHEET = "Soạn"
KIT = WORK_V3 / "soan-cau-hoi-dot-1.xlsx"
TEMPLATE_KIT = WORK_V3 / "mau-cau-dot-1.xlsx"
TEST_ROWS = GOLD_DIR / "test_rows_v3.yaml"
TIER_FILES = {
    "A": TABLES_DATASET,
    "B": DEFAULT_DATASET,
    "C": NATURAL_DATASET,
    "M": MULTIHOP_DATASET,
}


def parse_tiers(tier: str) -> list[str]:
    """`all` = A,B,C (tier M is opt-in so earlier baselines stay comparable); else a comma list."""
    tiers = ["A", "B", "C"] if tier == "all" else [t.strip().upper() for t in tier.split(",")]
    bad = [t for t in tiers if t not in TIER_FILES]
    if bad:
        raise SystemExit(f"unknown tier(s) {bad}; use all or a comma list of {sorted(TIER_FILES)}")
    return tiers


def _settings() -> Settings:
    return get_settings()


def _resolver(kind: str, settings: Settings):
    if kind == "db":
        from app.db.session import get_sessionmaker
        from app.evaluation.gold_resolver import DbResolver

        return DbResolver(get_sessionmaker(settings)())
    return DerivedResolver(settings)


def _load_all(tier: str, dataset: Path | None) -> list[GoldItem]:
    if dataset:
        return load_gold(dataset)
    items: list[GoldItem] = []
    tiers = parse_tiers(tier)
    for t in tiers:
        p = TIER_FILES[t]
        if p.exists():
            items += load_gold(p, tier=t)
    return items


def _print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:  # Windows console with cp1252
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))


# ---------------------------------------------------------------- commands


def cmd_validate(a: argparse.Namespace) -> int:
    settings = _settings()
    items = _load_all(a.tier, Path(a.dataset) if a.dataset else None)
    if a.item:
        items = [it for it in items if it.id in set(a.item)]
    if a.split:
        items = [it for it in items if it.split == a.split]
    strata = load_strata(STRATA) if STRATA.exists() else None
    rep = validate(
        items, _resolver(a.resolver, settings), RawSource(settings), strata, strict=a.strict
    )
    if a.report:
        Path(a.report).write_text(rep.render_markdown(), encoding="utf-8")
    _print(f"items={rep.n_items} errors={len(rep.errors)} warnings={len(rep.warnings)}")
    for f in rep.findings:
        _print(f"  {f}")
    return 0 if rep.ok else 1


def cmd_migrate_v1(a: argparse.Namespace) -> int:
    settings = _settings()
    items, problems = migrate_v1(Path(a.inp), DerivedResolver(settings))
    for p in problems:
        _print(f"  ! {p}")
    out = Path(a.out)
    existing = load_gold(out) if out.exists() and not a.overwrite else []
    keep = [it for it in existing if it.id not in {x.id for x in items}]
    dump_gold(keep + items, out)
    _print(f"migrated {len(items)} items -> {out} (kept {len(keep)} existing)")
    return 1 if problems else 0


def cmd_candidates(a: argparse.Namespace) -> int:
    settings = _settings()
    strata = load_strata(STRATA)
    s = strata.by_code().get(a.stratum)
    if not s:
        _print(f"unknown stratum {a.stratum}; known: {', '.join(strata.by_code())}")
        return 2
    res = DerivedResolver(settings)
    exclude_rules: set[str] = set()
    exclude_chunks: set[str] = set()
    covered: set[str] = set()
    if a.exclude_used and DEFAULT_DATASET.exists():
        items = load_gold(DEFAULT_DATASET)
        exclude_rules, exclude_chunks = used_keys(items)
        for it in items:
            if it.stratum == a.stratum:
                for code in it.target_rule_codes:
                    rule = res.rule(code)
                    if rule:
                        from app.evaluation.gold_candidates import _coverage_cells

                        covered |= set(_coverage_cells(rule))
    seed = a.seed if a.seed is not None else strata.seed
    n = a.n or (s.quota * 2)
    cands = []
    if s.tables:
        cands += candidate_rows(
            res, s, seed=seed, n=n, exclude_rules=exclude_rules, covered=covered
        )
    if s.chunk_ids or s.rule_ids or (s.document_ids and s.chunk_types):
        cands += candidate_chunks(res, s, seed=seed, n=n, exclude=exclude_chunks)
    raw = RawSource(settings)
    raw_lines = {}
    for c in cands:
        if c.document_id not in raw_lines:
            try:
                raw_lines[c.document_id] = raw.lines(c.document_id)
            except KeyError:
                pass
    text = render_markdown(cands, raw_lines)
    out = Path(a.out) if a.out else GOLD_DIR / "work" / f"candidates-{a.stratum}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    _print(f"{len(cands)} candidates -> {out}")
    return 0


def _ref_from_args(a: argparse.Namespace, res: DerivedResolver) -> SourceRef | None:
    if getattr(a, "ref", None):
        return SourceRef.model_validate(json.loads(a.ref))
    if getattr(a, "chunk", None):
        c = res.chunk(a.chunk)
        if not c:
            return None
        sp = c["span"]
        return SourceRef(
            document_id=c["document_id"],
            file_sha256=sp["file_hash"],
            line_start=sp["line_start"],
            line_end=sp["line_end"],
            pdf_page=sp.get("pdf_page_start"),
            printed_page=sp.get("printed_page"),
        )
    if getattr(a, "rule", None):
        r = res.rule(a.rule)
        if not r:
            return None
        row = res.row(r["row_id"]) or {}
        sp = row.get("span") or r.get("span") or {}
        return SourceRef(
            document_id=r["document_id"],
            file_sha256=sp["file_hash"],
            line_start=sp["line_start"],
            line_end=sp["line_end"],
            table_code=r["table_code"],
            row_no=row.get("row_no"),
            column_path=[r["zone"]],
        )
    return None


def cmd_source(a: argparse.Namespace) -> int:
    settings = _settings()
    res = DerivedResolver(settings)
    ref = _ref_from_args(a, res)
    if ref is None:
        _print("not found")
        return 1
    raw = RawSource(settings)
    _print(
        f"# {ref.document_id} sha256 ok={raw.sha256_ok(ref.document_id)} lines {ref.line_start}-{ref.line_end}"
    )
    ctx = a.context
    lines = raw.lines(ref.document_id)
    for i in range(max(1, ref.line_start - ctx), min(len(lines), ref.line_end + ctx) + 1):
        mark = ">" if ref.line_start <= i <= ref.line_end else " "
        _print(f"{mark}{i:5d}| {lines[i - 1]}")
    return 0


def cmd_map(a: argparse.Namespace) -> int:
    settings = _settings()
    der = Derived(settings)
    if a.ref:
        ref = SourceRef.model_validate(json.loads(a.ref))
        for c in der.map_to_chunks(ref):
            _print(f"{c['chunk_id']}\t{c['chunk_type']}\t{c['citation_label']}")
        return 0
    items = _load_all(a.tier, None)
    bad = 0
    for it in items:
        if not it.source_ref:
            continue
        chunks = der.map_to_chunks(it.source_ref)
        ids = {c["chunk_id"] for c in chunks}
        g2 = {r.chunk_id for r in it.grade2()}
        if g2 and not (g2 & ids):
            bad += 1
            _print(
                f"  ! {it.id}: grade-2 {sorted(g2)} not among chunks covering source_ref {sorted(ids)[:4]}"
            )
    _print(f"checked {len(items)} items, {bad} mapping mismatches")
    return 1 if bad else 0


def cmd_resolve(a: argparse.Namespace) -> int:
    settings = _settings()
    res = DerivedResolver(settings)
    out: dict = {}
    if a.rule:
        r = res.rule(a.rule)
        if r:
            row = res.row(r["row_id"]) or {}
            chunk = res.chunk(f"chunk:{r['row_id']}")
            from app.evaluation.gold_candidates import skeleton_for_rule

            out = {
                "rule": r,
                "row_label": row.get("citation_label"),
                "chunk_id": chunk["chunk_id"] if chunk else None,
                "skeleton": skeleton_for_rule(r, row, chunk),
            }
    elif a.chunk:
        out = res.chunk(a.chunk) or {}
    elif a.label:
        out = {
            "chunks": [
                {"chunk_id": c["chunk_id"], "label": c["citation_label"]}
                for c in res.chunks_by_label_prefix(a.label)
            ]
        }
    elif a.mr:
        out = res.monitoring(a.mr) or {}
    elif a.transition:
        out = res.transition(a.transition) or {}
    _print(json.dumps(out, ensure_ascii=False, indent=2 if a.json else None, default=str))
    return 0 if out else 1


def cmd_stats(a: argparse.Namespace) -> int:
    settings = _settings()
    res = DerivedResolver(settings)
    items = _load_all(a.tier, None)
    text = gold_stats.render_markdown(items, res, show_test=a.show_test, all_rules=res.all_rules())
    out = (
        Path(a.out)
        if a.out
        else REPO / "docs" / "reports" / f"gold-stats-{dt.date.today().isoformat()}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    _print(text.split("## Câu hỏi")[0])
    _print(f"-> {out}")
    return 0


def cmd_split(a: argparse.Namespace) -> int:
    strata = load_strata(STRATA)
    for tier, path in TIER_FILES.items():
        if not path.exists():
            continue
        items = load_gold(path, tier=tier)
        before = {it.id: it.split for it in items}
        assign_splits(items, strata, a.seed, force=a.force)
        changed = [it.id for it in items if before[it.id] != it.split]
        _print(f"{path.name}: {len(changed)} assignments changed: {changed[:20]}")
        if not a.dry_run:
            dump_gold(items, path)
    return 0


def cmd_freeze(a: argparse.Namespace) -> int:
    settings = _settings()
    strata = load_strata(STRATA)
    res = DerivedResolver(settings)
    raw = RawSource(settings)
    ok = True
    for tier, path in TIER_FILES.items():
        if not path.exists():
            continue
        items = load_gold(path, tier=tier)
        rep = (
            freeze_check(items, res, raw, strata)
            if tier == "B"
            else validate(items, res, raw, strata, strict=True)
        )
        if not rep.ok:
            ok = False
            _print(f"{path.name}: {len(rep.errors)} errors")
            for f in rep.errors[:50]:
                _print(f"  {f}")
            continue
        man = freeze(
            items,
            path,
            a.version,
            repo=REPO,
            index_label=a.index_label,
            document_hashes=None,
            strata=strata,
        )
        _print(
            f"{path.name}: frozen v{a.version} sha256={man['sha256'][:12]} items={man['n_items']} {man['by_split']}"
        )
    if ok:
        _print(f"git tag gold-v{a.version}")
    return 0 if ok else 1


def cmd_export(a: argparse.Namespace) -> int:
    from app.evaluation.gold_excel import export_review

    items = _load_all(a.tier, None)
    if a.split != "all":
        items = [it for it in items if it.split == a.split]
    if a.ids:
        items = [it for it in items if it.id in set(a.ids)]
    export_review(items, Path(a.out), mode=a.mode)
    _print(f"{len(items)} items -> {a.out}")
    return 0


def cmd_import(a: argparse.Namespace) -> int:
    from app.evaluation.gold_excel import apply_review, read_review

    decisions = read_review(Path(a.file))
    _print(f"{len(decisions)} decisions read")
    if a.dry_run:
        for d in decisions[:20]:
            _print(f"  {d}")
        return 0
    if a.role == "second":
        out = GOLD_DIR / "second_check_v2.jsonl"
        with open(out, "a", encoding="utf-8") as fh:
            for d in decisions:
                fh.write(
                    json.dumps(
                        {**d.__dict__, "reviewer": a.reviewer, "at": dt.date.today().isoformat()},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        _print(f"appended to {out}")
        return 0
    items = load_gold(DEFAULT_DATASET, tier="B")
    n = apply_review(items, decisions, a.reviewer)
    dump_gold(items, DEFAULT_DATASET)
    _print(
        f"{n} items updated (status/reviewer/expert_correction); apply corrections by hand and bump revision"
    )
    return 0


def cmd_agreement(a: argparse.Namespace) -> int:
    from app.evaluation.gold_agreement import compare
    from app.evaluation.gold_agreement import render_markdown as render_agreement

    items = load_gold(DEFAULT_DATASET, tier="B")
    second_path = Path(a.second)
    if not second_path.exists():
        _print(f"no second-check file at {second_path}")
        return 1
    seconds = [
        json.loads(line)
        for line in second_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rep = compare(items, seconds)
    text = render_agreement(rep)
    out = (
        Path(a.out)
        if a.out
        else REPO / "docs" / "reports" / f"gold-agreement-{dt.date.today().isoformat()}.md"
    )
    out.write_text(text, encoding="utf-8")
    _print(text)
    return 0


def cmd_generate(a: argparse.Namespace) -> int:
    from app.evaluation.gold_generate import generate

    settings = _settings()
    strata = load_strata(STRATA)
    tables = (
        a.tables.split(",")
        if a.tables
        else ["QCVN40_2025.B1", "QCVN40_2025.B2", "QCVN19_2024.B1", "QCVN19_2024.B2"]
    )
    items, findings = generate(
        settings,
        tables,
        Path(a.templates),
        seed=a.seed if a.seed is not None else strata.seed,
        sample_19=a.sample_19,
    )
    assign_splits(items, strata, seed=a.seed, force=True)
    dump_gold(items, Path(a.out))
    if findings:
        fp = GOLD_DIR / "parser_findings.jsonl"
        with open(fp, "w", encoding="utf-8") as fh:
            for f in findings:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")
        _print(f"{len(findings)} parser findings -> {fp}")
    _print(f"{len(items)} tier-A items -> {a.out}")
    return 0


def cmd_generate_multihop(a: argparse.Namespace) -> int:
    from app.evaluation.gold_multihop import generate_multihop

    items = generate_multihop(_settings(), Path(a.templates), Path(a.spec), TABLES_DATASET)
    dump_gold(items, Path(a.out))
    n_dev = sum(1 for it in items if it.split == "dev")
    _print(f"{len(items)} tier-M items (dev {n_dev} / test {len(items) - n_dev}) -> {a.out}")
    return 0


def cmd_crosscheck(a: argparse.Namespace) -> int:
    from app.evaluation.gold_generate import crosscheck

    settings = _settings()
    findings = crosscheck(settings, a.tables.split(",") if a.tables else None)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    _print(f"{len(findings)} findings -> {out}")
    for f in findings[:30]:
        _print(f"  {f['kind']}: {f.get('rule_code') or f.get('cell')} {f.get('detail', '')}")
    return 0


def cmd_sample_check(a: argparse.Namespace) -> int:
    from app.evaluation.gold_generate import sample_check_sheet

    items = load_gold(TABLES_DATASET, tier="A")
    out = (
        Path(a.out)
        if a.out
        else GOLD_DIR / "work" / f"sample-check-{dt.date.today().isoformat()}.csv"
    )
    n = sample_check_sheet(items, out, n=a.n, seed=a.seed, settings=_settings())
    _print(f"{n} cells -> {out}")
    return 0


def cmd_import_logs(a: argparse.Namespace) -> int:
    from app.evaluation.gold_natural import import_logs

    n, out = import_logs(
        Path(a.csv),
        Path(a.out) if a.out else GOLD_DIR / "work" / "natural-candidates.jsonl",
        since=a.since,
        existing=_load_all("all", None),
    )
    _print(f"{n} candidate questions -> {out}")
    return 0


def _session(settings: Settings):
    from app.db.session import get_sessionmaker

    return get_sessionmaker(settings)()


def cmd_migrate_v3(a: argparse.Namespace) -> int:
    from app.evaluation.gold_resolver import DbResolver
    from app.evaluation.gold_v3 import FactIndex, migrate_items, summary, write_frozen_manifest

    settings = _settings()
    session = _session(settings)
    strata = load_strata(STRATA)
    facts = FactIndex(session)
    out_of = {"A": TABLES_DATASET, "B": DEFAULT_DATASET, "M": MULTIHOP_DATASET}
    all_items: list[GoldItem] = []
    findings: list[str] = []
    for tier, src in V2_FILES.items():
        dst = out_of[tier]
        if dst.exists() and not a.force:
            _print(f"{dst.name} exists, kept (use --force to rebuild from v2)")
            all_items += load_gold(dst, tier=tier)
            continue
        items, f = migrate_items(load_gold(src, tier=tier), strata, facts)
        findings += f
        all_items += items
        if not a.dry_run:
            dump_gold(items, dst)
        _print(f"{src.name} -> {dst.name}: {len(items)} items")
    rep = validate(all_items, DbResolver(session), RawSource(settings), strata)
    _print(f"validate (db): items={rep.n_items} errors={len(rep.errors)}")
    for e in rep.errors[:50]:
        _print(f"  {e}")
    _print(f"findings to review: {len(findings)}")
    for f in findings[:50]:
        _print(f"  {f}")
    _print(json.dumps(summary(all_items), ensure_ascii=False))
    if not a.dry_run:
        man = write_frozen_manifest(
            list(V2_FILES.values()), GOLD_DIR / "gold_v2_frozen.manifest.json", "2.1.0"
        )
        _print(f"v2 frozen: {', '.join(man['files'])}")
    return 0 if rep.ok and not findings else 1


def cmd_authoring_kit(a: argparse.Namespace) -> int:
    from app.evaluation.gold_authoring import build_workbook

    settings = _settings()
    if a.templates_only:
        from app.evaluation.gold_authoring import build_template_workbook

        out = Path(a.out) if a.out else TEMPLATE_KIT
        out.parent.mkdir(parents=True, exist_ok=True)
        _print(f"{build_template_workbook(settings, out)} sentences to write -> {out}")
        return 0
    existing = _load_all("A,B,M", None)
    out = Path(a.out) if a.out else KIT
    out.parent.mkdir(parents=True, exist_ok=True)
    n = build_workbook(settings, load_strata(STRATA), existing, out, seed=a.seed)
    _print(f"{n} rows -> {out}")
    return 0


def cmd_import_authoring(a: argparse.Namespace) -> int:
    from app.evaluation.gold_authoring import import_workbook
    from app.evaluation.gold_resolver import DbResolver
    from app.evaluation.gold_v3 import FactIndex

    settings = _settings()
    session = _session(settings)
    strata = load_strata(STRATA)
    existing = load_gold(DEFAULT_DATASET) if DEFAULT_DATASET.exists() else []
    res = import_workbook(
        Path(a.file),
        settings,
        strata,
        DbResolver(session),
        FactIndex(session),
        author=a.author,
        existing=existing + _load_all("A,M", None),
    )
    _print(f"read {res.n_rows} filled rows: accepted {len(res.items)}, returned {len(res.rejects)}")
    for rid, msgs in res.rejects.items():
        for msg in msgs:
            _print(f"  ! {rid}: {msg}")
    for rid, msgs in res.notes.items():
        for msg in msgs:
            _print(f"  - {rid}: {msg}")
    report = WORK_V3 / f"tra-lai-{dt.date.today().isoformat()}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(res.render_markdown(), encoding="utf-8")
    _print(f"-> {report}")
    if a.dry_run:
        return 0
    new_ids = {it.id for it in res.items}
    keep = [it for it in existing if it.id not in new_ids]
    dump_gold(keep + res.items, DEFAULT_DATASET)
    _print(f"{DEFAULT_DATASET.name}: {len(keep) + len(res.items)} items ({len(res.items)} written)")
    return 0


def cmd_reserve_test_rows(a: argparse.Namespace) -> int:
    import yaml

    from app.evaluation.gold_authoring import hand_cells
    from app.evaluation.gold_v3_test import drop_reserved, load_reserve, reserve_rows

    settings = _settings()
    files = {"A": TABLES_DATASET, "M": MULTIHOP_DATASET}
    items = {t: load_gold(p, tier=t) for t, p in files.items()}
    if TEST_ROWS.exists():
        reserve = load_reserve(TEST_ROWS)  # never re-drawn: val already lost these rows
        _print(f"{TEST_ROWS.name} exists: {len(reserve['reserved_rows'])} rows, re-applied")
    else:
        reserve = reserve_rows(
            items["A"] + items["M"],
            hand_cells(Path(a.kit), settings),
            seed=load_strata(STRATA).seed,
        )
    for t, p in files.items():
        kept = drop_reserved(items[t], reserve)
        _print(f"{p.name}: {len(items[t]) - len(kept)} val items leave, {len(kept)} stay")
        if not a.dry_run:
            dump_gold(kept, p)
    by_table: dict[str, int] = {}
    for r in reserve["reserved_rows"]:
        by_table[r.split(".r")[0]] = by_table.get(r.split(".r")[0], 0) + 1
    _print(f"reserved rows by table: {by_table}")
    if not a.dry_run and not TEST_ROWS.exists():
        TEST_ROWS.write_text(
            yaml.safe_dump(reserve, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        _print(f"-> {TEST_ROWS}")
    return 0


def cmd_generate_test(a: argparse.Namespace) -> int:
    from app.evaluation.gold_authoring import Reject, read_templates
    from app.evaluation.gold_resolver import DbResolver
    from app.evaluation.gold_v3 import FactIndex
    from app.evaluation.gold_v3_test import generate_test, load_reserve

    settings = _settings()
    session = _session(settings)
    strata = load_strata(STRATA)
    if not TEST_ROWS.exists():
        _print(f"{TEST_ROWS.name} missing: run reserve-test-rows first")
        return 2
    try:
        new = read_templates(Path(a.kit))
    except Reject as e:
        _print(f"sheet «Mẫu câu»: {e}")
        return 1
    files = {"A": TABLES_DATASET, "M": MULTIHOP_DATASET}
    old = {t: [it for it in load_gold(p, tier=t) if it.split != "test"] for t, p in files.items()}
    t1, t2, notes = generate_test(
        settings,
        MULTIHOP_SPEC,
        new,
        load_reserve(TEST_ROWS),
        old["A"] + old["M"],
        FactIndex(session),
        seed=a.seed if a.seed is not None else strata.seed,
    )
    for n in notes:
        _print(f"  ! {n}")
    rep = validate(old["A"] + old["M"] + t1 + t2, DbResolver(session), RawSource(settings), strata)
    errors = [e for e in rep.errors if e.item_id in {it.id for it in t1 + t2}]
    _print(f"T1 {len(t1)} items, T2 {len(t2)} items, validator errors {len(errors)}")
    for e in errors[:30]:
        _print(f"  {e}")
    if a.dry_run or errors:
        return 1 if errors else 0
    dump_gold(old["A"] + t1, TABLES_DATASET)
    dump_gold(old["M"] + t2, MULTIHOP_DATASET)
    _print(f"-> {TABLES_DATASET.name}, {MULTIHOP_DATASET.name}")
    return 0


def _frame_files() -> dict[str, Path]:
    return {"A": TABLES_DATASET, "B": DEFAULT_DATASET, "M": MULTIHOP_DATASET}


def cmd_label_frames(a: argparse.Namespace) -> int:
    """Write the query-frame labels (plan G3 step 3.1): template items from their cell and
    pattern, hand-written items from the reviewed sheets."""
    from app.evaluation.gold_frame import (
        cell_slots,
        read_intent_sheet,
        read_slot_sheet,
        template_frame,
    )
    from app.evaluation.gold_schema import GoldFrame
    from app.ontology.loader import load_dictionaries

    settings = _settings()
    resolver = _resolver("derived", settings)
    intents = read_intent_sheet(Path(a.intents)) if Path(a.intents).exists() else {}
    slots = read_slot_sheet(Path(a.slots)) if Path(a.slots).exists() else {}
    # authoring workbooks that carry the frame columns themselves (sheet «Soạn»)
    for book in a.authoring or []:
        if Path(book).exists():
            intents |= read_intent_sheet(Path(book), AUTHORING_SHEET)
            slots |= read_slot_sheet(Path(book), AUTHORING_SHEET)
    problems: list[str] = []
    counts: dict[str, int] = {}
    # codes typed by the reviewer: any letter case, or a name the alias dictionary knows
    d = load_dictionaries(settings=settings)
    known = {
        "parameter": {p.parameter_id.lower(): p.parameter_id for p in d.parameters},
        "equipment": {e.equipment_id.lower(): e.equipment_id for e in d.equipment_types},
        "fuel_type": {f.code.lower(): f.code for f in d.fuel_types},
    }
    for al in d.aliases:
        if al.entity_type == "parameter":
            known["parameter"].setdefault(al.pattern.lower(), al.entity_id)
    for iid, rec in slots.items():
        seen = set()
        kept = []
        for s in rec["slots"]:
            if s.dimension in known:
                code = known[s.dimension].get(s.value.lower())
                if code is None:
                    problems.append(f"{iid}: {s.dimension} {s.value!r} is not in the dictionary")
                    continue
                s.value = code
            if (s.dimension, s.value, s.negated) not in seen:
                seen.add((s.dimension, s.value, s.negated))
                kept.append(s)
        rec["slots"] = kept
    written: dict[Path, list[GoldItem]] = {}
    for tier, path in _frame_files().items():
        items = written[path] = load_gold(path, tier=tier)
        for it in items:
            if tier in ("A", "M"):
                it.frame = template_frame(it, resolver)
                if it.frame is None:
                    problems.append(f"{it.id}: no limit rule for the frame")
                kind = "template"
            elif it.id not in intents:
                problems.append(f"{it.id}: no reviewed intent in {Path(a.intents).name}")
                continue
            else:
                cell = cell_slots(it, resolver)
                rec = slots.get(it.id)
                try:
                    it.frame = GoldFrame(
                        intents=intents[it.id],
                        intents_by="reviewed",
                        slots=cell if cell is not None else (rec["slots"] if rec else []),
                        slots_by="cell" if cell is not None else ("reviewed" if rec else "draft"),
                        missing=rec["missing"] if rec else [],
                        sub_questions=rec["sub_questions"] if rec else [],
                    )
                except ValueError as e:
                    problems.append(f"{it.id}: {str(e)[:200]}")
                    continue
                kind = f"hand:{it.frame.slots_by}"
            counts[kind] = counts.get(kind, 0) + 1
    if not a.dry_run and not problems:
        for path, items in written.items():
            dump_gold(items, path)
    _print(f"frames: {counts}")
    for msg in problems:
        _print(f"  ! {msg}")
    if problems:
        _print("nothing written")
    return 1 if problems else 0


def cmd_frame_sheet(a: argparse.Namespace) -> int:
    """Sheet of draft slots for the hand-written items that have no table cell."""
    from app.evaluation.gold_frame import production_predictor, write_slot_sheet
    from app.ontology.loader import load_dictionaries

    out = Path(a.out)
    if out.exists() and not a.force:
        _print(f"{out} exists (the PO may be editing it); use --force to rebuild")
        return 2
    settings = _settings()
    predict = production_predictor(settings, _session(settings))
    items = [it for it in load_gold(DEFAULT_DATASET) if not it.target_rule_codes]
    if a.split:
        items = [it for it in items if it.split == a.split]
    d = load_dictionaries(settings=settings)
    catalogue = {
        "Thông số (mã — tên)": [f"{p.parameter_id} — {p.name}" for p in d.parameters],
        "Thiết bị (mã — tên)": [f"{e.equipment_id} — {e.name}" for e in d.equipment_types],
        "Nhiên liệu": [f.code for f in d.fuel_types],
        "Văn bản": ["QCVN40:2025", "QCVN19:2024", "TT6/2025", "TT45/2024", "TT10/2021"],
        "Cột / dải": ["A", "B", "C", "F<=2000", "F>2000", "!A = không thuộc cột A"],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    write_slot_sheet(out, [(it, predict(it.question, it.as_of_date)) for it in items], catalogue)
    _print(f"{len(items)} rows -> {out}")
    return 0


def cmd_understanding_sets(a: argparse.Namespace) -> int:
    """Measurement sets of plan step 3.4: partial questions (missing facts) and composite
    questions (sub-questions), generated from the T1 items of dev and val."""
    from app.evaluation import gold_reasoning as gr

    settings = _settings()
    items = [it for it in load_gold(TABLES_DATASET, tier="A") if it.split in ("dev", "val")]
    partial = gr.generate_partial(settings, items, n=a.partial, seed=a.seed)
    composite = gr.generate_composite(items, n=a.composite, seed=a.seed)
    modes: dict[str, int] = {}
    for r in partial:
        modes[r["expected"]["mode"]] = modes.get(r["expected"]["mode"], 0) + 1
    _print(f"partial: {len(partial)} items, expected modes {modes}")
    _print(f"composite: {len(composite)} items")
    if not a.dry_run:
        gr.write_jsonl(partial, UNDERSTANDING_DIR / "partial_v1.jsonl")
        gr.write_jsonl(composite, UNDERSTANDING_DIR / "composite_v1.jsonl")
        _print(f"-> {UNDERSTANDING_DIR}")
    return 0


# ---------------------------------------------------------------- argparse


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gold set v2 tooling")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("validate")
    s.add_argument("--dataset")
    s.add_argument("--tier", default="all", help="all (= A,B,C) or a comma list, e.g. A,B,M")
    s.add_argument("--resolver", default="derived", choices=["derived", "db"])
    s.add_argument("--strict", action="store_true")
    s.add_argument("--item", nargs="*")
    s.add_argument("--split", choices=["dev", "val", "test"])
    s.add_argument("--report")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("migrate-v1")
    s.add_argument("--in", dest="inp", default=str(GOLD_DIR / "gold_qa_v1.jsonl"))
    s.add_argument("--out", default=str(DEFAULT_DATASET))
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(fn=cmd_migrate_v1)

    s = sub.add_parser("candidates")
    s.add_argument("--stratum", required=True)
    s.add_argument("--n", type=int)
    s.add_argument("--seed", type=int)
    s.add_argument("--exclude-used", action="store_true")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_candidates)

    s = sub.add_parser("source")
    s.add_argument("--ref")
    s.add_argument("--chunk")
    s.add_argument("--rule")
    s.add_argument("--context", type=int, default=2)
    s.set_defaults(fn=cmd_source)

    s = sub.add_parser("map")
    s.add_argument("--ref")
    s.add_argument("--tier", default="all", help="all (= A,B,C) or a comma list, e.g. A,B,M")
    s.set_defaults(fn=cmd_map)

    s = sub.add_parser("resolve")
    s.add_argument("--rule")
    s.add_argument("--chunk")
    s.add_argument("--label")
    s.add_argument("--mr")
    s.add_argument("--transition")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("stats")
    s.add_argument("--tier", default="all", help="all (= A,B,C) or a comma list, e.g. A,B,M")
    s.add_argument("--show-test", action="store_true")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("split")
    s.add_argument("--seed", type=int)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_split)

    s = sub.add_parser("freeze")
    s.add_argument("--version", required=True)
    s.add_argument("--index-label")
    s.set_defaults(fn=cmd_freeze)

    s = sub.add_parser("export")
    s.add_argument("--out", required=True)
    s.add_argument("--for", dest="mode", default="expert", choices=["expert", "second-check"])
    s.add_argument("--split", default="all", choices=["all", "dev", "val", "test"])
    s.add_argument("--tier", default="B", help="all (= A,B,C) or a comma list, e.g. A,B,M")
    s.add_argument("--ids", nargs="*")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("import")
    s.add_argument("file")
    s.add_argument("--reviewer", required=True)
    s.add_argument("--role", default="expert", choices=["expert", "second"])
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("agreement")
    s.add_argument("--second", default=str(GOLD_DIR / "second_check_v2.jsonl"))
    s.add_argument("--out")
    s.set_defaults(fn=cmd_agreement)

    s = sub.add_parser("generate")
    s.add_argument("--tables")
    s.add_argument("--templates", default=str(GOLD_DIR / "templates_v2.yaml"))
    s.add_argument("--seed", type=int)
    s.add_argument(
        "--sample-19", type=int, default=180, help="cells sampled from QCVN 19 (rows stratified)"
    )
    s.add_argument("--out", default=str(TABLES_DATASET))
    s.set_defaults(fn=cmd_generate)

    s = sub.add_parser("generate-multihop")
    s.add_argument("--templates", default=str(GOLD_DIR / "templates_v2.yaml"))
    s.add_argument("--spec", default=str(MULTIHOP_SPEC))
    s.add_argument("--out", default=str(MULTIHOP_DATASET))
    s.set_defaults(fn=cmd_generate_multihop)

    s = sub.add_parser("crosscheck")
    s.add_argument("--tables")
    s.add_argument("--tier", default="A")
    s.add_argument("--out", default=str(GOLD_DIR / "parser_findings.jsonl"))
    s.set_defaults(fn=cmd_crosscheck)

    s = sub.add_parser("sample-check")
    s.add_argument("--n", type=int, default=60)
    s.add_argument("--seed", type=int, default=20260924)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_sample_check)

    s = sub.add_parser("import-logs")
    s.add_argument("--csv", required=True)
    s.add_argument("--since")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_import_logs)

    s = sub.add_parser("migrate-v3")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true", help="rebuild v3 files that already exist")
    s.set_defaults(fn=cmd_migrate_v3)

    s = sub.add_parser("authoring-kit")
    s.add_argument("--out")
    s.add_argument("--templates-only", action="store_true", help="only the sheet «Mẫu câu»")
    s.add_argument("--seed", type=int)
    s.set_defaults(fn=cmd_authoring_kit)

    s = sub.add_parser("import-authoring")
    s.add_argument("file")
    s.add_argument("--author", required=True)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_import_authoring)

    s = sub.add_parser("reserve-test-rows")
    s.add_argument("--kit", default=str(KIT))
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_reserve_test_rows)

    s = sub.add_parser("generate-test")
    s.add_argument("--kit", default=str(TEMPLATE_KIT), help="workbook with the sheet «Mẫu câu»")
    s.add_argument("--seed", type=int)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_generate_test)

    s = sub.add_parser("understanding-sets")
    s.add_argument("--partial", type=int, default=60)
    s.add_argument("--composite", type=int, default=30)
    s.add_argument("--seed", type=int, default=20261012)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_understanding_sets)

    s = sub.add_parser("label-frames")
    s.add_argument("--intents", default=str(WORK_V3 / "nhan-y-dinh-dev.xlsx"))
    s.add_argument("--slots", default=str(WORK_V3 / "nhan-slot-dev.xlsx"))
    s.add_argument(
        "--authoring",
        nargs="*",
        default=[str(WORK_V3 / "soan-cau-hoi-test-60.xlsx")],
        help="authoring workbooks with intent and slot columns in sheet «Soạn»",
    )
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_label_frames)

    s = sub.add_parser("frame-sheet")
    s.add_argument("--out", default=str(WORK_V3 / "nhan-slot-dev.xlsx"))
    s.add_argument("--split", choices=["dev", "val", "test"], default="dev")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_frame_sheet)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
