"""Load data/dictionaries/*.yaml into validated models and run cross-file integrity checks."""

from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings, get_settings
from app.ontology.schema import Dictionaries

# file name -> (key in Dictionaries, top-level YAML key)
FILES: dict[str, str] = {
    "domains.yaml": "domains",
    "units.yaml": "units",
    "zones.yaml": "zones",
    "receiving_body_types.yaml": "receiving_body_types",
    "fuel_types.yaml": "fuel_types",
    "parameter_groups.yaml": "parameter_groups",
    "parameters.yaml": "parameters",
    "industry_sectors.yaml": "industry_sectors",
    "equipment_types.yaml": "equipment_types",
    "aliases.yaml": "aliases",
    "condition_dimensions.yaml": "condition_dimensions",
    "intents.yaml": "intents",
    "hops.yaml": "hops",
}

# vocabulary shared by every domain (plain string lists)
ROUTING_FILE = "routing.yaml"
ROUTING_KEYS = (
    "out_of_scope_keywords",
    "equipment_cue_words",
    "equipment_label_prefixes",
    "negation_cues",
    "split_cues",
    "zone_descriptions",
)

ID_FIELDS: dict[str, str] = {
    "domains": "code",
    "units": "unit_id",
    "zones": "code",  # unique per (domain, code) — handled separately
    "receiving_body_types": "code",
    "fuel_types": "code",
    "parameter_groups": "group_id",
    "parameters": "parameter_id",
    "industry_sectors": "sector_id",
    "equipment_types": "equipment_id",
    "condition_dimensions": "name",
    "intents": "code",
    "hops": "type",
}


def _read(path: Path, key: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    items = raw.get(key)
    if not isinstance(items, list):
        raise ValueError(f"{path.name}: expected top-level list under '{key}'")
    return items


def load_dictionaries(
    directory: Path | None = None, settings: Settings | None = None
) -> Dictionaries:
    settings = settings or get_settings()
    directory = directory or settings.dictionaries_dir
    payload = {key: _read(directory / fname, key) for fname, key in FILES.items()}
    routing = directory / ROUTING_FILE
    if routing.exists():
        payload |= {key: _read(routing, key) for key in ROUTING_KEYS}
    return Dictionaries.model_validate(payload)


def integrity_problems(d: Dictionaries) -> list[str]:
    """Referential + uniqueness checks that Pydantic alone cannot express."""
    problems: list[str] = []

    for collection, field in ID_FIELDS.items():
        if collection == "zones":
            keys = [(z.scheme_id, z.code) for z in d.zones]  # unique in its column scheme
        else:
            keys = [getattr(r, field) for r in getattr(d, collection)]
        for k, n in Counter(keys).items():
            if n > 1:
                problems.append(f"{collection}: duplicate id {k!r} ×{n}")

    unit_ids = {u.unit_id for u in d.units}
    group_ids = {g.group_id for g in d.parameter_groups}
    param_ids = {p.parameter_id for p in d.parameters}
    fuel_codes = {f.code for f in d.fuel_types}
    equip_ids = {e.equipment_id for e in d.equipment_types}
    sector_ids = {s.sector_id for s in d.industry_sectors}
    # query-side zone aliases may target a bare code ("B"); the router adds the domain later
    zone_ids = {f"{z.domain}.{z.code}" for z in d.zones} | {z.code for z in d.zones}
    rb_codes = {r.code for r in d.receiving_body_types}
    domain_codes = {x.code for x in d.domains}
    phase_domains = {x.code for x in d.domains if x.parameter_phase_required}
    for z in d.zones:
        if z.domain not in domain_codes:
            problems.append(f"zones: {z.code} belongs to unknown domain {z.domain}")

    for p in d.parameters:
        if p.default_unit_id and p.default_unit_id not in unit_ids:
            problems.append(f"parameters.{p.parameter_id}: unknown unit {p.default_unit_id}")
        if p.group_id and p.group_id not in group_ids:
            problems.append(f"parameters.{p.parameter_id}: unknown group {p.group_id}")
        for dom in p.domains:
            if dom != "both" and dom not in domain_codes:
                problems.append(f"parameters.{p.parameter_id}: unknown domain {dom}")
        if phase_domains & set(p.domains) or ("both" in p.domains and phase_domains):
            if p.phase is None:
                problems.append(f"parameters.{p.parameter_id}: phase required by its domain")

    for e in d.equipment_types:
        if e.parent_id and e.parent_id not in equip_ids:
            problems.append(f"equipment_types.{e.equipment_id}: unknown parent {e.parent_id}")
        if e.fuel_type and e.fuel_type not in fuel_codes:
            problems.append(f"equipment_types.{e.equipment_id}: unknown fuel {e.fuel_type}")

    for s in d.industry_sectors:
        for pid in s.characteristic_parameter_ids:
            if pid not in param_ids:
                problems.append(f"industry_sectors.{s.sector_id}: unknown parameter {pid}")

    targets = {
        "parameter": param_ids,
        "zone": zone_ids,
        "unit": unit_ids,
        "sector": sector_ids,
        "equipment": equip_ids,
        "domain": domain_codes,
        "receiving_body": rb_codes,
        "fuel": fuel_codes,
    }
    for a in d.aliases:
        if a.entity_id not in targets[a.entity_type]:
            problems.append(f"aliases: {a.pattern!r} -> unknown {a.entity_type} {a.entity_id}")

    # alias text must not collide across different entities of the same type
    seen: dict[tuple[str, str], str] = {}
    for a in d.aliases:
        key = (a.entity_type, a.pattern.lower())
        if key in seen and seen[key] != a.entity_id:
            problems.append(f"aliases: {a.pattern!r} maps to both {seen[key]} and {a.entity_id}")
        seen.setdefault(key, a.entity_id)
    for p in d.parameters:
        for al in p.aliases:
            key = ("parameter", al.lower())
            if key in seen and seen[key] != p.parameter_id:
                problems.append(
                    f"parameters: alias {al!r} used by {seen[key]} and {p.parameter_id}"
                )
            seen.setdefault(key, p.parameter_id)

    return problems
