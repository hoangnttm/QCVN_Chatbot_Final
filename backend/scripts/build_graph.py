"""Rebuild the Neo4j projection from PostgreSQL (idempotent) or print graph counts.

python backend/scripts/build_graph.py            # build
python backend/scripts/build_graph.py --counts   # labels / relationship counts only
python backend/scripts/build_graph.py --check    # is the graph still the projection of this database?
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.graph.build_graph import build, graph_counts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--counts", action="store_true")
    ap.add_argument(
        "--check", action="store_true", help="invariants of the projection against PostgreSQL"
    )
    ap.add_argument("--database", default=None, help="default: NEO4J_DATABASE")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings()
    database = args.database or settings.neo4j_database
    if args.counts:
        print(json.dumps(graph_counts(settings, database), ensure_ascii=False, indent=1))
        return 0
    if args.check:
        from app.graph.check import render, verify

        with session_scope(settings) as session:
            rows = verify(session, settings, database)
        print(render(rows))
        return 0 if all(r["ok"] for r in rows) else 1
    # say which load of the corpus goes into which graph: the two stores are versioned together
    source = settings.database_url.rsplit("/", 1)[-1]
    print(f"[graph] PostgreSQL {source} -> Neo4j {database}", file=sys.stderr)
    with session_scope(settings) as session:
        stats = build(session, settings, database)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
