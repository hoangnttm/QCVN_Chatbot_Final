"""Step 5: turn a Markdown pipe-table block into one JSON record per logical row (spec 4.4).

Everything is driven by the `tables` section of the document grammar; the parser only knows the
generic mechanics — grid, header rows, forward-fill of merged cells, numbered equipment hierarchy,
`<br>` splitting, value grammar, condition/exception extractors. Anything it cannot parse is kept
raw and reported in `parse_warnings` — never dropped silently (PLAN.md §14).
"""

from __future__ import annotations

import re
from typing import Any

from app.ingestion.axes import ColumnSchema
from app.ingestion.grammar import Grammar
from app.ingestion.models import (
    Condition,
    Method,
    Node,
    ParsedStructure,
    ParsedTable,
    ParsedTables,
    Span,
    TableRow,
    Value,
)
from app.ingestion.textnorm import ascii_slug, normalize_method_code, parse_vn_number, plain

_SEP_ROW = re.compile(r"^:?-+:?$")
_VALUE = re.compile(r"^(?P<op>≤|≥|<|>|=)?\s*(?P<num>[\d.,]+)\s*(?:\((?P<ref>\d+(?:,\d+)?)\))?$")
_RANGE = re.compile(r"^(?P<a>[\d.,]+)\s*[-–]\s*(?P<b>[\d.,]+)$")
_NUMBERING = re.compile(r"^(?P<num>\d+(?:\.\d+)*)\.\s*(?P<text>.*)$")
_OPS = {"≤": "<=", "≥": ">=", "<": "<", ">": ">", "=": "=", None: "="}
_CAP_OPS = {"từ": ">=", "trên": ">", "dưới": "<", "đến": "<="}
_CAP_UNITS = {"tấn/giờ": "t_per_h", "MW": "MW", "Kcal/giờ": "kcal_per_h", "kcal/giờ": "kcal_per_h"}
_FUELS = [
    ("biomass_solid", r"sinh khối"),
    ("biogas", r"khí sinh học"),
    ("liquid", r"nhiên liệu lỏng"),
    ("solid", r"nhiên liệu rắn"),
    ("gas", r"nhiên liệu khí"),
]
_CAPACITY = re.compile(
    r"(?P<kind>công suất hơi|công suất đốt|công suất|nhiệt lượng|Tổng công suất)?[^,;()]*?"
    r"(?P<op>từ|trên|dưới)\s*(?P<num>[\d.,]+)\s*(?P<unit>tấn/giờ|MW|Kcal/giờ|kcal/giờ)",
    re.I,
)


# ----------------------------------------------------------------------------- grid helpers


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_grid(lines: list[str]) -> list[list[str]]:
    rows = []
    for ln in lines:
        cells = split_row(ln)
        if cells and all(_SEP_ROW.match(c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def parse_value(
    raw: str, default_op: str | None = None, empty_markers: tuple[str, ...] = ()
) -> Value | None:
    """`default_op`: what a bare number means in this table ("<=" where every limit is a maximum);
    `empty_markers`: cells that say no limit is set ("-"), read as an empty cell."""
    txt = plain(raw)
    if not txt or txt in empty_markers:
        return None
    m = _RANGE.match(txt)
    if m:
        a, b = parse_vn_number(m.group("a")), parse_vn_number(m.group("b"))
        if a is not None and b is not None:
            return Value(op="between", min=a, max=b, raw=raw)
    m = _VALUE.match(txt)
    if m:
        num = parse_vn_number(m.group("num"))
        if num is not None:
            ref = parse_vn_number(m.group("ref")) if m.group("ref") else None
            op = _OPS[m.group("op")] if m.group("op") or not default_op else default_op
            return Value(op=op, value=num, ref_o2=ref, raw=raw)
    return Value(op="unparsed", raw=raw)


# ----------------------------------------------------------------------------- config selection


def select_config(
    node: Node, ancestors: list[Node], grammar: Grammar
) -> tuple[str, dict[str, Any]] | None:
    """Pick the grammar table config: by title for numbered tables, by enclosing Appendix otherwise."""
    appendix = next((a for a in ancestors if a.kind == "Appendix"), None)
    for code, cfg in grammar.tables.items():
        tm = cfg.get("title_match")
        if tm and node.kind == "Table" and re.search(tm, node.raw.split("\n")[0]):
            return code, cfg
        ap = cfg.get("appendix")
        if ap is not None and node.kind == "AppendixTable" and appendix is not None:
            if str(ap) == str(appendix.number):
                return code, cfg
    return None


# ----------------------------------------------------------------------------- main parser


class TableParser:
    def __init__(self, grammar: Grammar, structure: ParsedStructure):
        self.g = grammar
        self.s = structure
        self.by_id = structure.by_id()
        self.doc_id = structure.document_id
        self.file_hash = structure.file_hash

    def parse_all(self) -> ParsedTables:
        out = ParsedTables(document_id=self.doc_id, tables=[])
        for node in self.s.nodes:
            if node.kind not in ("Table", "AppendixTable") or not node.table_lines:
                continue
            ancestors: list[Node] = []
            cur = self.by_id.get(node.parent_id) if node.parent_id else None
            while cur is not None:
                ancestors.append(cur)
                cur = self.by_id.get(cur.parent_id) if cur.parent_id else None
            sel = select_config(node, ancestors, self.g)
            if sel is None:
                out.warnings.append(f"{node.node_id}: no table config matched — kept raw only")
                continue
            code, cfg = sel
            if cfg.get("code_pattern") and node.number:
                code = cfg["code_pattern"].format(number=node.number)
            # page-break continuation: the same "Bảng N." caption is repeated on the next page
            # and the grid carries no header (its first row is data, or the spill-over of the
            # previous row's cells). Report-form appendices restart their own numbering, so a
            # repeated code that is NOT adjacent becomes a distinct table.
            prev = out.tables[-1] if out.tables else None
            prev_node = self.by_id.get(prev.node_id) if prev else None
            adjacent = (
                prev_node is not None
                and prev.table_code == code
                and (node.span.pdf_page_start or 0) - (prev_node.span.pdf_page_end or 0) <= 1
                and node.span.line_start > prev_node.span.line_end
            )
            if cfg.get("continuation") and adjacent:
                lines = list(node.table_lines)
                grid0 = parse_grid(lines[:1])
                first_cell = plain(grid0[0][0]) if grid0 and grid0[0] else ""
                if not re.fullmatch(r"\d+(?:\.\d+)*|[IVX]+", first_cell) and prev.rows:
                    # spill-over of the last row's cells onto the next page
                    last = prev.rows[-1]
                    for col, cell in zip(prev.columns, grid0[0], strict=False):
                        if cell.strip():
                            last.raw_cells[col] = (last.raw_cells.get(col, "") + " " + cell).strip()
                            last.cells[col] = plain(last.raw_cells[col])
                    last.parse_warnings.append(f"cell spill-over merged from {node.node_id}")
                    lines = lines[1:]
                    # drop the separator row that followed the spill-over line
                    if lines and all(_SEP_ROW.match(c) for c in split_row(lines[0]) if c):
                        lines = lines[1:]
                cont_node = node.model_copy(update={"table_lines": lines})
                cont = self.parse_table(
                    cont_node,
                    code,
                    {
                        **cfg,
                        "columns": prev.columns,
                        "header_rows": 0,
                        "row_offset": len(prev.rows),
                        "existing_row_ids": {r.row_id for r in prev.rows},
                    },
                )
                prev.rows.extend(cont.rows)
                prev.warnings.append(f"continued on {node.node_id} (+{len(cont.rows)} rows)")
                prev.node_id = node.node_id  # so the next page can chain onto this one
                continue
            if any(t.table_code == code for t in out.tables):
                base = f"{code}@p{node.span.pdf_page_start or node.order}"
                code, k = base, 2
                while any(t.table_code == code for t in out.tables):
                    code = f"{base}#{k}"
                    k += 1
            out.tables.append(self.parse_table(node, code, cfg))
        return out

    # -- one table -------------------------------------------------------------------------

    def parse_table(self, node: Node, code: str, cfg: dict[str, Any]) -> ParsedTable:
        grid = parse_grid(node.table_lines or [])
        hr = cfg.get("header_rows", 1)
        if hr == "auto":
            # header = leading rows whose first cell is not a row number (docling keeps merged
            # headers as two rows: "Độ chính xác | Độ chính xác" then "(% giá trị đọc) | (% khoảng đo)")
            n_header = 0
            for row in grid:
                first = plain(row[0]) if row else ""
                if re.fullmatch(r"\d+(?:\.\d+)*|[IVX]+", first):
                    break
                n_header += 1
            n_header = max(1, min(n_header, 3)) if grid else 0
        else:
            n_header = int(hr)
        header_rows, data = grid[:n_header], grid[n_header:]
        if cfg.get("columns") == "auto":
            # generic tables (TT 10/2021): logical column names come from the header rows themselves
            columns: list[str] = []
            width = max((len(r) for r in header_rows), default=len(data[0]) if data else 0)
            for i in range(width):
                parts: list[str] = []
                for hrow in header_rows:
                    cell = plain(hrow[i]) if i < len(hrow) else ""
                    if cell and cell not in parts:
                        parts.append(cell)
                slug = ascii_slug(" ".join(parts)).lower() or f"c{i}"
                while slug in columns:
                    slug += "_"
                columns.append(slug)
        else:
            columns = list(cfg["columns"])
        # line number of each grid row: pipe lines are contiguous inside the node
        raw_lines = node.raw.split("\n")
        first_pipe = next(i for i, ln in enumerate(raw_lines) if ln.lstrip().startswith("|"))
        pipe_line_nos = [
            node.span.line_start + first_pipe + i
            for i, ln in enumerate(raw_lines[first_pipe:])
            if ln.lstrip().startswith("|")
        ]
        # map data rows back to line numbers (skip separator rows)
        data_line_nos: list[int] = []
        k = 0
        for ln_no, ln in zip(pipe_line_nos, (node.table_lines or []), strict=False):
            cells = split_row(ln)
            if cells and all(_SEP_ROW.match(c) for c in cells if c):
                continue
            if k >= n_header:
                data_line_nos.append(ln_no)
            k += 1

        table = ParsedTable(
            table_code=code,
            document_id=self.doc_id,
            node_id=node.node_id,
            title=node.title,
            columns=columns,
            header_rows=header_rows,
            rows=[],
            config=cfg,
        )
        footnote_ids = [
            n.node_id for n in self.s.nodes if n.parent_id == node.node_id and n.kind == "Footnote"
        ]
        table_label = node.citation_label or code
        schema = ColumnSchema.from_config(cfg)
        # columns that hold limit values (zone / tier columns, or the single value column)
        zone_cols = [c for c in columns if schema.is_value_column(c)]
        fill_cols = cfg.get("row_span_fill", [])
        # the row-number column: "tt" in curated configs, "stt"/"tt" (or a slug of them) in auto tables
        tt_col = (
            "tt"
            if "tt" in columns
            else next(
                (c for c in columns if c in ("stt", "tt") or c.startswith(("stt_", "tt_"))), "tt"
            )
        )
        eq_cfg = cfg.get("equipment_hierarchy")
        split_token = cfg.get("cell_split")
        split_rows = bool(cfg.get("split_creates_logical_rows"))
        gh_rule = cfg.get("group_header_when") or (eq_cfg or {}).get("group_header_when")
        sub_pat = re.compile(cfg["sub_row_pattern"]) if cfg.get("sub_row_pattern") else None
        extractors = cfg.get("condition_extractors", [])
        methods_cfg = cfg.get("methods_ordered")
        coeff_cfg = cfg.get("coefficient_table")

        prev: dict[str, str] = {}
        prev_row_id: str | None = None
        used_row_ids: set[str] = set(cfg.get("existing_row_ids") or ())
        block_tt: str | None = None
        block_rows: dict[str, TableRow] = {}  # numbering -> row (for parent lookup)
        parent_rows: dict[str, TableRow] = {}  # tt -> row for sub-row inheritance

        for ridx, cells in enumerate(data):
            warnings: list[str] = []
            if len(cells) != len(columns):
                warnings.append(f"cell count {len(cells)} != columns {len(columns)}")
                cells = (cells + [""] * len(columns))[: len(columns)]
            raw_cells = dict(zip(columns, cells, strict=False))
            line_no = data_line_nos[ridx] if ridx < len(data_line_nos) else node.span.line_end
            span = Span(file_hash=self.file_hash, line_start=line_no, line_end=line_no)

            # group-header rows detected by TT pattern (PL2 roman numerals)
            if isinstance(gh_rule, str) and gh_rule.startswith("tt_matches"):
                pat = gh_rule.split(" ", 1)[1].strip().strip("'\"")
                if re.match(pat, raw_cells.get("tt", "")):
                    tt = plain(raw_cells["tt"])
                    row = TableRow(
                        row_id=f"{code}.g{tt}",
                        table_code=code,
                        row_no=tt,
                        row_path=[tt],
                        is_group_header=True,
                        cells={c: plain(v) for c, v in raw_cells.items()},
                        raw_cells=raw_cells,
                        span=span,
                        citation_label=f"{table_label}, nhóm {tt}",
                        footnote_refs=footnote_ids,
                    )
                    table.rows.append(row)
                    continue

            # forward-fill merged cells (QCVN 19: TT / parameter / unit only on the first row)
            inherits_from = None
            filled = dict(raw_cells)
            new_block = bool(raw_cells.get("tt", "").strip())
            if cfg.get("new_block_when") == "tt_non_empty" and new_block:
                block_tt = plain(raw_cells["tt"]).rstrip(".")
                block_rows = {}
            for c in fill_cols:
                if not filled.get(c, "").strip() and prev.get(c):
                    filled[c] = prev[c]
                    inherits_from = prev_row_id
            for c in fill_cols:
                if filled.get(c, "").strip():
                    prev[c] = filled[c]

            tt = plain(filled.get(tt_col, "")).rstrip(".") or (
                block_tt or str(ridx + 1 + int(cfg.get("row_offset", 0)))
            )
            row_no = tt
            row_path = [tt]
            eq_num: str | None = None
            eq_text: str | None = None
            plain_cells = {c: plain(v) for c, v in filled.items()}

            # equipment hierarchy (QCVN 19)
            if eq_cfg:
                label = plain_cells.get(eq_cfg["column"], "")
                m = _NUMBERING.match(label)
                if m:
                    eq_num, eq_text = m.group("num"), m.group("text").strip()
                    parts = eq_num.split(".")
                    row_path = [tt] + [".".join(parts[: i + 1]) for i in range(len(parts))]
                else:
                    eq_text = label
                plain_cells["equipment_label"] = eq_text or ""

            # sub-rows 4.1 / 4.2 (QCVN 40 Bảng 2): inherit the parameter of the parent TT row
            sub_parent: TableRow | None = None
            if sub_pat and (sm := sub_pat.match(tt)):
                sub_parent = parent_rows.get(sm.group("parent"))
                row_path = [sm.group("parent"), tt]
                if sub_parent is not None:
                    plain_cells["condition_label"] = plain_cells.get("parameter", "")
                    plain_cells["parameter"] = sub_parent.cells.get("parameter", "")
                    inherits_from = sub_parent.row_id

            # values (a row that will be split into logical rows parses values per part instead)
            do_split = bool(
                split_rows
                and split_token
                and any(split_token in filled.get(c, "") for c in ("parameter", "unit"))
            )
            values: dict[str, Value] = {}
            if not do_split:
                for c in zone_cols:
                    v = parse_value(filled.get(c, ""), schema.default_op, schema.empty_markers)
                    if v is not None:
                        values[c] = v
                        if v.op == "unparsed":
                            warnings.append(f"unparsed value in {c}: {filled.get(c)!r}")
            is_group = gh_rule == "all_value_cells_empty" and zone_cols and not values
            # parameter header rows with sub-rows (T-P) are group headers too
            if sub_pat and not is_group and zone_cols and not values:
                is_group = True

            row_id = f"{code}.r{tt}" + (f".{eq_num}" if eq_num else "")
            # report-form tables repeat the same TT for several rows ("1 | Điểm 1 | Đợt 01/02"):
            # keep ids unique so provenance never collides
            if row_id in used_row_ids:
                k = 2
                while f"{row_id}#{k}" in used_row_ids:
                    k += 1
                row_id = f"{row_id}#{k}"
            used_row_ids.add(row_id)
            citation = f"{table_label}, TT {tt}"
            if eq_cfg:
                short = plain_cells.get("parameter", "").split("(")[0].strip()
                citation = f"{table_label}, TT {tt} ({short})" if short else citation
                if eq_num:
                    citation += f", Thiết bị {eq_num}"

            # conditions: own label + inherited from ancestor rows in the equipment block
            conditions: list[Condition] = []
            exceptions: list[str] = []
            label_for_conditions = (
                plain_cells.get("equipment_label")
                or plain_cells.get("condition_label")
                or plain_cells.get("parameter", "")
            )
            if eq_cfg and eq_num:
                anc_nums = [
                    ".".join(eq_num.split(".")[: i + 1]) for i in range(len(eq_num.split(".")) - 1)
                ]
                for a in anc_nums:
                    anc = block_rows.get(a)
                    if anc:
                        for cnd in anc.conditions:
                            if cnd.type != "equipment_path":
                                conditions.append(
                                    cnd.model_copy(update={"inherited_from": anc.row_id})
                                )
                        exceptions.extend(anc.exceptions)
                conditions.append(
                    Condition(type="equipment_path", value=row_path[1:], text=eq_text)
                )
            for ex in extractors:
                self._apply_extractor(ex, label_for_conditions, conditions, exceptions)
            if sub_parent is not None:
                # conditions extracted from the parent's parameter label (rare) are not inherited

                pass

            # methods (PL1 tables)
            methods: list[Method] = []
            if methods_cfg:
                tok = split_token or "<br>"
                stage_default = "lab"
                if cfg.get("sampling_rows") and tt.isdigit() and int(tt) in cfg["sampling_rows"]:
                    stage_default = "sampling"
                method_cols: list[tuple[str, str]] = [
                    ("methods", stage_default),
                    ("field_methods", "field"),
                    ("lab_methods", "lab"),
                ]
                if cfg.get("methods_column_match"):
                    # auto-column tables: any column whose header names a method list
                    needle = cfg["methods_column_match"].lower()
                    for ci, col in enumerate(columns):
                        head = (
                            plain(header_rows[0][ci]).lower()
                            if header_rows and ci < len(header_rows[0])
                            else col
                        )
                        if needle in head or needle in col:
                            stage = (
                                "field"
                                if "hiện trường" in head
                                else "lab"
                                if "phòng thí nghiệm" in head
                                else stage_default
                            )
                            method_cols.append((col, stage))
                for col, stage in method_cols:
                    if col in filled:
                        for i, part in enumerate([p for p in filled[col].split(tok) if plain(p)]):
                            methods.append(
                                Method(
                                    code=normalize_method_code(plain(part)),
                                    order=i + 1,
                                    stage=stage,
                                )
                            )

            # PL2 sector rows
            vsic: list[str] = []
            conditional: list[dict[str, Any]] = []
            if "vsic_codes" in filled:
                vsic = [
                    plain(p) for p in filled["vsic_codes"].split(split_token or "<br>") if plain(p)
                ]
            if "other_parameters" in filled and cfg.get("conditional_pattern"):
                cp = re.compile(cfg["conditional_pattern"])
                for part in filled["other_parameters"].split(split_token or "<br>"):
                    cm = cp.match(plain(part))
                    if cm:
                        conditional.append(
                            {
                                "condition_text": cm.group("condition").strip(),
                                "parameters_raw": cm.group("params").strip(),
                            }
                        )
                    elif plain(part):
                        conditional.append({"condition_text": None, "parameters_raw": plain(part)})

            # coefficient tables (TEF)
            coefficient = None
            if coeff_cfg:
                name_col = next((c for c in columns if c not in ("tt", "tef")), None)
                tef = parse_vn_number(plain_cells.get("tef", ""))
                coefficient = {
                    "name": plain_cells.get(name_col or "", ""),
                    "tef": tef,
                    "table": coeff_cfg.get("name"),
                }
                if tef is None:
                    warnings.append(f"unparsed TEF: {plain_cells.get('tef')!r}")

            base = TableRow(
                row_id=row_id,
                table_code=code,
                row_no=row_no,
                row_path=row_path,
                is_group_header=bool(is_group),
                inherits_from=inherits_from,
                cells=plain_cells,
                raw_cells=raw_cells,
                values=values,
                conditions=conditions,
                exceptions=exceptions,
                methods=methods,
                conditional_parameters=conditional,
                vsic_codes=vsic,
                coefficient=coefficient,
                footnote_refs=footnote_ids,
                span=span,
                citation_label=citation,
                parse_warnings=warnings,
            )

            # `<br>` inside value cells -> logical rows .a / .b (QCVN 40 Bảng 1 COD/TOC)
            if do_split:
                n_parts = max(len(filled.get(c, "").split(split_token)) for c in columns)
                for i in range(n_parts):
                    suffix = chr(ord("a") + i)
                    sub_raw = {
                        c: (v.split(split_token)[i] if len(v.split(split_token)) > i else v)
                        for c, v in filled.items()
                    }
                    sub_vals: dict[str, Value] = {}
                    sub_warn = list(warnings)
                    for c in zone_cols:
                        pv = parse_value(
                            sub_raw.get(c, ""), schema.default_op, schema.empty_markers
                        )
                        if pv is not None:
                            sub_vals[c] = pv
                            if pv.op == "unparsed":
                                sub_warn.append(f"unparsed value in {c}: {sub_raw.get(c)!r}")
                    # deep copy: logical rows must not share `entities`/`conditions` objects with
                    # the base row, otherwise resolve_entities writes TOC over COD (2.a vs 2.b)
                    sub = base.model_copy(
                        deep=True,
                        update={
                            "row_id": f"{row_id}.{suffix}",
                            "row_no": f"{tt}.{suffix}",
                            "row_path": [tt, f"{tt}.{suffix}"],
                            "cells": {c: plain(v) for c, v in sub_raw.items()},
                            "raw_cells": sub_raw,
                            "values": sub_vals,
                            "parse_warnings": sub_warn,
                            "citation_label": f"{table_label}, TT {tt}.{suffix}",
                            "inherits_from": row_id,
                        },
                    )
                    table.rows.append(sub)
                prev_row_id = row_id
                continue

            table.rows.append(base)
            prev_row_id = row_id
            if eq_num:
                block_rows[eq_num] = base
            if tt.isdigit() and sub_pat:
                parent_rows[tt] = base

        return table

    # -- extractors --------------------------------------------------------------------------

    @staticmethod
    def _apply_extractor(
        ex: dict[str, Any], label: str, conditions: list[Condition], exceptions: list[str]
    ) -> None:
        typ, pat = ex.get("type"), ex.get("pattern")
        if not label or not pat:
            return
        m = re.search(pat, label, re.I)
        if not m:
            return
        gd = {k: v for k, v in m.groupdict().items() if v}
        if typ == "exclusion":
            exceptions.append(gd.get("excl") or m.group(0))
        elif typ == "fuel_type":
            text = m.group(0)
            code = next((c for c, fp in _FUELS if re.search(fp, text, re.I)), None)
            if code:
                conditions.append(Condition(type="fuel_type", value=code, text=text))
        elif typ == "capacity":
            found = list(_CAPACITY.finditer(label))
            if found:
                first = found[0]
                unit = _CAP_UNITS.get(first.group("unit"), first.group("unit"))
                if unit == "t_per_h" and re.search(r"công suất hơi", label, re.I):
                    unit = "t_steam_per_h"
                cond = Condition(
                    type="capacity",
                    op=_CAP_OPS.get(first.group("op").lower(), "="),
                    value=parse_vn_number(first.group("num")),
                    unit=unit,
                    text=m.group(0),
                )
                if len(found) > 1:
                    alt = found[1]
                    cond.alt = {
                        "op": _CAP_OPS.get(alt.group("op").lower(), "="),
                        "value": parse_vn_number(alt.group("num")),
                        "unit": _CAP_UNITS.get(alt.group("unit"), alt.group("unit")),
                    }
                conditions.append(cond)
        elif typ == "receiving_body":
            conditions.append(
                Condition(
                    type="receiving_body", value=gd.get("body", m.group(0)).strip(), text=m.group(0)
                )
            )
        elif typ == "sector_scope":
            conditions.append(
                Condition(
                    type="sector_scope", value=gd.get("scope", m.group(0)).strip(), text=m.group(0)
                )
            )
        elif typ == "co_processing":
            conditions.append(Condition(type="co_processing", value=True, text=m.group(0)))
        else:
            conditions.append(
                Condition(type=typ or "other", value=gd or m.group(0), text=m.group(0))
            )
