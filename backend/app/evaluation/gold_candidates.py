"""Candidate lists for authoring (plan §2.3): table rows sampled risk-first with
`app.review.sampling.sample_rows`, a fixed seed, zone rotation and a greedy pass that serves
uncovered coverage cells first; clause/definition/registry chunks listed from the strata file.
Every candidate carries the exact `chunk_id`, labels, values, conditions and a JSON skeleton."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.evaluation.gold_resolver import DerivedResolver
from app.evaluation.gold_schema import GoldItem, Stratum
from app.review.sampling import natural_key, risk_score, sample_rows

ZONES = ("A", "B", "C")


@dataclass
class Candidate:
    kind: str  # row | chunk
    key: str  # rule_code or chunk_id
    stratum: str
    label: str
    chunk_id: str | None
    document_id: str
    table_code: str | None = None
    row_id: str | None = None
    row_no: str | None = None
    zone: str | None = None
    parameter: str | None = None
    parameter_id: str | None = None
    unit: str | None = None
    value_text: str | None = None
    ref_o2: float | None = None
    conditions: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    inherits_from: str | None = None
    span: dict[str, Any] = field(default_factory=dict)
    raw_cells: dict[str, str] = field(default_factory=dict)
    text: str = ""
    coverage_cells: list[str] = field(default_factory=list)
    skeleton: dict[str, Any] = field(default_factory=dict)


def _ns(row: dict[str, Any]) -> SimpleNamespace:
    """Adapter so `app.review.sampling` (written for ORM rows) works on derived JSON rows."""
    sp = row.get("span") or {}
    return SimpleNamespace(
        row_id=row["row_id"],
        line_start=sp.get("line_start", 0),
        is_group_header=row.get("is_group_header", False),
        exceptions=row.get("exceptions") or [],
        parse_warnings=row.get("parse_warnings") or [],
        inherits_from=row.get("inherits_from"),
        conditions=row.get("conditions") or [],
        values=row.get("values") or {},
        footnote_refs=row.get("footnote_refs") or [],
        entities=row.get("entities") or {},
        cells=row.get("cells") or {},
        _row=row,
    )


def _cond_texts(conds: list[dict[str, Any]]) -> list[str]:
    out = []
    for c in conds or []:
        t = c.get("text") or f"{c.get('type')} {c.get('op') or ''} {c.get('value')}".strip()
        if c.get("unit"):
            t += f" {c['unit']}"
        if c.get("inherited_from"):
            t += " (kế thừa)"
        out.append(f"[{c.get('type')}] {t}")
    return out


def _coverage_cells(rule: dict[str, Any]) -> list[str]:
    cells = [f"zone={rule.get('zone')}"]
    for c in rule.get("conditions") or []:
        t = c.get("type")
        if t == "flow_regime":
            cells.append("flow=F<=2000" if c.get("op") in ("<=", "<") else "flow=F>2000")
        elif t == "fuel_type":
            cells.append(f"fuel={c.get('value')}")
        elif t == "capacity":
            cells.append(f"capacity={c.get('op')}{c.get('value')}{c.get('unit') or ''}")
        elif t == "receiving_body":
            cells.append(f"rb={c.get('value')}")
        elif t == "equipment_path":
            v = c.get("value")
            if isinstance(v, list):
                cells.append(f"depth={len(v)}")
    if rule.get("ref_o2") is not None:
        cells.append(f"o2={rule['ref_o2']:g}")
    if rule.get("exceptions"):
        cells.append("exception")
    return cells


def _value_text(rule: dict[str, Any]) -> str:
    if rule.get("op") == "between":
        s = f"{rule.get('min')} – {rule.get('max')}"
    else:
        s = f"{rule.get('op')} {rule.get('value')}"
    if rule.get("ref_o2") is not None:
        s += f" (O₂ {rule['ref_o2']:g} %)"
    return s


def candidate_rows(
    resolver: DerivedResolver,
    stratum: Stratum,
    *,
    seed: int,
    n: int,
    exclude_rules: set[str] | None = None,
    covered: set[str] | None = None,
) -> list[Candidate]:
    exclude_rules = exclude_rules or set()
    covered = set(covered or ())
    rows: list[dict[str, Any]] = []
    for tcode in stratum.tables:
        rows += [r for r in resolver.rows(tcode) if not r.get("is_group_header")]
    if not rows:
        return []
    ns_rows = [_ns(r) for r in rows]
    sampled = sample_rows(ns_rows, min(len(ns_rows), max(n * 2, 6)))
    rng = random.Random(f"{seed}:{stratum.code}")
    rng.shuffle(sampled)
    pool: list[tuple[dict[str, Any], dict[str, Any]]] = []
    plain: list[Candidate] = []
    for ns in sampled:
        row = ns._row
        rules = resolver.rules_for_row(row["row_id"])
        if not rules:
            chunk = resolver.chunk(f"chunk:{row['row_id']}")
            if chunk and chunk["chunk_id"] not in exclude_rules:
                sp = row.get("span") or {}
                plain.append(
                    Candidate(
                        kind="row",
                        key=chunk["chunk_id"],
                        stratum=stratum.code,
                        label=chunk["citation_label"],
                        chunk_id=chunk["chunk_id"],
                        document_id=chunk["document_id"],
                        table_code=row.get("table_code"),
                        row_id=row["row_id"],
                        row_no=row.get("row_no"),
                        raw_cells=row.get("raw_cells") or {},
                        span=sp,
                        text=chunk.get("text", ""),
                        skeleton={
                            "source_ref": {
                                "document_id": chunk["document_id"],
                                "file_sha256": sp.get("file_hash"),
                                "line_start": sp.get("line_start"),
                                "line_end": sp.get("line_end"),
                                "table_code": row.get("table_code"),
                                "row_no": row.get("row_no"),
                            },
                            "relevant": [
                                {
                                    "chunk_id": chunk["chunk_id"],
                                    "citation_label": chunk["citation_label"],
                                    "grade": 2,
                                }
                            ],
                            "expected_citation_labels": [chunk["citation_label"]],
                        },
                    )
                )
            continue
        for rule in rules:
            if rule["rule_code"] not in exclude_rules:
                pool.append((row, rule))
    if not pool:
        return plain[:n]
    order = {id(row): i for i, row in enumerate(r for r, _ in pool)}
    used_rows: dict[str, int] = {}
    ordered: list[Candidate] = []

    def gain(rule: dict[str, Any]) -> int:
        return sum(1 for x in _coverage_cells(rule) if x not in covered)

    while pool and len(ordered) < n:
        want_zone = ZONES[len(ordered) % 3]
        pool.sort(
            key=lambda rr: (
                used_rows.get(rr[0]["row_id"], 0),
                -gain(rr[1]),
                0 if rr[1].get("zone") == want_zone else 1,
                -risk_score(_ns(rr[0])),
                order.get(id(rr[0]), 0),
                natural_key(rr[1]["rule_code"]),
            )
        )
        row, rule = pool.pop(0)
        used_rows[row["row_id"]] = used_rows.get(row["row_id"], 0) + 1
        chunk = resolver.chunk(f"chunk:{row['row_id']}")
        cells = _coverage_cells(rule)
        ordered.append(
            Candidate(
                kind="row",
                key=rule["rule_code"],
                stratum=stratum.code,
                label=rule["citation_label"],
                chunk_id=chunk["chunk_id"] if chunk else None,
                document_id=rule["document_id"],
                table_code=rule["table_code"],
                row_id=row["row_id"],
                row_no=row.get("row_no"),
                zone=rule.get("zone"),
                parameter=rule.get("parameter_raw"),
                parameter_id=rule.get("parameter_id"),
                unit=rule.get("unit_raw"),
                value_text=_value_text(rule),
                ref_o2=rule.get("ref_o2"),
                conditions=_cond_texts(rule.get("conditions") or []),
                exceptions=list(rule.get("exceptions") or []),
                footnotes=list(row.get("footnote_refs") or []),
                inherits_from=row.get("inherits_from"),
                span=row.get("span") or {},
                raw_cells=row.get("raw_cells") or {},
                text=(chunk or {}).get("text", ""),
                coverage_cells=cells,
                skeleton=skeleton_for_rule(rule, row, chunk),
            )
        )
        covered |= set(cells)
    return ordered


def skeleton_for_rule(
    rule: dict[str, Any], row: dict[str, Any], chunk: dict[str, Any] | None
) -> dict[str, Any]:
    sp = row.get("span") or {}
    conds = rule.get("conditions") or []
    cond = {"zone": rule.get("zone")}
    for c in conds:
        t = c.get("type")
        if t == "flow_regime":
            cond["flow_band"] = "F<=2000" if c.get("op") in ("<=", "<") else "F>2000"
        elif t == "fuel_type":
            cond["fuel"] = c.get("value")
        elif t == "receiving_body":
            cond["receiving_body"] = c.get("value")
        elif t == "sector_scope":
            v = c.get("value")
            cond["sector_id"] = v[0] if isinstance(v, list) and v else v
        elif t == "equipment_path":
            v = c.get("value")
            cond["equipment_path"] = v[-1] if isinstance(v, list) and v else v
        elif t == "capacity":
            cond["capacity_text"] = c.get("text")
    if rule.get("ref_o2") is not None:
        cond["o2_ref"] = rule["ref_o2"]
    nums = []
    if rule.get("op") == "between":
        nums.append(
            {
                "value": _canon(rule["min"]),
                "display": _canon(rule["min"]),
                "unit": rule.get("unit_raw"),
                "role": "range_min",
                "must_appear": True,
            }
        )
        nums.append(
            {
                "value": _canon(rule["max"]),
                "display": _canon(rule["max"]),
                "unit": rule.get("unit_raw"),
                "role": "range_max",
                "must_appear": True,
            }
        )
    elif rule.get("value") is not None:
        nums.append(
            {
                "value": _canon(rule["value"]),
                "display": _canon(rule["value"]),
                "unit": rule.get("unit_raw"),
                "role": "limit",
                "must_appear": True,
            }
        )
    if rule.get("ref_o2") is not None:
        nums.append(
            {
                "value": _canon(rule["ref_o2"]),
                "display": _canon(rule["ref_o2"]),
                "unit": "%",
                "role": "o2_ref",
                "must_appear": True,
            }
        )
    col = []
    if cond.get("flow_band"):
        col.append(cond["flow_band"])
    col.append(rule.get("zone"))
    return {
        "expected_numbers": nums,
        "expected_conditions": {k: v for k, v in cond.items() if v is not None},
        "target_rule_codes": [rule["rule_code"]],
        "source_ref": {
            "document_id": rule["document_id"],
            "file_sha256": sp.get("file_hash"),
            "line_start": sp.get("line_start"),
            "line_end": sp.get("line_end"),
            "table_code": rule["table_code"],
            "row_no": row.get("row_no"),
            "column_path": col,
        },
        "relevant": [
            {"chunk_id": chunk["chunk_id"], "citation_label": chunk["citation_label"], "grade": 2}
        ]
        if chunk
        else [],
        "expected_citation_labels": [rule["citation_label"]],
    }


def _canon(v: Any) -> str:
    if v is None:
        return ""
    f = float(v)
    s = format(f, "f").rstrip("0").rstrip(".")
    return s or "0"


def candidate_chunks(
    resolver: DerivedResolver,
    stratum: Stratum,
    *,
    seed: int,
    n: int,
    exclude: set[str] | None = None,
) -> list[Candidate]:
    exclude = exclude or set()
    pool: list[dict[str, Any]] = []
    if stratum.chunk_ids:
        pool += [c for cid in stratum.chunk_ids if (c := resolver.chunk(cid))]
    for rid in stratum.rule_ids:
        mr = resolver.monitoring(rid)
        if mr and mr.get("evidence_chunk_id"):
            c = resolver.chunk(mr["evidence_chunk_id"])
            if c:
                pool.append(c)
    if stratum.document_ids and stratum.chunk_types:
        for c in resolver.all_chunks():
            if c["document_id"] in stratum.document_ids and c["chunk_type"] in stratum.chunk_types:
                pool.append(c)
    seen = set()
    uniq = []
    for c in pool:
        if c["chunk_id"] in seen or c["chunk_id"] in exclude:
            continue
        seen.add(c["chunk_id"])
        uniq.append(c)
    rng = random.Random(f"{seed}:{stratum.code}")
    rng.shuffle(uniq)
    out = []
    for c in uniq[:n]:
        sp = c.get("span") or {}
        out.append(
            Candidate(
                kind="chunk",
                key=c["chunk_id"],
                stratum=stratum.code,
                label=c["citation_label"],
                chunk_id=c["chunk_id"],
                document_id=c["document_id"],
                span=sp,
                text=c.get("text", ""),
                skeleton={
                    "source_ref": {
                        "document_id": c["document_id"],
                        "file_sha256": sp.get("file_hash"),
                        "line_start": sp.get("line_start"),
                        "line_end": sp.get("line_end"),
                        "pdf_page": sp.get("pdf_page_start"),
                        "printed_page": sp.get("printed_page"),
                    },
                    "relevant": [
                        {
                            "chunk_id": c["chunk_id"],
                            "citation_label": c["citation_label"],
                            "grade": 2,
                        }
                    ],
                    "expected_citation_labels": [c["citation_label"]],
                },
            )
        )
    return out


def used_keys(items: list[GoldItem]) -> tuple[set[str], set[str]]:
    rules = {rc for it in items for rc in it.target_rule_codes}
    chunks = {r.chunk_id for it in items for r in it.relevant}
    return rules, chunks


def render_markdown(cands: list[Candidate], raw_lines: dict[str, list[str]] | None = None) -> str:
    lines = [f"# Ứng viên — {cands[0].stratum if cands else ''}", ""]
    for i, c in enumerate(cands, start=1):
        lines.append(f"## #{i} {c.key}")
        lines.append("")
        lines.append(f"- Nhãn: `{c.label}`")
        if c.chunk_id:
            lines.append(f"- chunk_id: `{c.chunk_id}`")
        if c.kind == "row":
            lines.append(
                f"- Thông số: {c.parameter} ({c.parameter_id}) · đơn vị {c.unit}"
                f" · cột **{c.zone}** · giá trị **{c.value_text}**"
            )
            if c.conditions:
                lines.append(f"- Điều kiện: {'; '.join(c.conditions)}")
            if c.exceptions:
                lines.append(f"- Ngoại lệ: {'; '.join(c.exceptions)}")
            if c.inherits_from:
                lines.append(f"- Kế thừa từ: `{c.inherits_from}`")
            if c.footnotes:
                lines.append(f"- Chú thích: {', '.join(c.footnotes)}")
            lines.append(f"- Ô thô: {json.dumps(c.raw_cells, ensure_ascii=False)}")
            lines.append(f"- Phủ: {', '.join(c.coverage_cells)}")
        sp = c.span
        lines.append(
            f"- Nguồn: {c.document_id} dòng {sp.get('line_start')}–{sp.get('line_end')}"
            + (f", trang {sp.get('printed_page')}" if sp.get("printed_page") else "")
        )
        if raw_lines and c.document_id in raw_lines and sp.get("line_start"):
            src = raw_lines[c.document_id][
                sp["line_start"] - 1 : sp.get("line_end", sp["line_start"])
            ]
            lines.append("")
            lines.append("```")
            lines += [s[:400] for s in src]
            lines.append("```")
        if c.text:
            lines.append("")
            lines.append(f"> {c.text[:500]}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(c.skeleton, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)
