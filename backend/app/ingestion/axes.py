"""What the value columns of a threshold table mean (plan 2026-10-06 G1, step 0.5).

A value column key is a "."-joined path of header tiers. Each tier is either a zone of the table's
`zone_axis` or a value of one of its condition axes; a table may also have plain value columns
(`value_columns`) that carry no condition at all. Declared in the grammar, per table:

    zone_axis: [A, B, C]                        # optional
    zone_label: "Mức {zone}"                    # optional, default "Cột {zone}"
    header_condition_axis: { name: flow_regime, values: { "F<=2000": {op: "<=", value: 2000, …} } }
    condition_axes:                             # optional, any number of further axes
      - { name: averaging_period, values: { "1h": {value: 1, unit: hour, label: "Trung bình 1 giờ"} } }
    value_columns: [value]                      # optional, single-value tables
    default_op: "<="                            # optional: a bare number is a maximum
    default_unit: "µg/Nm3"                      # optional: unit stated once above the table
    empty_markers: ["-"]                        # optional: cells that mean "no limit set"

Examples: `F<=2000.A` (flow tier × zone), `A` (zone only), `1h` (averaging period only), `value`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_ZONE_LABEL = "Cột {zone}"


@dataclass(frozen=True)
class Axis:
    name: str
    values: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ColumnMeaning:
    zone: str | None = None
    tiers: tuple[tuple[Axis, str], ...] = ()  # (axis, key of the value on that axis)


@dataclass(frozen=True)
class ColumnSchema:
    zone_axis: tuple[str, ...] = ()
    zone_label: str = DEFAULT_ZONE_LABEL
    axes: tuple[Axis, ...] = ()
    value_columns: tuple[str, ...] = ()
    default_op: str | None = None
    empty_markers: tuple[str, ...] = field(default=())

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> ColumnSchema:
        axes = []
        first = cfg.get("header_condition_axis")
        for a in ([first] if first else []) + list(cfg.get("condition_axes") or []):
            axes.append(
                Axis(name=a["name"], values={str(k): v or {} for k, v in a["values"].items()})
            )
        return cls(
            zone_axis=tuple(str(z) for z in cfg.get("zone_axis") or ()),
            zone_label=cfg.get("zone_label") or DEFAULT_ZONE_LABEL,
            axes=tuple(axes),
            value_columns=tuple(cfg.get("value_columns") or ()),
            default_op=cfg.get("default_op"),
            empty_markers=tuple(cfg.get("empty_markers") or ()),
        )

    @property
    def has_values(self) -> bool:
        """The table carries limit values (it yields LimitRules)."""
        return bool(self.zone_axis or self.value_columns or self.axes)

    def _axis_of(self, part: str) -> Axis | None:
        return next((a for a in self.axes if part in a.values), None)

    def is_value_column(self, column: str) -> bool:
        if column in self.value_columns or column in self.zone_axis:
            return True
        parts = column.split(".")
        if len(parts) > 1 and parts[-1] in self.zone_axis:
            return True  # "<tier>.<zone>": the historical rule, kept as it was
        return bool(self.axes) and all(self._axis_of(p) is not None for p in parts)

    def meaning(self, column: str) -> ColumnMeaning:
        """Zone and axis values of a value column. A tier that no axis declares is ignored, as it
        always was for a `<tier>.<zone>` key without a matching axis."""
        if column in self.value_columns:
            return ColumnMeaning()
        parts = column.split(".")
        zone = parts[-1] if parts[-1] in self.zone_axis else None
        tiers = []
        for part in parts[:-1] if zone is not None else parts:
            axis = self._axis_of(part)
            if axis is not None:
                tiers.append((axis, part))
        return ColumnMeaning(zone=zone, tiers=tuple(tiers))

    def zone_text(self, zone: str) -> str:
        return self.zone_label.format(zone=zone)


def tier_label(axis: Axis, key: str) -> str:
    """How a tier shows in a citation label: its declared `label`, else the flow-regime form the
    corpus has used so far ("F ≤ 2000"), else the key itself."""
    spec = axis.values.get(key, {})
    if spec.get("label"):
        return str(spec["label"])
    if axis.name == "flow_regime":
        return f"F {'≤' if spec.get('op') == '<=' else '>'} {spec.get('value')}"
    return key
