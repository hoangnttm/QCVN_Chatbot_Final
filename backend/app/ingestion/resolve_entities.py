"""Step 9: link table rows to canonical dictionary entities (parameter, unit, equipment, sector).

Purely lexical and deterministic. Anything unresolved is returned so it becomes a review task —
nothing is guessed (PLAN.md §5 step 9).
"""

from __future__ import annotations

import re

from app.ingestion.axes import ColumnSchema
from app.ingestion.models import ParsedTables
from app.ingestion.textnorm import ascii_slug, plain
from app.ontology.schema import Dictionaries

_PAREN = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)")


def _norm(s: str) -> str:
    """Lower-case plain text with list punctuation removed ('PAH - tính theo' == 'PAH tính theo')."""
    s = re.sub(r"[-–,:;]", " ", plain(s).lower())
    return re.sub(r"\s+", " ", s).strip(" .")


def _formula_key(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s)


class EntityResolver:
    def __init__(self, d: Dictionaries):
        self.d = d
        self.param_by_text: dict[str, str] = {}
        self.param_by_formula: dict[str, str] = {}
        for p in d.parameters:
            self.param_by_text[_norm(p.name)] = p.parameter_id
            self.param_by_text[_norm(p.name.split("(")[0])] = p.parameter_id
            for a in p.aliases:
                self.param_by_text.setdefault(_norm(a), p.parameter_id)
            if p.formula:
                self.param_by_formula.setdefault(_formula_key(p.formula), p.parameter_id)
        for a in d.aliases:
            if a.entity_type == "parameter":
                self.param_by_text.setdefault(_norm(a.pattern), a.entity_id)
        self.param_by_formula_lower = {k.lower(): v for k, v in self.param_by_formula.items()}
        for p in d.parameters:  # ids like "As", "Hg", "Cr6", "PM" are usable symbols too
            self.param_by_formula_lower.setdefault(p.parameter_id.lower(), p.parameter_id)
        self.unit_by_text: dict[str, str] = {}
        self.unit_by_symbol: dict[str, str] = {}
        for u in d.units:
            self.unit_by_symbol[plain(u.symbol).lower()] = u.unit_id
            for key in [u.symbol, u.latex or "", *u.aliases]:
                if key and _norm(key):
                    self.unit_by_text[_norm(key)] = u.unit_id
        self.equipment_by_slug = {e.equipment_id: e.equipment_id for e in d.equipment_types}
        self.equipment_by_name = {_norm(e.name): e.equipment_id for e in d.equipment_types}
        self.sector_by_tt = {s.tt: s.sector_id for s in d.industry_sectors}

    # -- parameters ----------------------------------------------------------------------------

    def parameter(self, text: str) -> str | None:
        t = _norm(text)
        if not t:
            return None
        if t in self.param_by_text:
            return self.param_by_text[t]
        head = _norm(text.split("(")[0])
        if head in self.param_by_text:
            return self.param_by_text[head]
        for grp in _PAREN.findall(plain(text)):
            cand = grp.split(",")[0].strip()
            if cand.lower().startswith("tính theo"):
                cand = cand[len("tính theo") :].strip()
            key = _formula_key(cand)
            if key and key in self.param_by_formula:
                return self.param_by_formula[key]
        # longest alias that the text starts with (e.g. "Tổng hóa chất bảo vệ thực vật Clo hữu cơ, tính theo…")
        best = max(
            (k for k in self.param_by_text if t.startswith(k) and len(k) >= 6),
            key=len,
            default=None,
        )
        if best:
            return self.param_by_text[best]
        # …or a dictionary name that starts with the (shorter) text, e.g. the method table lists
        # "Tổng hóa chất bảo vệ thực vật Clo hữu cơ" while the limit table spells out the congeners
        for probe in (t, head):
            if len(probe) >= 12:
                hit = next((k for k in self.param_by_text if k.startswith(probe)), None)
                if hit:
                    return self.param_by_text[hit]
        # bare symbols in sector lists ("As, Hg, Pb, Cd, tổng Cr, Cr6+")
        sym = _formula_key(re.sub(r"^tổng\s+", "", plain(text), flags=re.I)).lower()
        if 1 <= len(sym) <= 6 and sym in self.param_by_formula_lower:
            return self.param_by_formula_lower[sym]
        return None

    def unit(self, text: str) -> str | None:
        raw = plain(text).lower()
        if (
            raw in self.unit_by_symbol
        ):  # exact symbol first — keeps the pH placeholder "-" resolvable
            return self.unit_by_symbol[raw]
        t = _norm(text)
        return self.unit_by_text.get(t) if t else None

    def equipment(self, label: str) -> str | None:
        if not label:
            return None
        slug = ascii_slug(plain(label)).lower()[:80]
        return self.equipment_by_slug.get(slug) or self.equipment_by_name.get(_norm(label))

    # -- tables --------------------------------------------------------------------------------

    def resolve_tables(self, pt: ParsedTables) -> list[str]:
        """Fill row.entities in place; return unresolved descriptions."""
        unresolved: list[str] = []
        for t in pt.tables:
            # threshold tables: with a zone axis, other condition axes or a single value column
            has_zone = ColumnSchema.from_config(t.config).has_values
            for r in t.rows:
                if r.is_group_header:
                    continue
                ents: dict[str, str] = {}
                if has_zone or t.config.get("methods_ordered"):
                    ptxt = r.cells.get("parameter") or r.cells.get("item") or ""
                    pid = self.parameter(ptxt)
                    if pid:
                        ents["parameter_id"] = pid
                    elif ptxt and not (
                        t.config.get("sampling_rows")
                        and r.row_no.isdigit()
                        and int(r.row_no) in t.config["sampling_rows"]
                    ):
                        unresolved.append(f"{r.row_id}: parameter {ptxt[:60]!r}")
                if has_zone:
                    # no unit column: the unit stated once above the table (grammar `default_unit`)
                    utxt = r.cells.get("unit") or t.config.get("default_unit") or ""
                    uid = self.unit(utxt)
                    if uid:
                        ents["unit_id"] = uid
                    elif utxt:
                        unresolved.append(f"{r.row_id}: unit {utxt!r}")
                if r.cells.get("equipment_label"):
                    eid = self.equipment(r.cells["equipment_label"])
                    if eid:
                        ents["equipment_type_id"] = eid
                    else:
                        unresolved.append(
                            f"{r.row_id}: equipment {r.cells['equipment_label'][:60]!r}"
                        )
                if "sector" in r.cells and r.row_no in self.sector_by_tt:
                    ents["sector_id"] = self.sector_by_tt[r.row_no]
                    # cột 4: "Amoni, tổng Nitơ, tổng Phốt pho." -> parameter ids (Phụ lục 2 QCVN 40)
                    ids, missing = self.parameter_list(r.cells.get("characteristic_parameters", ""))
                    ents["characteristic_parameter_ids"] = ids
                    for m in missing:
                        unresolved.append(f"{r.row_id}: characteristic parameter {m!r}")
                    cond_ids = []
                    for c in r.conditional_parameters:
                        cids, cmissing = self.parameter_list(c.get("parameters_raw", ""))
                        cond_ids.append(
                            {"condition_text": c.get("condition_text"), "parameter_ids": cids}
                        )
                        for m in cmissing:
                            unresolved.append(f"{r.row_id}: conditional parameter {m!r}")
                    ents["conditional_parameter_ids"] = cond_ids
                r.entities.update(ents)
        return unresolved

    # Whole entries that name no parameter: "Theo thực tế nguyên liệu, nhiên liệu, hóa chất sử dụng",
    # "Xác định tại Mục 4.3.2 Quy chuẩn này", "Theo loại hình sản xuất … quy định tại Cột 4 và Cột 5"
    _GENERIC = re.compile(
        r"^(theo thực tế|xác định tại|theo loại hình|các thông số|một hoặc|không|và)\b", re.I
    )

    def parameter_list(self, text: str) -> tuple[list[str], list[str]]:
        """Split a comma list of parameter names and resolve each; generic phrases are ignored."""
        ids: list[str] = []
        missing: list[str] = []
        whole = plain(text).lstrip("-• ").strip()
        if not whole or self._GENERIC.match(whole):
            return ids, missing
        # split on commas/semicolons, but not inside chemical locants ("1,4-Dioxane", "1,2-dichloroethane")
        for part in re.split(r";|,(?!\s*\d)", whole):
            p = part.strip(" .-")
            if not p or self._GENERIC.match(p):
                continue
            pid = self.parameter(p)
            if pid:
                if pid not in ids:
                    ids.append(pid)
            else:
                missing.append(p)
        return ids, missing
