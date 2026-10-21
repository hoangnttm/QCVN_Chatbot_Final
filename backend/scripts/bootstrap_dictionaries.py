"""Seed parameters / industry_sectors / equipment_types dictionaries from the Markdown sources.

Deterministic, idempotent. Re-run after editing the curated maps below; hand-edits to the generated
files should instead go into the maps so they survive a re-run. Prints anything it could not resolve
(unknown units, unmapped parameter names) — those must be fixed, never silently dropped.

Usage:  python backend/scripts/bootstrap_dictionaries.py [--write]
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import OrderedDict
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "sources"
OUT = REPO / "data" / "dictionaries"
F40 = SRC / "th_ng_t_06_2025_tt_btnmt_v_qcvn_40_2025_btnmt.md"
F19 = SRC / "qcvn_19_2024_btnmt_quy_chu_n_k_thu_t_qu_c_gia_v_kh_th_i_c_ng_nghi_p.md"

# ----------------------------------------------------------------------------- text helpers


def latex_to_text(s: str) -> str:
    """Flatten the inline LaTeX used in the converted Markdown (PLAN.md F6)."""
    s = s.replace("\\text{ngày}", "ngày")
    s = re.sub(r"\\text\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", s)
    s = s.replace("^\\circ", "°").replace("\\circ", "°")
    s = s.replace("\\le", "≤").replace("\\ge", "≥").replace("\\beta", "β")
    s = re.sub(r"[_^]\{([^}]*)\}", r"\1", s)
    s = re.sub(r"[_^](\S)", r"\1", s)
    s = s.replace("\\,", "").replace("\\ ", " ").replace("\\%", "%")
    s = s.replace("$", "").replace("{", "").replace("}", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_md(s: str) -> str:
    return re.sub(r"\*\*|\*", "", s).strip()


def ascii_slug(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s


def parse_md_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Return (rows, next_index) for the pipe-table beginning at lines[start]."""
    rows: list[list[str]] = []
    i = start
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        raw = lines[i].strip()
        cells = [c.strip() for c in raw.strip("|").split("|")]
        if not all(re.fullmatch(r":?-+:?", c) for c in cells if c):  # skip separator row
            rows.append(cells)
        i += 1
    return rows, i


def find_table(lines: list[str], title_regex: str) -> list[list[str]]:
    for i, ln in enumerate(lines):
        if re.search(title_regex, ln):
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("|"):
                j += 1
            rows, _ = parse_md_table(lines, j)
            return rows
    raise SystemExit(f"table not found: {title_regex}")


# ----------------------------------------------------------------------------- curated maps

# name (lower, latex flattened) prefix -> parameter_id.  Order matters: first match wins.
CURATED_IDS: list[tuple[str, str]] = [
    ("nhu cầu ôxy sinh hóa", "BOD5"),
    ("nhu cầu ôxy hóa học", "COD"),
    ("tổng cacbon hữu cơ", "TOC"),
    ("tổng chất rắn lơ lửng", "TSS"),
    ("nhiệt độ", "Temperature"),
    ("tổng nitơ", "T_N"),
    ("tổng phốt pho", "T_P"),
    ("tổng coliform", "Coliform"),
    ("độ màu", "Color"),
    ("amoni (", "NH4_N"),
    ("phenol", "Phenol"),
    ("tổng phenol", "Total_phenols"),
    ("clorua (", "Cl"),
    ("xylen", "Xylene"),
    ("styren", "Styrene"),
    ("toluen", "Toluene"),
    ("benzen", "Benzene"),
    ("naphthalen", "Naphthalene"),
    ("naphtalen", "Naphthalene"),
    ("methylene chloride", "Dichloromethane"),
    ("diethylhexylphthalate", "DEHP"),
    ("bis (2-ethylhexyl) adipate", "DEHA"),
    ("hợp chất hữu cơ dễ bay hơi", "TVOC"),
    ("hơi h2so4", "H2SO4_mist"),
    ("β-clopren", "Chloroprene"),
    # same substance, Vietnamese vs English spelling across the two QCVN
    ("acrylonitril", "Acrylonitrile"),
    ("diclometan", "Dichloromethane"),
    ("tricloetylen", "Trichloroethylene"),
    ("trichloroethylene", "Trichloroethylene"),
    ("1,2-dicloetan", "Dichloroethane_1_2"),
    ("1,2-dichloroethane", "Dichloroethane_1_2"),
    ("tetraclometan", "Carbon_tetrachloride"),
    ("carbon tetrachloride", "Carbon_tetrachloride"),
    ("cloroform", "Chloroform"),
    ("chloroform", "Chloroform"),
    ("vinyl clorua", "Vinyl_chloride"),
    ("vinyl chloride", "Vinyl_chloride"),
    ("formaldehyt", "Formaldehyde"),
    ("formaldehyde", "Formaldehyde"),
    ("dầu mỡ khoáng", "Mineral_oil"),
    ("dầu mỡ động thực vật", "Animal_vegetable_oil"),
    ("clo dư", "Residual_chlorine"),
    ("tổng hóa chất bảo vệ thực vật clo hữu cơ", "Organochlorine_pesticides"),
    ("tổng hóa chất bảo vệ thực vật photpho hữu cơ", "Organophosphorus_pesticides"),
    ("pcb", "PCB"),
    ("dioxin/furan", "Dioxin_Furan"),
    ("halogen hữu cơ dễ bị hấp thụ", "AOX"),
    ("chất hoạt động bề mặt anion", "Anionic_surfactants"),
    ("sunfit", "Sulfite"),
    ("bụi (pm)", "PM"),
    ("độ khói", "Smoke_opacity"),
    ("hydrocacbon thơm đa vòng", "PAH_BaP"),
    ("tổng kim loại cd, tl", "Total_Cd_Tl"),
    ("tổng các kim loại", "Total_metals_Sb_As_Pb_Cr_Co_Cu_Mn_Ni_V"),
]

GROUP_BY_ID: dict[str, str] = {
    "BOD5": "organic_load",
    "COD": "organic_load",
    "TOC": "organic_load",
    "TSS": "organic_load",
    "pH": "physical",
    "Temperature": "physical",
    "Color": "physical",
    "T_N": "nutrients",
    "T_P": "nutrients",
    "NH4_N": "nutrients",
    "Coliform": "microbiological",
    "Phenol": "phenols",
    "Total_phenols": "phenols",
    "Mineral_oil": "oils_surfactants",
    "Animal_vegetable_oil": "oils_surfactants",
    "Anionic_surfactants": "oils_surfactants",
    "Residual_chlorine": "inorganic_ions",
    "Sulfite": "inorganic_ions",
    "Organochlorine_pesticides": "pesticides_pops",
    "Organophosphorus_pesticides": "pesticides_pops",
    "PCB": "pesticides_pops",
    "Dioxin_Furan": "pesticides_pops",
    "AOX": "pesticides_pops",
    "PM": "particulate",
    "Smoke_opacity": "opacity",
    "PAH_BaP": "pah",
    "Total_Cd_Tl": "particulate",
    "Total_metals_Sb_As_Pb_Cr_Co_Cu_Mn_Ni_V": "particulate",
}
METALS = {
    "As",
    "Hg",
    "Pb",
    "Cd",
    "Cr6",
    "Cr",
    "Cu",
    "Zn",
    "Ni",
    "Mn",
    "Fe",
    "Ba",
    "Sb",
    "Sn",
    "Se",
    "Tl",
    "Co",
    "V",
}
INORGANIC_IONS = {"CN", "S2", "F", "Cl", "SO3"}
GAS_INORGANIC = {
    "NH3",
    "CO",
    "HCl",
    "SO2",
    "NOx",
    "H2S",
    "HF",
    "Cl2",
    "HCN",
    "Br2",
    "H2SO4",
    "HNO3",
    "O3",
    "AsH3",
    "PH3",
}

EXTRA_ALIASES: dict[str, list[str]] = {
    "Dioxin_Furan": ["Dioxin", "Dioxin/Furan", "dioxin furan"],
    "Coliform": ["coliform"],
    "F": ["Flo", "Florua", "flo"],
    "Cr": ["tổng Cr", "tổng Crom", "Crom"],
    "Cr6": ["Cr6+", "Crom VI", "Cr VI"],
    "Residual_chlorine": ["Clo dư"],  # bare "Clo" is the emission parameter Cl2
    "Chloroform": ["Cloroform", "Chloroform"],
    "Total_phenols": ["tổng phenol"],
    "Organochlorine_pesticides": ["tổng hóa chất bảo vệ thực vật Clo hữu cơ"],
    "Organophosphorus_pesticides": ["tổng hóa chất bảo vệ thực vật Photpho hữu cơ"],
    "Anionic_surfactants": ["chất hoạt động bề mặt anion"],
    "Animal_vegetable_oil": ["dầu mỡ động thực vật"],
    "Mineral_oil": ["dầu mỡ khoáng"],
}

UNIT_MAP: dict[str, str] = {
    "mg/L": "mg_L",
    "mg/Nm3": "mg_Nm3",
    "pgTEQ/Nm3": "pg_TEQ_Nm3",
    "pgTEQ/L": "pg_TEQ_L",
    "°C": "degC",
    "-": "ph_unit",
    "MPN hoặc CFU/100 mL": "MPN_or_CFU_100mL",
    "Pt/Co": "Pt_Co",
    "Giá trị Ringelmann": "ringelmann",
}

# ----------------------------------------------------------------------------- parameters


FORMULA_CHARS = re.compile(r"^[A-Za-z0-9+\-=\[\]().,]+$")


def formula_key(formula: str) -> str:
    """Alphanumeric skeleton of a formula — the merge key across the two QCVN (Benzen/Benzene)."""
    return re.sub(r"[^A-Za-z0-9]", "", formula)


def param_id_for(name_txt: str, formula: str | None) -> str:
    low = name_txt.lower()
    if low == "ph":
        return "pH"
    for prefix, pid in CURATED_IDS:
        if low.startswith(prefix):
            return pid
    if formula:
        key = formula_key(formula)
        # short inorganic formulas make good ids (As, NH3, SO2, NOx, Cr6); organics get a name slug
        if 1 <= len(key) <= 3 and re.search(r"[A-Z]", key):
            return key
    return ascii_slug(name_txt.split("(")[0])[:40]


def _top_level_parens(txt: str) -> list[str]:
    """Return the contents of balanced top-level (...) groups, in order."""
    groups, depth, start = [], 0, -1
    for i, ch in enumerate(txt):
        if ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                groups.append(txt[start:i].strip())
    return groups


def split_name_formula(cell: str) -> tuple[str, str | None]:
    """Name text plus the first parenthesised chemical formula, if any.

    Handles 'Chì (Pb) và hợp chất Pb (tính theo Pb)', 'Nitơ oxit (NOx, tính theo NO2)',
    'Clo (tính theo Cl2)' and nested formulas like 'Butylamin (CH3(CH2)2CH2NH2)'.
    """
    txt = latex_to_text(strip_md(cell))
    for group in _top_level_parens(txt):
        cand = group
        if cand.lower().startswith("tính theo"):
            cand = cand[len("tính theo") :].strip()
        cand = cand.split(",")[0].strip()
        if cand and re.search(r"[A-Z]", cand) and len(cand) <= 24 and FORMULA_CHARS.match(cand):
            return txt, cand
    return txt, None


def group_for(pid: str, phase: str | None) -> str:
    if pid in GROUP_BY_ID:
        return GROUP_BY_ID[pid]
    base = re.sub(r"\d+$", "", pid)
    if pid in METALS or base in METALS or pid.startswith("Cr"):
        return "heavy_metals" if phase is None else "particulate"
    if pid in INORGANIC_IONS or base in INORGANIC_IONS:
        return "inorganic_ions"
    if pid in GAS_INORGANIC:
        return "gas_inorganic"
    if phase == "gas":
        return "gas_organic"
    if phase == "particulate":
        return "particulate"
    return (
        "volatile_organics"
        if re.search(
            r"chlor|ethyl|ethane|benz|tolu|xyl|styren|dioxane|vinyl|acryl|bromo", pid, re.I
        )
        else "other_organics"
    )


def collect_parameters() -> tuple[OrderedDict[str, dict], list[str]]:
    params: OrderedDict[str, dict] = OrderedDict()
    unknown_units: list[str] = []

    by_formula: dict[str, str] = {}  # formula key -> parameter_id already created

    def add(name_cell: str, unit_cell: str, domain: str, phase: str | None, source: str) -> None:
        name_txt, formula = split_name_formula(name_cell)
        pid = param_id_for(name_txt, formula)
        # same substance named differently in the two QCVN (Benzen / Benzene) -> merge by formula
        if formula and pid not in params:
            pid = by_formula.get(formula_key(formula), pid)
        unit_txt = latex_to_text(strip_md(unit_cell)) if unit_cell else ""
        unit_id = UNIT_MAP.get(unit_txt)
        if unit_txt and unit_id is None:
            unknown_units.append(f"{unit_txt!r} @ {source}")
        rec = params.get(pid)
        if rec is None:
            if formula:
                by_formula.setdefault(formula_key(formula), pid)
            rec = params[pid] = {
                "parameter_id": pid,
                "name": name_txt,
                "symbol": formula,
                "formula": formula,
                "domains": [domain],
                "group_id": group_for(pid, phase),
                "phase": phase,
                "default_unit_id": unit_id,
                "aliases": [],
                "sources": [source],
            }
        else:
            if domain not in rec["domains"]:
                rec["domains"] = ["both"]
            if phase and rec["phase"] and rec["phase"] != phase:
                rec["phase"] = "gas_and_particulate"
            elif phase and not rec["phase"]:
                rec["phase"] = phase
            if source not in rec["sources"]:
                rec["sources"].append(source)
        alias = name_txt.split("(")[0].strip()
        for a in {alias, alias.lower(), (formula or "").lower()}:
            if a and a not in rec["aliases"] and a.lower() != pid.lower():
                rec["aliases"].append(a)

    l40 = F40.read_text(encoding="utf-8").splitlines()
    for r in find_table(l40, r"^\*\*Bảng 1\. Giá trị giới hạn cho phép của COD")[3:]:
        names, units = r[1].split("<br>"), r[2].split("<br>")
        for n, u in zip(names, units, strict=False):
            add(n, u, "wastewater", None, f"QCVN 40:2025/BTNMT, Mục 2.1, Bảng 1, TT {r[0]}")
    for r in find_table(
        l40, r"^\*\*Bảng 2\. Giá trị giới hạn cho phép của các thông số ô nhiễm đặc trưng"
    )[1:]:
        if not re.fullmatch(r"\d+", r[0]):
            continue  # 4.1–4.3 are condition sub-rows of T-P, not parameters
        add(r[1], r[2], "wastewater", None, f"QCVN 40:2025/BTNMT, Mục 2.2, Bảng 2, TT {r[0]}")

    l19 = F19.read_text(encoding="utf-8").splitlines()
    for tno, phase, sec in [(1, "gas", "2.1"), (2, "particulate", "2.2")]:
        for r in find_table(
            l19, rf"^\*\*Bảng {tno}\. Giá trị giới hạn cho phép của thông số ô nhiễm"
        )[1:]:
            tt = strip_md(r[0]).rstrip(".")
            if tt and r[1]:
                add(
                    r[1],
                    r[2],
                    "emission",
                    phase,
                    f"QCVN 19:2024/BTNMT, Mục {sec}, Bảng {tno}, TT {tt}",
                )
    # component metals that only appear inside "Tổng các kim loại" / "Tổng kim loại Cd, Tl" but have
    # their own method rows in Phụ lục 1 (QCVN 19), plus the HF spelling used there for Flo
    add(
        "Tali (Tl) và hợp chất Tl (tính theo Tl)",
        "$\\text{mg/Nm}^3$",
        "emission",
        "particulate",
        "QCVN 19:2024/BTNMT, Phụ lục 1, TT 71",
    )
    add(
        "Vanadi (V) và hợp chất V (tính theo V)",
        "$\\text{mg/Nm}^3$",
        "emission",
        "particulate",
        "QCVN 19:2024/BTNMT, Phụ lục 1, TT 72",
    )
    add(
        "Coban (Co) và hợp chất Co (tính theo Co)",
        "$\\text{mg/Nm}^3$",
        "emission",
        "particulate",
        "QCVN 19:2024/BTNMT, Bảng 2, TT 10 (thành phần tổng kim loại)",
    )
    if "F" in params:
        for a in ("Axit flohydric (HF)", "axit flohydric", "HF"):
            if a not in params["F"]["aliases"]:
                params["F"]["aliases"].append(a)
        params["F"]["sources"].append("QCVN 19:2024/BTNMT, Phụ lục 1, TT 15")
    # short forms used in QCVN 40 Phụ lục 2 (cột 4/5) and everyday queries
    for pid, extra in EXTRA_ALIASES.items():
        if pid in params:
            for a in extra:
                if a not in params[pid]["aliases"]:
                    params[pid]["aliases"].append(a)
    for rec in params.values():
        if "both" in rec["domains"] and rec["phase"] is None:
            rec["phase"] = "gas"
        if rec["phase"] is None and ("emission" in rec["domains"]):
            rec["phase"] = "gas"
        if (
            rec["symbol"]
            and rec["symbol"] == rec["formula"]
            and not re.fullmatch(r"[A-Za-z0-9+\-=,. ]+", rec["symbol"])
        ):
            rec["symbol"] = None
    return params, unknown_units


# ----------------------------------------------------------------------------- industry sectors


def collect_sectors() -> list[dict]:
    l40 = F40.read_text(encoding="utf-8").splitlines()
    # match the appendix heading (not the header row) so rows[0] is the header and rows[1:] the data
    rows = find_table(l40, r"^### PHỤ LỤC 2\s*$")
    out: list[dict] = []
    group_roman, group_letter = None, None
    for r in rows[1:]:
        tt = strip_md(r[0])
        if re.fullmatch(r"[IVX]+", tt):
            group_roman, group_letter = tt, strip_md(r[2]) or None
            continue
        if not re.fullmatch(r"\d+", tt):
            continue
        name = latex_to_text(strip_md(r[1]))
        vsic = [latex_to_text(strip_md(v)) for v in r[2].split("<br>") if strip_md(v)]
        out.append(
            {
                "sector_id": f"S{int(tt):02d}",
                "tt": tt,
                "name": name,
                "vsic_codes": vsic,
                "group_roman": group_roman,
                "group_letter": group_letter,
                "characteristic_parameters_raw": latex_to_text(strip_md(r[3])),
                "other_parameters_raw": latex_to_text(strip_md(r[4]).replace("<br>", " ")),
                "characteristic_parameter_ids": [],
                "aliases": [name.lower()],
                "sources": [f"QCVN 40:2025/BTNMT, Phụ lục 2, TT {tt}"],
            }
        )
    return out


# ----------------------------------------------------------------------------- equipment types

FUEL_PATTERNS = [
    ("biomass_solid", r"sinh khối"),
    ("biogas", r"khí sinh học"),
    ("liquid", r"nhiên liệu lỏng"),
    ("solid", r"nhiên liệu rắn"),
    ("gas", r"nhiên liệu khí"),
]


def collect_equipment() -> tuple[list[dict], int]:
    l19 = F19.read_text(encoding="utf-8").splitlines()
    equip: OrderedDict[str, dict] = OrderedDict()
    n_rows = 0
    for tno, sec in [(1, "2.1"), (2, "2.2")]:
        rows = find_table(
            l19, rf"^\*\*Bảng {tno}\. Giá trị giới hạn cho phép của thông số ô nhiễm"
        )[1:]
        block_tt: str | None = None
        block_param = ""
        path_stack: dict[str, str] = {}
        for r in rows:
            tt = strip_md(r[0]).rstrip(".")
            if tt:
                block_tt, block_param, path_stack = tt, latex_to_text(strip_md(r[1])), {}
            cell = r[3]
            if not cell:
                continue
            n_rows += 1
            is_group = all(not c.strip() for c in r[4:7])
            label_md = cell.strip()
            label = latex_to_text(strip_md(label_md))
            m = re.match(r"^(\d+(?:\.\d+)*)\.\s*(.*)$", label)
            num, text = (m.group(1), m.group(2)) if m else (None, label)
            path = []
            if num:
                parts = num.split(".")
                path = [".".join(parts[: i + 1]) for i in range(len(parts))]
                path_stack[num] = text
            parent_label = (
                path_stack.get(".".join(num.split(".")[:-1])) if num and "." in num else None
            )
            fuel = next((code for code, pat in FUEL_PATTERNS if re.search(pat, text, re.I)), None)
            if fuel is None and parent_label:
                fuel = next(
                    (code for code, pat in FUEL_PATTERNS if re.search(pat, parent_label, re.I)),
                    None,
                )
            cap = text if re.search(r"công suất|nhiệt lượng|MW", text) else None
            eid = ascii_slug(text).lower()[:80] or f"row_{n_rows}"
            short = block_param.split("(")[0].strip()
            occ = (
                f"QCVN 19:2024/BTNMT, Mục {sec}, Bảng {tno}, TT {block_tt} ({short}), "
                f"Thiết bị {num or '-'}"
            )
            rec = equip.get(eid)
            if rec is None:
                rec = equip[eid] = {
                    "equipment_id": eid,
                    "name": text,
                    "label_raw": label_md,
                    "path": path,
                    "parent_id": ascii_slug(parent_label).lower()[:80] if parent_label else None,
                    "fuel_type": fuel,
                    "capacity_condition_raw": cap,
                    "is_group_header": is_group,
                    "aliases": [],
                    "sources": [],
                    "occurrences": [],
                }
            rec["occurrences"].append(occ)
            if not rec["sources"]:
                rec["sources"].append(occ)
    for rec in equip.values():
        if rec["parent_id"] and rec["parent_id"] not in equip:
            rec["parent_id"] = None
    return list(equip.values()), n_rows


# ----------------------------------------------------------------------------- main


def dump(path: Path, key: str, items: list[dict], header: str) -> None:
    text = header + yaml.safe_dump({key: items}, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write YAML files (default: dry run)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    params, unknown_units = collect_parameters()
    sectors = collect_sectors()
    equipment, n_rows = collect_equipment()

    n_ww = sum(1 for p in params.values() if p["domains"] == ["wastewater"])
    n_em = sum(1 for p in params.values() if p["domains"] == ["emission"])
    n_both = sum(1 for p in params.values() if p["domains"] == ["both"])
    print(
        f"parameters: {len(params)}  (wastewater-only {n_ww}, emission-only {n_em}, both {n_both})"
    )
    print(f"industry_sectors: {len(sectors)}")
    print(f"equipment_types: {len(equipment)} unique labels from {n_rows} equipment rows")
    if unknown_units:
        print("UNKNOWN UNITS (add to UNIT_MAP + units.yaml):")
        for u in sorted(set(unknown_units)):
            print("  ", u)
    dup_names = [
        p for p in params.values() if len(p["sources"]) > 1 and p["domains"] == ["wastewater"]
    ]
    if dup_names:
        print(
            "parameters merged from several wastewater rows (check):",
            [p["parameter_id"] for p in dup_names],
        )

    if args.write:
        hdr = (
            "# GENERATED by backend/scripts/bootstrap_dictionaries.py — "
            "edit the curated maps there, not this file.\n"
        )
        dump(OUT / "parameters.yaml", "parameters", list(params.values()), hdr)
        dump(OUT / "industry_sectors.yaml", "industry_sectors", sectors, hdr)
        dump(OUT / "equipment_types.yaml", "equipment_types", equipment, hdr)
        print("written:", OUT)
    return 1 if unknown_units else 0


if __name__ == "__main__":
    raise SystemExit(main())
