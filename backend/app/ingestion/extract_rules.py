"""Step 6: derive LimitRule records from parsed table rows — one rule per value cell.

What a value column means (a zone, a flow tier, an averaging period, or nothing at all for a
single-value table) is declared per table in the grammar and read through `ColumnSchema`. The rule
keeps every qualifier the row carries (equipment path, fuel, capacity, receiving body, sector
scope, exclusions) plus the conditions of its column, so no consumer has to re-read the table
(docs/table-row-json-spec.md).
"""

from __future__ import annotations

from app.ingestion.address import cell_address
from app.ingestion.axes import ColumnSchema, tier_label
from app.ingestion.models import Condition, LimitRule, Node, ParsedTables


def extract_limit_rules(
    pt: ParsedTables, domain: str, nodes: dict[str, Node] | None = None
) -> list[LimitRule]:
    """`nodes` (structure nodes by id) gives every rule its legal address down to the cell."""
    rules: list[LimitRule] = []
    nodes = nodes or {}
    for t in pt.tables:
        schema = ColumnSchema.from_config(t.config)
        if not schema.has_values:
            continue
        for r in t.rows:
            if r.is_group_header:
                continue
            # unique inside the table: "3.8" (parameter 3, equipment 8), "2.a", "4.2"
            row_key = r.row_id.removeprefix(f"{t.table_code}.r")
            for key, v in r.values.items():
                if v.op == "unparsed":
                    continue
                meaning = schema.meaning(key)
                conds: list[Condition] = [c.model_copy() for c in r.conditions]
                label_parts: list[str] = []
                for axis, tier in meaning.tiers:
                    spec = axis.values.get(tier, {})
                    conds.append(
                        Condition(
                            type=axis.name,
                            op=spec.get("op"),
                            value=spec.get("value"),
                            unit=spec.get("unit"),
                            text=tier,
                        )
                    )
                    label_parts.append(tier_label(axis, tier))
                if meaning.zone is not None:
                    zone_text = schema.zone_text(meaning.zone)
                    conds.append(Condition(type="zone", value=meaning.zone, text=zone_text))
                    label_parts.append(zone_text)
                rules.append(
                    LimitRule(
                        rule_code=f"{r.row_id}.{key}",
                        document_id=pt.document_id,
                        domain=domain,
                        table_code=t.table_code,
                        row_id=r.row_id,
                        parameter_raw=r.cells.get("parameter", ""),
                        parameter_id=r.entities.get("parameter_id"),
                        unit_raw=r.cells.get("unit") or t.config.get("default_unit") or "",
                        unit_id=r.entities.get("unit_id"),
                        zone=meaning.zone,
                        zone_scheme=(
                            (t.config.get("zone_scheme") or pt.document_id)
                            if meaning.zone is not None
                            else None
                        ),
                        column_key=key,
                        cell_raw=r.cells.get(key),
                        address=cell_address(
                            t.node_id, nodes, row_key, key, ", ".join(label_parts) or None
                        ),
                        op=v.op,
                        value=v.value,
                        min=v.min,
                        max=v.max,
                        ref_o2=v.ref_o2,
                        conditions=conds,
                        exceptions=list(r.exceptions),
                        equipment_type_id=r.entities.get("equipment_type_id"),
                        citation_label=", ".join([r.citation_label, *label_parts]),
                        span=r.span,
                        footnote_refs=list(r.footnote_refs),
                    )
                )
    return rules
