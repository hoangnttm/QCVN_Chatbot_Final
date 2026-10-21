"""Fingerprint of a Neo4j projection, to compare two builds (read-only).

Writes one JSON file: counts per label / relationship type, and three sorted lists hashed with
sha256 — nodes with their properties, edges as (source id, type, target id), and edges with their
properties. Build stamps (`projection_version`, the `Meta` node) and the vectors are left out; the
number of vectors is counted.

    python backend/scripts/graph_fingerprint.py --database neo4j --out graph-neo4j.json
    python backend/scripts/graph_fingerprint.py --diff graph-neo4j.json graph-qcvnv2.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import GraphDatabase  # noqa: E402

from app.core.config import Settings  # noqa: E402

SKIP = {"projection_version", "embedding"}


def _line(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _sha(lines: list[str]) -> str:
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def fingerprint(settings: Settings, database: str) -> dict:
    with (
        GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        ) as driver,
        driver.session(database=database) as s,
    ):
        nodes, labels = [], Counter()
        for r in s.run(
            "MATCH (n) WHERE NOT n:Meta RETURN n.id AS id, labels(n) AS labels, properties(n) AS p"
        ):
            props = {k: v for k, v in r["p"].items() if k not in SKIP}
            nodes.append(_line([r["id"], sorted(r["labels"]), props]))
            labels.update(r["labels"])
        triples, edges, rels = [], [], Counter()
        for r in s.run(
            "MATCH (a)-[e]->(b) RETURN a.id AS a, type(e) AS t, b.id AS b, properties(e) AS p"
        ):
            props = {k: v for k, v in r["p"].items() if k not in SKIP}
            triples.append(_line([r["a"], r["t"], r["b"]]))
            edges.append(_line([r["a"], r["t"], r["b"], props]))
            rels[r["t"]] += 1
        vectors = s.run(
            "MATCH (n:EvidenceChunk) WHERE n.embedding IS NOT NULL RETURN count(n) AS c"
        ).single()["c"]
        meta = s.run("MATCH (m:Meta) RETURN properties(m) AS p").data()
    nodes.sort()
    triples.sort()
    edges.sort()
    return {
        "database": database,
        "labels": dict(sorted(labels.items())),
        "relationships": dict(sorted(rels.items())),
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "n_distinct_triples": len(set(triples)),
        "vectors": vectors,
        "sha_nodes": _sha(nodes),
        "sha_triples": _sha(triples),
        "sha_edges": _sha(edges),
        "meta": json.loads(_line(meta)),
        "nodes": nodes,
        "edges": edges,
    }


def diff(a: dict, b: dict, show: int) -> int:
    different = 0
    for key in ("labels", "relationships"):
        for name in sorted(set(a[key]) | set(b[key])):
            x, y = a[key].get(name, 0), b[key].get(name, 0)
            if x != y:
                print(f"{key} {name}: {x} -> {y}")
    for key in ("nodes", "edges"):
        sa, sb = set(a[key]), set(b[key])
        only_a, only_b = sorted(sa - sb), sorted(sb - sa)
        different += len(only_a) + len(only_b)
        print(f"{key}: {len(sa)} vs {len(sb)}; only first={len(only_a)} only second={len(only_b)}")
        for line in only_a[:show]:
            print("   - " + line[:300])
        for line in only_b[:show]:
            print("   + " + line[:300])
    print(f"vectors: {a['vectors']} vs {b['vectors']}")
    print("IDENTICAL" if not different and a["vectors"] == b["vectors"] else "DIFFERENT")
    return 0 if not different else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--diff", nargs=2, metavar=("FIRST", "SECOND"))
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.diff:
        first, second = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.diff)
        return diff(first, second, args.show)
    settings = Settings()
    fp = fingerprint(settings, args.database or settings.neo4j_database)
    if args.out:
        Path(args.out).write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
    print(_line({k: v for k, v in fp.items() if k not in ("nodes", "edges")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
