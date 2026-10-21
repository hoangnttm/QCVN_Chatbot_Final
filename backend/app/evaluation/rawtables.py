"""Independent reader of the QCVN threshold tables straight from the Markdown in `sources/`.

Deliberately NOT built on `app/ingestion`: tier A of the gold set (plan §2.7) needs a second,
simpler reading of the same cells so that gold values do not inherit parser errors. Handles:
pipe tables with multi-row headers, LaTeX cells (`$\\le 40$`, `$6 - 9$`, `$\\le 15(12)$` with the
O₂ reference in parentheses, `$\\le 0,001$` decimal comma), `<br>` alternatives (COD or TOC),
bold/italic group headers and the numbered equipment hierarchy of QCVN 19, row-span fills."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.gold_source import RawSource, normalize_raw

_SEP_RE = re.compile(r"^:?-{2,}:?$")
_NUM_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s*(.*)$", re.S)
_VALUE_RE = re.compile(
    r"^(?P<op>≤|≥|<=|>=|<|>)?\s*(?P<a>\d+(?:[.,]\d+)?)\s*(?:(?:-|–|—)\s*(?P<b>\d+(?:[.,]\d+)?))?\s*(?:\((?P<o2>\d+(?:[.,]\d+)?)\))?\s*$"
)


@dataclass
class RawCell:
    table_code: str
    document_id: str
    line_no: int
    row_key: str  # parser-compatible row key: e.g. "1", "2.a", "4.2", "2.1.2.1"
    row_no: str  # TT of the parameter row ("2") — for QCVN 19 the parameter number
    equipment_path: str | None  # QCVN 19: "1.2.1"
    row_label_path: list[str]  # human labels of the equipment ancestors + leaf (QCVN 19)
    parameter: str
    parameter_short: str | None
    unit: str
    column_path: list[str]  # ["F<=2000","B"] or ["B"]
    zone: str
    flow_band: str | None
    value_raw: str
    op: str | None
    value: float | None
    vmin: float | None
    vmax: float | None
    o2_ref: float | None
    display: str
    line_text: str
    notes: list[str] = field(default_factory=list)

    @property
    def cell_key(self) -> str:
        return f"{self.table_code}.r{self.row_key}.{'.'.join(self.column_path)}"


@dataclass
class RawRow:
    table_code: str
    line_no: int
    row_key: str
    row_no: str
    equipment_path: str | None
    row_label_path: list[str]
    parameter: str
    parameter_short: str | None
    unit: str
    cells: dict[str, str]
    is_group: bool
    line_text: str
    alt_index: int | None = None


TABLE_SPECS: dict[str, dict[str, Any]] = {
    "QCVN40_2025.B1": {
        "document_id": "QCVN_40_2025",
        "title_re": r"^\*\*Bảng 1\.",
        "header_rows": 3,
        "columns": [
            "tt",
            "parameter",
            "unit",
            "F<=2000.A",
            "F<=2000.B",
            "F<=2000.C",
            "F>2000.A",
            "F>2000.B",
            "F>2000.C",
        ],
        "kind": "flow_zone",
    },
    "QCVN40_2025.B2": {
        "document_id": "QCVN_40_2025",
        "title_re": r"^\*\*Bảng 2\.",
        "header_rows": 1,
        "columns": ["tt", "parameter", "unit", "A", "B", "C"],
        "kind": "zone",
    },
    "QCVN19_2024.B1": {
        "document_id": "QCVN_19_2024",
        "title_re": r"^\*\*Bảng 1\.",
        "header_rows": 1,
        "columns": ["tt", "parameter", "unit", "equipment", "A", "B", "C"],
        "kind": "equipment_zone",
    },
    "QCVN19_2024.B2": {
        "document_id": "QCVN_19_2024",
        "title_re": r"^\*\*Bảng 2\.",
        "header_rows": 1,
        "columns": ["tt", "parameter", "unit", "equipment", "A", "B", "C"],
        "kind": "equipment_zone",
    },
}


# ---------------------------------------------------------------- cell helpers


def strip_markup(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\\text\{([^}]*)\}", r"\1", t)
    t = t.replace("$", "")
    t = t.replace(r"\le", "≤").replace(r"\leq", "≤").replace(r"\ge", "≥").replace(r"\geq", "≥")
    t = t.replace(r"^\circ", "°").replace(r"\circ", "°").replace(r"\%", "%")
    t = re.sub(r"\^\{?(\d)\}?", lambda m: {"2": "²", "3": "³"}.get(m.group(1), m.group(1)), t)
    t = re.sub(r"_\{([^}]*)\}", r"\1", t)
    t = re.sub(r"_(\w)", r"\1", t)
    t = re.sub(r"\^\{([^}]*)\}", r"\1", t)
    t = t.replace("{", "").replace("}", "")
    t = t.strip("*").strip()
    return re.sub(r"\s+", " ", t)


def _num(s: str) -> float:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        return float(s.replace(".", ""))
    return float(s.replace(",", "."))


def parse_value(raw: str) -> dict[str, Any]:
    """`$\\le 350(6)$` → {op '<=', value 350, o2 6}; `$6 - 9$` → between; '' → empty."""
    t = strip_markup(raw)
    if not t or t in ("-", "–", "—"):
        return {"op": None, "value": None, "min": None, "max": None, "o2": None, "display": t}
    m = _VALUE_RE.match(t)
    if not m:
        return {
            "op": None,
            "value": None,
            "min": None,
            "max": None,
            "o2": None,
            "display": t,
            "unparsed": True,
        }
    op = {"≤": "<=", "≥": ">=", "<=": "<=", ">=": ">=", "<": "<", ">": ">"}.get(
        m.group("op") or "", None
    )
    a = _num(m.group("a"))
    b = _num(m.group("b")) if m.group("b") else None
    o2 = _num(m.group("o2")) if m.group("o2") else None
    if b is not None:
        return {"op": "between", "value": None, "min": a, "max": b, "o2": o2, "display": t}
    return {"op": op or "=", "value": a, "min": None, "max": None, "o2": o2, "display": t}


def canon(v: float | None) -> str:
    if v is None:
        return ""
    s = format(float(v), "f").rstrip("0").rstrip(".")
    return s or "0"


def _short_name(parameter: str) -> str | None:
    m = re.search(r"\(([^()]{1,25})\)\s*$", parameter)
    if m:
        inner = m.group(1)
        if not re.search(r"\d{3,}", inner) and len(inner) <= 25:
            return inner.split(" ở ")[0].strip()
    return None


def _split_cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


# ---------------------------------------------------------------- table reader


class RawTableReader:
    def __init__(self, raw: RawSource) -> None:
        self.raw = raw

    def _locate(self, spec: dict[str, Any]) -> tuple[int, list[tuple[int, str]]]:
        doc = spec["document_id"]
        lines = self.raw.lines(doc)
        f = self.raw.file(doc)
        lo, hi = f.line_range or (1, len(lines))
        title_re = re.compile(spec["title_re"])
        start = None
        for i in range(lo, min(hi, len(lines)) + 1):
            if title_re.match(lines[i - 1].strip()):
                start = i
                break
        if start is None:
            raise ValueError(f"table title not found for {spec}")
        j = start + 1
        while j <= len(lines) and not lines[j - 1].lstrip().startswith("|"):
            j += 1
        rows: list[tuple[int, str]] = []
        while j <= len(lines) and lines[j - 1].lstrip().startswith("|"):
            rows.append((j, lines[j - 1]))
            j += 1
        return start, rows

    def rows(self, table_code: str) -> list[RawRow]:
        spec = TABLE_SPECS[table_code]
        _, lines = self._locate(spec)
        cols = spec["columns"]
        body: list[tuple[int, list[str]]] = []
        n_header = 0
        for ln, text in lines:
            cells = _split_cells(text)
            if all(_SEP_RE.match(c) or not c for c in cells):
                continue  # separator
            if n_header < spec["header_rows"]:
                n_header += 1
                continue
            cells = (cells + [""] * len(cols))[: len(cols)]
            body.append((ln, cells))
        kind = spec["kind"]
        out: list[RawRow] = []
        text_by_line = dict(lines)
        value_cols = [k for k in cols if k not in ("tt", "parameter", "unit", "equipment")]
        cur_param = ""
        cur_short: str | None = None
        cur_unit = ""
        cur_tt = ""
        ancestors: list[tuple[int, str, str]] = []  # (depth, number, label)
        for ln, cells in body:
            c = dict(zip(cols, cells, strict=False))
            tt = strip_markup(c.get("tt", "")).rstrip(".")
            param = strip_markup(c.get("parameter", ""))
            unit = strip_markup(c.get("unit", ""))
            values = {k: c.get(k, "") for k in value_cols}
            has_values = any(strip_markup(v) for v in values.values())
            line_text = text_by_line[ln]
            if kind in ("flow_zone", "zone"):
                self._plain_row(
                    table_code,
                    out,
                    ln,
                    tt,
                    param,
                    unit,
                    values,
                    has_values,
                    line_text,
                    c,
                    kind,
                    cur_unit,
                )
                if tt and "." not in tt:
                    cur_unit = unit or cur_unit
                continue
            # equipment_zone (QCVN 19)
            if tt:
                cur_tt, cur_param, cur_unit = tt, param, unit
                cur_short = _short_name(param)
                ancestors = []
            if not cur_tt:
                continue
            equip = strip_markup(c.get("equipment", ""))
            m = _NUM_PREFIX.match(equip)
            if not m:
                if not has_values:
                    continue
                # un-numbered single equipment cell → the row key is the parameter number
                out.append(
                    RawRow(
                        table_code,
                        ln,
                        cur_tt,
                        cur_tt,
                        None,
                        [cur_param] + ([equip] if equip else []),
                        cur_param,
                        cur_short,
                        cur_unit,
                        values,
                        False,
                        line_text,
                    )
                )
                continue
            number, label = m.group(1), m.group(2).strip()
            depth = number.count(".") + 1
            ancestors = [a for a in ancestors if a[0] < depth]
            ancestors.append((depth, number, label))
            path_labels = [a[2] for a in ancestors]
            out.append(
                RawRow(
                    table_code,
                    ln,
                    f"{cur_tt}.{number}",
                    cur_tt,
                    number,
                    [cur_param] + path_labels,
                    cur_param,
                    cur_short,
                    cur_unit,
                    values,
                    not has_values,
                    line_text,
                )
            )
        return out

    @staticmethod
    def _plain_row(
        table_code, out, ln, tt, param, unit, values, has_values, line_text, c, kind, cur_unit
    ):
        if not tt:
            return
        if "." in tt and kind == "zone":
            parent_no = tt.split(".")[0]
            parent = next((r for r in out if r.row_no == parent_no and r.is_group), None)
            pname = parent.parameter if parent else param
            out.append(
                RawRow(
                    table_code,
                    ln,
                    tt,
                    tt,
                    None,
                    [pname, param],
                    pname,
                    parent.parameter_short if parent else None,
                    unit or (parent.unit if parent else cur_unit),
                    values,
                    False,
                    line_text,
                )
            )
            return
        if "<br>" in c.get("parameter", "") and kind == "flow_zone":
            parts = [strip_markup(x) for x in c["parameter"].split("<br>")]
            units = [strip_markup(x) for x in c.get("unit", "").split("<br>")]
            for idx, pname in enumerate(parts):
                sub_vals = {}
                for k, v in values.items():
                    alts = v.split("<br>")
                    sub_vals[k] = alts[idx] if idx < len(alts) else ""
                key = f"{tt}.{'abcdef'[idx]}"
                u = units[idx] if idx < len(units) else (units[0] if units else unit)
                pname_clean = re.sub(r"^(hoặc|or)\s+", "", pname, flags=re.I)
                out.append(
                    RawRow(
                        table_code,
                        ln,
                        key,
                        tt,
                        None,
                        [pname_clean],
                        pname_clean,
                        _short_name(pname_clean),
                        u,
                        sub_vals,
                        False,
                        line_text,
                        alt_index=idx,
                    )
                )
            return
        out.append(
            RawRow(
                table_code,
                ln,
                tt,
                tt,
                None,
                [param],
                param,
                _short_name(param),
                unit,
                values,
                not has_values,
                line_text,
            )
        )

    def cells(self, table_code: str) -> list[RawCell]:
        spec = TABLE_SPECS[table_code]
        out: list[RawCell] = []
        for row in self.rows(table_code):
            if row.is_group:
                continue
            for col, raw_val in row.cells.items():
                pv = parse_value(raw_val)
                if pv["value"] is None and pv["min"] is None:
                    continue
                parts = col.split(".")
                zone = parts[-1]
                flow = parts[0] if len(parts) == 2 else None
                notes = []
                if pv.get("unparsed"):
                    notes.append("unparsed")
                out.append(
                    RawCell(
                        table_code=table_code,
                        document_id=spec["document_id"],
                        line_no=row.line_no,
                        row_key=row.row_key,
                        row_no=row.row_no,
                        equipment_path=row.equipment_path,
                        row_label_path=row.row_label_path,
                        parameter=row.parameter,
                        parameter_short=row.parameter_short,
                        unit=row.unit,
                        column_path=parts,
                        zone=zone,
                        flow_band=flow,
                        value_raw=raw_val,
                        op=pv["op"],
                        value=pv["value"],
                        vmin=pv["min"],
                        vmax=pv["max"],
                        o2_ref=pv["o2"],
                        display=pv["display"],
                        line_text=row.line_text,
                        notes=notes,
                    )
                )
        return out


def cell_matches_rule(cell: RawCell, rule: dict[str, Any]) -> list[str]:
    """Differences between a raw cell and the parser's rule (empty = agree)."""
    diffs = []
    if rule.get("zone") != cell.zone:
        diffs.append(f"zone {rule.get('zone')}≠{cell.zone}")
    if cell.op == "between":
        if canon(rule.get("min")) != canon(cell.vmin) or canon(rule.get("max")) != canon(cell.vmax):
            diffs.append(f"range {rule.get('min')}-{rule.get('max')}≠{cell.vmin}-{cell.vmax}")
    else:
        if canon(rule.get("value")) != canon(cell.value):
            diffs.append(f"value {rule.get('value')}≠{cell.value}")
        if (rule.get("op") or "=") != (cell.op or "="):
            diffs.append(f"op {rule.get('op')}≠{cell.op}")
    if canon(rule.get("ref_o2")) != canon(cell.o2_ref):
        diffs.append(f"o2 {rule.get('ref_o2')}≠{cell.o2_ref}")
    if normalize_raw(rule.get("unit_raw") or "") != normalize_raw(cell.unit):
        diffs.append(f"unit {rule.get('unit_raw')!r}≠{cell.unit!r}")
    return diffs
