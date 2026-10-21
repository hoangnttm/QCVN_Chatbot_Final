"""Dictionary stubs for a new document (plan 2026-10-06 G1, step 0.9).

`bootstrap_dictionaries.py` seeds the dictionaries from the two regulations it was written for.
For any further document the same job is done from its parsed tables: every parameter or unit a
threshold table uses and the dictionaries do not know becomes a stub for a person to complete,
and every known parameter that the document's domain does not list yet is reported. Deterministic,
nothing is written to the dictionaries: the output is a proposal (never a guess that goes live).
"""

from __future__ import annotations

import re
from typing import Any

from app.ingestion.axes import ColumnSchema
from app.ingestion.models import ParsedTables
from app.ingestion.resolve_entities import EntityResolver
from app.ingestion.textnorm import ascii_slug, plain
from app.ontology.schema import Dictionaries

_SYMBOL = re.compile(r"\(([^()]+)\)\s*$")


def _split_name(text: str) -> tuple[str, str | None]:
    """("Chì", "Pb") from "Chì (Pb)"; the symbol only when it reads like one (short, ASCII)."""
    txt = plain(text).strip()
    m = _SYMBOL.search(txt)
    if m:
        sym = re.sub(r"\s+", "", m.group(1))
        if sym.isascii() and 1 <= len(sym) <= 12:
            return txt, sym
    return txt, None


def parameter_id_for(name: str, symbol: str | None, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", symbol) if symbol else ascii_slug(name.split("(")[0])
    base = base or "PARAM"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def propose(pt: ParsedTables, d: Dictionaries, domain: str) -> dict[str, list[dict[str, Any]]]:
    """Stubs and domain extensions for the threshold tables of one parsed document."""
    resolver = EntityResolver(d)
    by_id = {p.parameter_id: p for p in d.parameters}
    taken = set(by_id)
    new_parameters: dict[str, dict[str, Any]] = {}
    extend: dict[str, dict[str, Any]] = {}
    new_units: dict[str, dict[str, Any]] = {}
    for t in pt.tables:
        if not ColumnSchema.from_config(t.config).has_values:
            continue
        for r in t.data_rows():
            ptxt = r.cells.get("parameter") or r.cells.get("item") or ""
            utxt = r.cells.get("unit") or t.config.get("default_unit") or ""
            unit_id = resolver.unit(utxt) if utxt else None
            if utxt and unit_id is None:
                stub = new_units.setdefault(
                    plain(utxt),
                    {"unit_id": ascii_slug(plain(utxt)), "symbol": plain(utxt), "sources": []},
                )
                stub["sources"].append(r.citation_label)
            if not ptxt:
                continue
            pid = resolver.parameter(ptxt)
            if pid is None:
                name, symbol = _split_name(ptxt)
                key = plain(ptxt).lower()
                if key not in new_parameters:
                    new_id = parameter_id_for(name, symbol, taken)
                    taken.add(new_id)
                    new_parameters[key] = {
                        "parameter_id": new_id,
                        "name": name,
                        "symbol": symbol,
                        "domains": [domain],
                        "default_unit_id": unit_id,
                        "aliases": [],
                        "sources": [],
                    }
                new_parameters[key]["sources"].append(r.citation_label)
            elif domain not in by_id[pid].domains and "both" not in by_id[pid].domains:
                ext = extend.setdefault(
                    pid, {"parameter_id": pid, "add_domain": domain, "seen_as": [], "sources": []}
                )
                if plain(ptxt) not in ext["seen_as"]:
                    ext["seen_as"].append(plain(ptxt))
                ext["sources"].append(r.citation_label)
    return {
        "new_parameters": list(new_parameters.values()),
        "extend_parameter_domains": list(extend.values()),
        "new_units": list(new_units.values()),
    }
