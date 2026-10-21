"""Independent verification of the limit rules (plan 2026-10-06, G2 step 2.4, decision D37).

A rule is `auto_verified` when a second reader of the source — `rawtables.RawTableReader`, written
separately from the ingestion parser and working on the raw table lines — finds the same cell with
the same zone, operator, value(s), O₂ reference and unit. Disagreements and cells only one side
sees stay `pending` and are listed, so a person looks at exactly those.

The result is an ingestion artefact, `data/derived/<document>/verification.json`, read by the
loader. Documents without an independent reader get no file: all of their rules stay `pending`.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.evaluation.gold_generate import crosscheck
from app.evaluation.gold_resolver import DerivedResolver
from app.evaluation.rawtables import TABLE_SPECS

METHOD = "independent_reader"
FILE = "verification.json"


def verify(settings: Settings) -> dict[str, dict[str, Any]]:
    """Per document: rule codes the independent reader confirms, and what it disputes."""
    findings = crosscheck(settings)
    disputed: dict[str, str] = {}
    unmatched_cells: list[dict[str, Any]] = []
    for f in findings:
        if f.get("rule_code"):
            disputed[f["rule_code"]] = f"{f['kind']}: {f.get('detail', '')}"
        else:
            unmatched_cells.append(f)
    out: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "method": METHOD,
            "tables": [],
            "verified": [],
            "disputed": {},
            "unmatched_cells": [],
        }
    )
    for rule in DerivedResolver(settings).all_rules():
        if rule["table_code"] not in TABLE_SPECS:
            continue
        doc = out[rule["document_id"]]
        if rule["table_code"] not in doc["tables"]:
            doc["tables"].append(rule["table_code"])
        if rule["rule_code"] in disputed:
            doc["disputed"][rule["rule_code"]] = disputed[rule["rule_code"]]
        else:
            doc["verified"].append(rule["rule_code"])
    for f in unmatched_cells:
        table = str(f.get("cell", "")).split(".r")[0]
        for doc in out.values():
            if table in doc["tables"]:
                doc["unmatched_cells"].append(f)
    return dict(out)


def write(settings: Settings) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    for doc_id, result in verify(settings).items():
        path = settings.data_dir / "derived" / doc_id / FILE
        result = {"generated_at": stamp, **result}
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        paths[doc_id] = path
    return paths
