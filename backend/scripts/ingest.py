"""Run ingestion stages and print the coverage / node-count report.

Usage:
    python backend/scripts/ingest.py structure [--doc QCVN_40_2025 ...] [--show-uncovered N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.ingestion.pipeline import (  # noqa: E402
    run_all,
    run_pdf_extract,
    run_structure,
    run_tables,
    write_quality_report,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "stage",
        choices=[
            "pdf",
            "structure",
            "tables",
            "all",
            "report",
            "verify",
            "quality",
            "load",
            "db",
            "embed",
            "activate",
            "bm25",
        ],
    )
    ap.add_argument("--label", default=None, help="activate: index_versions.label")
    ap.add_argument("--doc", action="append", help="document_id (repeatable); default: all")
    ap.add_argument("--dry-run", action="store_true", help="embed: only estimate, no API calls")
    ap.add_argument("--batch", type=int, default=None, help="embed: texts per request")
    ap.add_argument("--limit", type=int, default=None, help="embed: stop after N chunks")
    ap.add_argument("--show-uncovered", type=int, default=15)
    ap.add_argument("--kinds", action="store_true", help="print node kind counts")
    ap.add_argument("--show-warnings", type=int, default=8)
    ap.add_argument(
        "--partial", action="store_true", help="PDF: parse only the docling batches cached so far"
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings(_env_file=None)

    if args.stage == "verify":
        # second, independent reading of the threshold tables -> verification.json per document
        from app.evaluation.verification import write as write_verification

        for doc_id, path in write_verification(settings).items():
            result = json.loads(path.read_text(encoding="utf-8"))
            print(
                f"[verify] {doc_id}: verified={len(result['verified'])} "
                f"disputed={len(result['disputed'])} unmatched_cells={len(result['unmatched_cells'])}"
            )
        return 0

    if args.stage == "quality":
        # gate of the fact layer: computed from the artefacts, written as JSON + Markdown
        import datetime as dt

        from app.ingestion.quality import corpus_quality, render
        from app.ontology.loader import load_dictionaries
        from app.registry.loader import load_registry

        rows = corpus_quality(
            load_registry(settings=settings), load_dictionaries(settings=settings), settings
        )
        today = dt.date.today().isoformat()
        out_dir = settings.data_dir.parent / "docs" / "reports"
        (out_dir / f"fact-quality-{today}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (out_dir / f"fact-quality-{today}.md").write_text(render(rows, today), encoding="utf-8")
        for r in rows:
            print(
                f"[quality] {r['document_id']}: {'PASS' if r['passed'] else 'FAIL'} "
                f"cells={r['value_cells']} rules={r['limit_rules']} trust={r['trust']} "
                f"facts={sum(r['facts_by_type'].values())}"
            )
        return 0 if all(r["passed"] for r in rows) else 1

    if args.stage == "report":
        out = write_quality_report(settings)
        print("written:", out)
        return 0

    if args.stage in ("load", "db"):
        from app.db.loader import db_stats, load_documents
        from app.db.session import database_reachable, session_scope

        settings = Settings()  # reads backend/.env (DATABASE_URL)
        if not database_reachable(settings):
            print(f"database not reachable: {settings.database_url}")
            return 1
        if args.stage == "load":
            for doc_id, counts in load_documents(args.doc, settings).items():
                print(f"[load] {doc_id}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        with session_scope(settings) as session:
            print("[db]", ", ".join(f"{k}={v}" for k, v in db_stats(session).items()))
        return 0

    if args.stage in ("activate", "bm25"):
        from sqlalchemy import func, select, update

        from app.db import models as m
        from app.db.session import database_reachable, session_scope
        from app.retrieval.retriever import RetrievalService

        settings = Settings()
        if not database_reachable(settings):
            print(f"database not reachable: {settings.database_url}")
            return 1
        with session_scope(settings) as session:
            if args.stage == "activate":
                if not args.label:
                    print(
                        "--label required; available:",
                        [v.label for v in session.execute(select(m.IndexVersion)).scalars()],
                    )
                    return 1
                session.execute(
                    update(m.IndexVersion)
                    .where(m.IndexVersion.status == "active")
                    .values(status="inactive")
                )
                n = session.execute(
                    update(m.IndexVersion)
                    .where(m.IndexVersion.label == args.label)
                    .values(status="active", activated_at=func.now())
                ).rowcount
                if not n:
                    print("index version not found:", args.label)
                    return 1
                print("activated:", args.label)
            svc = RetrievalService(settings)
            print("[bm25]", svc.reload(session))
        return 0

    if args.stage == "embed":
        from app.db.session import database_reachable
        from app.retrieval.index import embed_pending, pending_summary

        settings = Settings()
        if not database_reachable(settings):
            print(f"database not reachable: {settings.database_url}")
            return 1
        summary = pending_summary(settings, args.doc)
        print(
            f"[embed] provider={settings.embedding_provider} model={settings.embedding_model} "
            f"dim={settings.embedding_dim} " + ", ".join(f"{k}={v}" for k, v in summary.items())
        )
        if args.dry_run or not summary["pending_chunks"]:
            return 0
        from app.retrieval.embedder import get_embedder

        embedder = get_embedder(settings)
        result = embed_pending(embedder, settings, args.doc, args.batch, args.limit, progress=print)
        extra = {
            k: getattr(embedder, k)
            for k in ("requests", "retries", "truncated")
            if hasattr(embedder, k)
        }
        print("[embed] " + ", ".join(f"{k}={v}" for k, v in {**result, **extra}.items()))
        return 0

    if args.stage == "pdf":
        for doc_id, info in run_pdf_extract(args.doc, settings).items():
            print(
                f"[pdf] {doc_id}: lines={info['lines']} tables={info['tables']} "
                f"failed_pages={info['failed_pages']} cache={info['cache']}"
            )
        return 0

    if args.stage == "all":
        failed = False
        for doc_id, art in run_all(args.doc, settings, allow_partial=args.partial).items():
            rep = art.report
            # informational notes (sanitize, scope partition, auditable docling fix-ups) do not
            # fail the gate; parse warnings, unresolved entities and registry mismatches do
            info = ("sanitize:", "scope:", "postprocess:", "PARTIAL:")
            blocking = [w for w in rep.warnings if not w.startswith(info) and "spill-over" not in w]
            status = (
                "OK "
                if rep.coverage_ratio == 1.0 and not rep.unresolved_entities and not blocking
                else "CHK"
            )
            print(
                f"[{status}] {doc_id} ({rep.text_status}): coverage={rep.coverage_ratio:.1%} "
                f"rows={sum(rep.table_row_counts.values())} rules={rep.rules} chunks={rep.chunks} "
                f"unresolved={len(rep.unresolved_entities)} warnings={len(rep.warnings)}"
            )
            for u in rep.unresolved_entities[: args.show_warnings]:
                print("      unresolved:", u)
            for w in rep.warnings[: args.show_warnings]:
                print("      warn:", w)
            failed |= status != "OK "
        return 1 if failed else 0

    if args.stage == "tables":
        failed = False
        for doc_id, pt in run_tables(args.doc, settings).items():
            print(f"[{doc_id}] tables={len(pt.tables)} warnings={len(pt.warnings)}")
            for w in pt.warnings:
                print("      warn:", w)
            for t in pt.tables:
                rows = t.rows
                n_warn = sum(1 for r in rows if r.parse_warnings)
                n_vals = sum(len(r.values) for r in rows)
                print(
                    f"   {t.table_code:22} rows={len(rows):4} (group={sum(r.is_group_header for r in rows):3}) "
                    f"values={n_vals:4} rows_with_warnings={n_warn}"
                )
                shown = 0
                for r in rows:
                    for w in r.parse_warnings:
                        if shown < args.show_warnings:
                            print(f"        L{r.span.line_start} {r.row_id}: {w}")
                            shown += 1
                failed |= n_warn > 0
        return 1 if failed else 0

    failed = False
    results = run_structure(args.doc, settings, allow_partial=args.partial)
    for doc_id, parsed in results.items():
        cov = parsed.coverage
        status = "OK " if cov.ratio == 1.0 and not cov.uncovered else "GAP"
        print(
            f"[{status}] {doc_id}: lines {parsed.line_range}, nodes={len(parsed.nodes)}, "
            f"coverage={cov.covered}/{cov.total_nonblank} ({cov.ratio:.1%}), warnings={len(parsed.warnings)}"
        )
        if args.kinds:
            print("      kinds:", parsed.kind_counts())
        for no, txt in cov.uncovered[: args.show_uncovered]:
            print(f"      uncovered L{no}: {txt}")
        for w in parsed.warnings[: args.show_warnings]:
            print(f"      warn: {w}")
        failed |= status == "GAP"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
