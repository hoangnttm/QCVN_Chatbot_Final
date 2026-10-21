"""Tier A — template-generated table-lookup questions from the raw source (plan §2.7).

Cells come from `rawtables.py` (independent reader), the wording from
`data/gold/templates_v2.yaml`, the parser's rule for the same cell is only *compared* (cross-check)
and recorded as `target_rule_codes`; disagreements are written to `parser_findings.jsonl` and the
item keeps the source value with tag `parser_mismatch`."""

from __future__ import annotations

import csv
import datetime as dt
import random
import re
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings
from app.evaluation.gold_resolver import DerivedResolver
from app.evaluation.gold_schema import (
    ExpectedBehaviour,
    ExpectedConditions,
    ExpectedNumber,
    ForbiddenNumber,
    GoldItem,
    Provenance,
    RelevantChunk,
    SourceRef,
    Verification,
    compute_difficulty,
)
from app.evaluation.gold_source import Derived, RawSource
from app.evaluation.rawtables import TABLE_SPECS, RawCell, RawTableReader, canon, cell_matches_rule

FUEL_KEYWORDS = [
    ("sinh khối", "biomass_solid"),
    ("biogas", "biogas"),
    ("khí thu hồi", "gas"),
    ("nhiên liệu khí", "gas"),
    ("nhiên liệu lỏng", "liquid"),
    ("nhiên liệu rắn", "solid"),
]
DOMAIN_OF_TABLE = {
    "QCVN40_2025.B1": "wastewater",
    "QCVN40_2025.B2": "wastewater",
    "QCVN19_2024.B1": "emission",
    "QCVN19_2024.B2": "emission",
}
FOOTNOTE_B1 = "chunk:QCVN_40_2025/Section2/Section2.1/Table1/Footnote#25"
UNIT_DISPLAY = {"mg/L": "mg/L", "mg/Nm³": "mg/Nm³", "mg/Nm3": "mg/Nm³"}
# QCVN 19 rows whose label itself names a case with no reference O2 (glass furnaces fed with
# pure oxygen); the expected answer repeats that exception (PO review 2026-10-20)
O2_EXEMPT_MARK = "không áp dụng ôxy tham chiếu"
O2_EXEMPT_NOTE = (
    " (riêng sản xuất sợi thủy tinh sử dụng ôxy tinh khiết: không áp dụng ôxy tham chiếu)"
)


def load_templates(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def equipment_phrase(cell: RawCell) -> str:
    parts = cell.row_label_path[1:]
    if not parts:
        return "thiết bị xả khí thải công nghiệp"
    out = []
    for i, p in enumerate(parts):
        t = p.strip()
        if i > 0:
            t = re.sub(r"^Thiết bị (có |sử dụng )?", lambda m: m.group(1) or "", t)
        out.append(_lower_first(t))
    return ", ".join(out)


def infer_fuel(cell: RawCell) -> str | None:
    text = " ".join(cell.row_label_path[1:]).lower()
    for kw, code in FUEL_KEYWORDS:
        if kw in text:
            return code
    return None


def capacity_text(cell: RawCell) -> str | None:
    for p in reversed(cell.row_label_path[1:]):
        if re.search(r"công suất|MW|tấn/giờ|Kcal", p):
            return p
    return None


def capacity_family(text: str | None) -> str | None:
    if not text:
        return None
    if "20 tấn" in text or "12.380.000" in text:
        return "steam20"
    if "MW" in text:
        return "mw"
    if "tấn/giờ" in text:
        return "tph"
    return "other"


def _features(cell: RawCell) -> set[str]:
    f = {f"zone={cell.zone}", f"param={cell.row_no}"}
    if cell.flow_band:
        f.add(f"flow={cell.flow_band}")
    if cell.o2_ref is not None:
        f.add(f"o2={canon(cell.o2_ref)}")
    fuel = infer_fuel(cell)
    if fuel:
        f.add(f"fuel={fuel}")
    if cell.equipment_path:
        f.add(f"depth={cell.equipment_path.count('.') + 1}")
    fam = capacity_family(capacity_text(cell))
    if fam:
        f.add(f"cap={fam}")
    return f


def sample_cells(cells: list[RawCell], n: int, rng: random.Random) -> list[RawCell]:
    """Greedy feature coverage first (every zone/O₂/fuel/depth/capacity family/parameter),
    then seeded random fill up to n."""
    if n >= len(cells):
        return list(cells)
    pool = list(cells)
    rng.shuffle(pool)
    covered: set[str] = set()
    chosen: list[RawCell] = []
    while pool and len(chosen) < n:
        best = max(pool, key=lambda c: len(_features(c) - covered))
        if not (_features(best) - covered):
            break
        chosen.append(best)
        covered |= _features(best)
        pool.remove(best)
    rest = pool[: max(0, n - len(chosen))]
    chosen += rest
    order = {id(c): i for i, c in enumerate(cells)}
    return sorted(chosen, key=lambda c: order[id(c)])


class Generator:
    def __init__(self, settings: Settings, templates: Path, seed: int) -> None:
        self.settings = settings
        self.raw = RawSource(settings)
        self.reader = RawTableReader(self.raw)
        self.resolver = DerivedResolver(settings)
        self.derived = Derived(settings)
        self.t = load_templates(templates)
        self.rng = random.Random(seed)
        self.seed = seed
        self.findings: list[dict[str, Any]] = []

    # ------------------------------------------------------------ phrasing

    def _param_phrase(self, cell: RawCell) -> str:
        aliases = self.t.get("parameter_aliases", {})
        key = cell.parameter_short or ""
        opts = [cell.parameter]
        if key and key in aliases:
            opts += aliases[key]
        elif key:
            opts.append(key)
        # a short form drops a trailing "(...)" or ":" part, never a comma-separated member
        # ("Tổng kim loại Cd, Tl và hợp chất tương ứng" is one group, not Cd alone) and never a
        # parenthesis that lists the members of a group ("Tổng các kim loại (bao gồm: Sb, …)")
        head = re.split(r"[(:]", cell.parameter)[0].strip()
        if 3 <= len(head) <= 45 and head != cell.parameter and "(bao gồm" not in cell.parameter:
            opts.append(head)
        return self.rng.choice(opts)

    def _zone_phrase(self, domain: str, zone: str) -> str:
        return self.rng.choice(self.t["zone_phrases"][domain][zone])

    def _flow_phrase(self, band: str) -> tuple[str, int]:
        flow = self.rng.choice(self.t["flow_samples"][band])
        tpl = self.rng.choice(self.t["flow_phrases"])
        return tpl.format(flow=flow, flow_dot=f"{flow:,}".replace(",", ".")), flow

    def question_for(self, cell: RawCell) -> tuple[str, str, dict[str, Any]]:
        kind = TABLE_SPECS[cell.table_code]["kind"]
        domain = DOMAIN_OF_TABLE[cell.table_code]
        slots: dict[str, Any] = {
            "param": self._param_phrase(cell),
            "zone": self._zone_phrase(domain, cell.zone),
        }
        if kind == "flow_zone":
            phrase, flow = self._flow_phrase(cell.flow_band or "F<=2000")
            slots["flow"] = phrase
            slots["flow_value"] = flow
            tpl = self.rng.choice(self.t["templates"]["flow_zone"])
        elif kind == "zone":
            row_key = f"{cell.table_code}.r{cell.row_key}"
            if row_key in self.t.get("row_phrases", {}):
                slots["row_phrase"] = self.rng.choice(self.t["row_phrases"][row_key])
                tpl = self.rng.choice(self.t["templates"]["zone_sub"])
            else:
                tpl = self.rng.choice(self.t["templates"]["zone"])
        else:
            eq = equipment_phrase(cell)
            slots["equipment"] = eq
            slots["equipment_cap"] = eq[:1].upper() + eq[1:]
            slots["o2_suffix"] = (
                self.rng.choice(self.t["o2_suffix"]) if cell.o2_ref is not None else ""
            )
            tpl = self.rng.choice(self.t["templates"]["equipment_zone"])
        text = tpl["text"].format(**slots)
        text = re.sub(r"\s+", " ", text).replace(" ,", ",").replace(" ?", "?").strip()
        if not text.startswith("pH"):
            text = text[:1].upper() + text[1:]
        return text, tpl["id"], slots

    # ------------------------------------------------------------ items

    def _rule_for(self, cell: RawCell) -> dict[str, Any] | None:
        code = f"{cell.table_code}.r{cell.row_key}.{'.'.join(cell.column_path)}"
        return self.resolver.rule(code)

    def _expected_answer(self, cell: RawCell) -> str:
        unit = UNIT_DISPLAY.get(cell.unit, cell.unit)
        if unit in ("-", "–", "—"):
            unit = ""
        if cell.op == "between":
            core = f"{cell.display} {unit}".strip()
        else:
            core = f"{cell.display.split('(')[0].strip()} {unit}".strip()
        if cell.o2_ref is not None:
            core += f", ôxy tham chiếu {canon(cell.o2_ref)} %"
            if any(O2_EXEMPT_MARK in lbl for lbl in cell.row_label_path):
                core += O2_EXEMPT_NOTE  # the row itself names a case without a reference O2
        return core

    def _numbers(self, cell: RawCell) -> list[ExpectedNumber]:
        out = []
        if cell.op == "between":
            out.append(
                ExpectedNumber(
                    value=canon(cell.vmin),
                    display=_disp(cell.vmin),
                    unit=cell.unit,
                    role="range_min",
                )
            )
            out.append(
                ExpectedNumber(
                    value=canon(cell.vmax),
                    display=_disp(cell.vmax),
                    unit=cell.unit,
                    role="range_max",
                )
            )
        else:
            out.append(
                ExpectedNumber(
                    value=canon(cell.value), display=_disp(cell.value), unit=cell.unit, role="limit"
                )
            )
        if cell.o2_ref is not None:
            out.append(
                ExpectedNumber(
                    value=canon(cell.o2_ref), display=canon(cell.o2_ref), unit="%", role="o2_ref"
                )
            )
        return out

    def _forbidden(self, cell: RawCell, siblings: list[RawCell]) -> list[ForbiddenNumber]:
        mine = {canon(cell.value), canon(cell.vmin), canon(cell.vmax)} - {""}
        out = []
        seen = set()
        for s in siblings:
            if s.row_key != cell.row_key or s is cell:
                continue
            for v in (s.value, s.vmin, s.vmax):
                cv = canon(v)
                if cv and cv not in mine and cv not in seen:
                    seen.add(cv)
                    out.append(
                        ForbiddenNumber(
                            value=cv,
                            unit=s.unit,
                            reason=f"cùng dòng, cột {'/'.join(s.column_path)}",
                        )
                    )
        return out

    def item_for(self, cell: RawCell, siblings: list[RawCell], idx: int) -> GoldItem:
        spec = TABLE_SPECS[cell.table_code]
        domain = DOMAIN_OF_TABLE[cell.table_code]
        f = self.raw.file(cell.document_id)
        question, template_id, slots = self.question_for(cell)
        rule = self._rule_for(cell)
        tags = ["tier_a"]
        target = []
        if rule is None:
            self.findings.append(
                {
                    "kind": "parser_missing",
                    "cell": cell.cell_key,
                    "line": cell.line_no,
                    "detail": cell.display,
                }
            )
            tags.append("parser_missing")
        else:
            diffs = cell_matches_rule(cell, rule)
            target = [rule["rule_code"]]
            if diffs:
                self.findings.append(
                    {
                        "kind": "parser_mismatch",
                        "rule_code": rule["rule_code"],
                        "cell": cell.cell_key,
                        "line": cell.line_no,
                        "detail": "; ".join(diffs),
                    }
                )
                tags.append("parser_mismatch")
        ref = SourceRef(
            document_id=cell.document_id,
            file_sha256=f.sha256,
            line_start=cell.line_no,
            line_end=cell.line_no,
            table_code=cell.table_code,
            row_no=cell.row_key,
            column_path=list(cell.column_path),
            cell_raw=cell.value_raw,
        )
        row_chunk = self.resolver.chunk(f"chunk:{cell.table_code}.r{cell.row_key}")
        relevant: list[RelevantChunk] = []
        if row_chunk:
            relevant.append(
                RelevantChunk(
                    chunk_id=row_chunk["chunk_id"],
                    citation_label=row_chunk["citation_label"],
                    grade=2,
                    source_ref=ref,
                )
            )
        else:
            for c in self.derived.map_to_chunks(ref, chunk_types=("table_row",)):
                relevant.append(
                    RelevantChunk(
                        chunk_id=c["chunk_id"],
                        citation_label=c["citation_label"],
                        grade=2,
                        source_ref=ref,
                    )
                )
                break
            if not relevant:
                tags.append("chunk_missing")
        factors = ["zone"]
        cond = ExpectedConditions(zone=cell.zone)
        if cell.flow_band:
            factors.append("flow_band")
            cond.flow_band = cell.flow_band  # type: ignore[assignment]
        if spec["kind"] == "flow_zone" and cell.row_key.split(".")[0] == "2":
            factors.append("footnote")
            fn = self.resolver.chunk(FOOTNOTE_B1)
            if fn:
                relevant.append(
                    RelevantChunk(
                        chunk_id=fn["chunk_id"],
                        citation_label=fn["citation_label"],
                        grade=1,
                        note="COD hoặc TOC",
                    )
                )
        if spec["kind"] == "zone" and "." in cell.row_key:
            factors.append("inheritance")
            label = cell.row_label_path[-1]
            rb = label.lower()
            cond.receiving_body = "lake" if "hồ" in rb[:40] else ("river" if "sông" in rb else None)
            if "Áp dụng đối với" in label:
                factors.append("sector_scope")
                cond.sector_scope_text = label.split("Áp dụng đối với", 1)[1].strip()[:200]
        if cell.equipment_path:
            depth = cell.equipment_path.count(".") + 1
            cond.equipment_path = cell.equipment_path
            if depth >= 3:
                factors.append("equipment_depth3")
            elif depth == 2:
                factors.append("equipment_depth2")
            if depth >= 2:
                factors.append("inheritance")
            fuel = infer_fuel(cell)
            if fuel:
                factors.append("fuel")
                cond.fuel = fuel
            cap = capacity_text(cell)
            if cap:
                factors.append("capacity")
                cond.capacity_text = cap
        if cell.o2_ref is not None:
            factors.append("o2_ref")
            cond.o2_ref = cell.o2_ref
        labels = (
            [rule["citation_label"]]
            if rule
            else ([row_chunk["citation_label"]] if row_chunk else [])
        )
        return GoldItem(
            id=f"G-TA-{idx:04d}",
            tier="A",
            question=question,
            domain=domain,
            intent="threshold",
            as_of_date=dt.date(2026, 1, 15),
            stratum="A-TABLES",
            difficulty_factors=factors,
            difficulty=compute_difficulty(factors),
            source="template_generated",
            answer_type="range" if cell.op == "between" else "value",
            expected_answer=self._expected_answer(cell),
            expected_numbers=self._numbers(cell),
            forbidden_numbers=self._forbidden(cell, siblings),
            expected_conditions=cond,
            target_rule_codes=target,
            source_ref=ref,
            relevant=relevant,
            expected_citation_labels=labels,
            expected_behaviour=ExpectedBehaviour(
                mode="answer", must_not=["pass_fail_verdict", "unit_conversion"]
            ),
            cluster_id=f"{cell.table_code}.r{cell.row_key}",
            tags=tags,
            judge_notes=None,
            verification=Verification(
                verified_by="rawtables.py",
                verified_at=dt.date.today(),
                method="template",
                source_document_id=cell.document_id,
                source_quote=cell.display,
            ),
            provenance=Provenance(origin="template", template_id=template_id),
            check_level="authored",
            notes=f"slots: {slots}",
        )


def _disp(v: float | None) -> str:
    if v is None:
        return ""
    s = canon(v)
    return s.replace(".", ",")


def generate(
    settings: Settings, tables: list[str], templates: Path, *, seed: int, sample_19: int = 180
) -> tuple[list[GoldItem], list[dict[str, Any]]]:
    g = Generator(settings, templates, seed)
    items: list[GoldItem] = []
    seen: set[str] = set()
    idx = 1
    for t in tables:
        cells = g.reader.cells(t)
        if t.startswith("QCVN19") and sample_19:
            n19 = sample_19 // 2 if len(tables) > 1 else sample_19
            chosen = sample_cells(cells, n19, random.Random(f"{seed}:{t}"))
        else:
            chosen = cells
        for cell in chosen:
            item = g.item_for(cell, cells, idx)
            for _ in range(6):
                if norm_question(item.question) not in seen:
                    break
                item = g.item_for(cell, cells, idx)
            if norm_question(item.question) in seen:
                item.question = item.question.rstrip("?.") + f" (thông số {cell.parameter})?"
            seen.add(norm_question(item.question))
            items.append(item)
            idx += 1
    return items, g.findings


def norm_question(q: str) -> str:
    """Normalised text for duplicate detection: standalone numbers masked, digits inside
    tokens (chemical formulas such as C6H6 / C10H8) kept."""
    from app.evaluation.gold_source import normalize_raw

    t = normalize_raw(q)
    t = re.sub(r"(?<![a-z0-9])\d+(?:[.,]\d+)?(?![a-z0-9])", "#", t)
    return re.sub(r"[^\w#\s]", "", t)


def crosscheck(settings: Settings, tables: list[str] | None = None) -> list[dict[str, Any]]:
    raw = RawSource(settings)
    reader = RawTableReader(raw)
    res = DerivedResolver(settings)
    findings: list[dict[str, Any]] = []
    for t in tables or list(TABLE_SPECS):
        cells = reader.cells(t)
        seen_rules = set()
        for cell in cells:
            code = f"{t}.r{cell.row_key}.{'.'.join(cell.column_path)}"
            rule = res.rule(code)
            if rule is None:
                findings.append(
                    {
                        "kind": "parser_missing",
                        "cell": cell.cell_key,
                        "line": cell.line_no,
                        "detail": cell.display,
                    }
                )
                continue
            seen_rules.add(code)
            diffs = cell_matches_rule(cell, rule)
            if diffs:
                findings.append(
                    {
                        "kind": "parser_mismatch",
                        "rule_code": code,
                        "cell": cell.cell_key,
                        "line": cell.line_no,
                        "detail": "; ".join(diffs),
                    }
                )
        for r in res.all_rules():
            if r["table_code"] == t and r["rule_code"] not in seen_rules:
                findings.append(
                    {
                        "kind": "raw_missing",
                        "rule_code": r["rule_code"],
                        "detail": r.get("citation_label"),
                    }
                )
    return findings


def sample_check_sheet(
    items: list[GoldItem], out: Path, *, n: int, seed: int, settings: Settings
) -> int:
    raw = RawSource(settings)
    rng = random.Random(seed)
    pool = list(items)
    rng.shuffle(pool)
    chosen = sorted(pool[:n], key=lambda it: it.id)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "id",
                "table",
                "row",
                "column",
                "question",
                "expected_answer",
                "source_line",
                "raw_line",
                "OK (x)",
                "correct_value_if_wrong",
                "note",
            ]
        )
        for it in chosen:
            ref = it.source_ref
            line = raw.lines(ref.document_id)[ref.line_start - 1] if ref else ""
            w.writerow(
                [
                    it.id,
                    ref.table_code if ref else "",
                    ref.row_no if ref else "",
                    "/".join(ref.column_path or []) if ref else "",
                    it.question,
                    it.expected_answer,
                    ref.line_start if ref else "",
                    line[:300],
                    "",
                    "",
                    "",
                ]
            )
    return len(chosen)
