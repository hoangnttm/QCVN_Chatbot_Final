"""Quality gate of the fact layer (plan 2026-10-06, G2 step 2.6).

Computed from the ingestion artefacts of each document (no database): how completely the value
cells became facts, whether every fact has a legal address, how well entities resolved, which
conditions are on declared dimensions, what an independent reader confirmed, and which statements
could not become facts because nothing citable carries them. A document passes when the three
hard criteria hold; everything else is reported so a person can decide.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.db import facts as fact_layer
from app.db.loader import read_verified
from app.ingestion.address import is_resolvable
from app.ingestion.axes import ColumnSchema
from app.ingestion.models import (
    EvidenceChunk,
    ExtractedRelations,
    LimitRule,
    MonitoringRequirement,
    ParsedStructure,
    ParsedTables,
)
from app.ontology.schema import Dictionaries
from app.registry.schema import Registry

CELL_LEVELS = {"Table", "AppendixTable", "TableRow", "Column"}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def document_quality(
    doc_id: str, reg: Registry, d: Dictionaries, settings: Settings
) -> dict[str, Any]:
    base = settings.data_dir / "derived" / doc_id
    doc = reg.by_id(doc_id)
    structure = ParsedStructure.model_validate(_read(base / "structure.json"))
    tables = ParsedTables.model_validate(_read(base / "tables.json"))
    rules = [LimitRule.model_validate(r) for r in _read(base / "rules.json")]
    relations = ExtractedRelations.model_validate(_read(base / "relations.json"))
    monitoring = (
        [MonitoringRequirement.model_validate(q) for q in _read(base / "monitoring.json")]
        if (base / "monitoring.json").exists()
        else []
    )
    chunks = [EvidenceChunk.model_validate(c) for c in _read(base / "chunks.json")]
    verified = read_verified(base)
    validity = fact_layer.document_validity(doc_id, reg)

    # value cells of the threshold tables vs the rules derived from them
    cells = unparsed = 0
    for t in tables.tables:
        if not ColumnSchema.from_config(t.config).has_values:
            continue
        for r in t.data_rows():
            for v in r.values.values():
                if v.op == "unparsed":
                    unparsed += 1
                else:
                    cells += 1
    rule_codes = [r.rule_code for r in rules]
    addressed = sum(
        is_resolvable(r.address)
        and CELL_LEVELS & {p["level"] for p in r.address} >= {"TableRow", "Column"}
        for r in rules
    )
    known_dims = {x.name for x in d.condition_dimensions}
    dims = Counter(c.type for r in rules for c in r.conditions)
    undeclared = {k: n for k, n in dims.items() if k not in known_dims}

    facts = fact_layer.fact_rows(
        doc, structure, tables, rules, relations, monitoring, chunks, validity, verified
    )
    by_type = Counter(f["fact_type"] for f in facts)
    chunk_ids = {c.chunk_id for c in chunks}
    not_citable = {
        "transition": sum(
            f"chunk:{t.source_node_id}" not in chunk_ids for t in relations.transition_rules
        ),
        "monitoring": sum(q.evidence_chunk_id not in chunk_ids for q in monitoring),
        "relation": sum(
            not (rel.source_node_id and f"chunk:{rel.source_node_id}" in chunk_ids)
            for rel in relations.relations
        ),
        "registry_effectivity": sum(f.fact_type in fact_layer.REGISTRY_FACT_TYPE for f in doc.facts)
        - by_type.get("effectivity", 0),
    }
    criteria = {
        "every_value_cell_has_one_rule": cells == len(rules) == len(set(rule_codes)),
        "every_fact_has_a_legal_address": all(is_resolvable(f["address"]) for f in facts)
        and addressed == len(rules),
        "every_condition_on_a_declared_dimension": not undeclared,
    }
    return {
        "document_id": doc_id,
        "passed": all(criteria.values()),
        "criteria": criteria,
        "value_cells": cells,
        "unparsed_cells": unparsed,
        "limit_rules": len(rules),
        "rules_with_cell_address": addressed,
        "rules_with_cell_text": sum(bool(r.cell_raw) for r in rules),
        "parameter_resolved": _share(sum(bool(r.parameter_id) for r in rules), len(rules)),
        "unit_resolved": _share(sum(bool(r.unit_id) for r in rules), len(rules)),
        "conditions_by_dimension": dict(sorted(dims.items())),
        "undeclared_dimensions": undeclared,
        "trust": dict(
            Counter(fact_layer.trust_of(r.review_status, r.rule_code in verified) for r in rules)
        ),
        "facts_by_type": dict(sorted(by_type.items())),
        "facts_without_chunk": sum(f["chunk_id"] is None for f in facts),
        "statements_not_citable": {k: v for k, v in not_citable.items() if v},
        "valid_from": str(validity["valid_from"]) if validity["valid_from"] else None,
        "valid_to": str(validity["valid_to"]) if validity["valid_to"] else None,
        "validity_basis": validity["basis"],
    }


def corpus_quality(reg: Registry, d: Dictionaries, settings: Settings) -> list[dict[str, Any]]:
    out = []
    for doc in reg.documents:
        if (settings.data_dir / "derived" / doc.document_id / "rules.json").exists():
            out.append(document_quality(doc.document_id, reg, d, settings))
    return out


def render(rows: list[dict[str, Any]], date: str) -> str:
    lines = [
        f"# Cổng chất lượng lớp fact — {date}",
        "",
        "Tính từ kết quả ingestion của từng văn bản (`data/derived/<văn bản>/`). Ba tiêu chí cứng: "
        "mỗi ô giá trị có đúng một rule; mọi fact có địa chỉ pháp lý; mọi điều kiện thuộc một chiều "
        "đã khai báo.",
        "",
        "| Văn bản | Qua cổng | Ô giá trị | Rule | Có địa chỉ tới ô | Thông số nhận ra | Đơn vị nhận ra "
        "| auto_verified | pending | Hiệu lực từ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['document_id']} | {'đạt' if r['passed'] else '**không**'} | {r['value_cells']} "
            f"| {r['limit_rules']} | {r['rules_with_cell_address']} | {r['parameter_resolved']} "
            f"| {r['unit_resolved']} | {r['trust'].get('auto_verified', 0)} "
            f"| {r['trust'].get('pending', 0)} | {r['valid_from']} |"
        )
    lines += ["", "## Fact theo loại", "", "| Văn bản | " + " | ".join(_types(rows)) + " |"]
    lines.append("|---|" + "---|" * len(_types(rows)))
    for r in rows:
        lines.append(
            f"| {r['document_id']} | "
            + " | ".join(str(r["facts_by_type"].get(t, 0)) for t in _types(rows))
            + " |"
        )
    lines += ["", "## Khẳng định chưa trích dẫn được (không thành fact)", ""]
    for r in rows:
        if r["statements_not_citable"]:
            lines.append(f"- {r['document_id']}: {r['statements_not_citable']}")
    lines += ["", "## Căn cứ của khoảng hiệu lực", ""]
    for r in rows:
        lines.append(
            f"- {r['document_id']}: {r['valid_from']} → {r['valid_to']}; {r['validity_basis']}"
        )
    lines += ["", "## Điều kiện theo chiều", ""]
    for r in rows:
        if r["conditions_by_dimension"]:
            lines.append(f"- {r['document_id']}: {r['conditions_by_dimension']}")
    return "\n".join(lines) + "\n"


def _types(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({t for r in rows for t in r["facts_by_type"]})
