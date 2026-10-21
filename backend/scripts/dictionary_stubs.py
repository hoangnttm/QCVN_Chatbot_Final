"""Propose dictionary entries for a document from its parsed tables (plan 2026-10-06 G1, 0.9).

    python backend/scripts/dictionary_stubs.py --doc QCVN_08_2023            # print the proposal
    python backend/scripts/dictionary_stubs.py --doc QCVN_08_2023 --out f.yaml

Needs the registry entry and the grammar of the document; reads the source, parses structure and
tables in memory and compares with data/dictionaries. Nothing is written to the dictionaries:
a person completes the stubs (group, phase, aliases) and moves them in.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.ingestion.pipeline import parse_structure_for, parse_tables_for  # noqa: E402
from app.ontology.loader import load_dictionaries  # noqa: E402
from app.ontology.stubs import propose  # noqa: E402
from app.registry.loader import load_registry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True, help="document_id in data/registry/documents.yaml")
    ap.add_argument("--domain", help="domain code; default: the document's domain in the registry")
    ap.add_argument("--out", help="write the proposal to this YAML file instead of printing it")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings()
    doc = load_registry(settings=settings).by_id(args.doc)
    structure = parse_structure_for(doc, settings)
    tables = parse_tables_for(doc, structure, settings)
    proposal = propose(tables, load_dictionaries(settings=settings), args.domain or doc.domain)
    text = yaml.safe_dump(proposal, allow_unicode=True, sort_keys=False, width=10000)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{args.out}: " + ", ".join(f"{k}={len(v)}" for k, v in proposal.items()))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
