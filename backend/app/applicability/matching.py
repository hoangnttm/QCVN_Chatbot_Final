"""Shared LimitRule condition matching (fuel / capacity) used by the applicability engine and by
structured lookup in retrieval, so both agree on what "matches the user's boiler" means."""

from __future__ import annotations

from typing import Any

_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "=": lambda a, b: a == b,
}


def rule_fuel_ok(conditions: list[dict[str, Any]], fuel: str | None) -> bool:
    """True when the rule has no fuel condition or one of its fuel conditions equals `fuel`."""
    if not fuel or fuel == "unknown":
        return True
    fc = [c for c in conditions if c.get("type") == "fuel_type"]
    return not fc or any(c.get("value") == fuel for c in fc)


def rule_capacity_ok(
    conditions: list[dict[str, Any]], value: float | None, unit: str | None
) -> bool:
    """True when no capacity condition uses `unit`, or every one that does is satisfied by `value`
    (conditions may carry an `alt` expressed in another unit, e.g. tấn hơi/giờ vs Kcal/giờ)."""
    if value is None or not unit:
        return True
    for c in conditions:
        if c.get("type") != "capacity":
            continue
        cands = [c] + ([c["alt"]] if c.get("alt") else [])
        same = [x for x in cands if x.get("unit") == unit and x.get("value") is not None]
        if not same:
            continue
        x = same[0]
        op = _OPS.get(x.get("op") or ">=")
        if op and not op(float(value), float(x["value"])):
            return False
    return True


def filter_rules(
    rules: list[Any], fuel: str | None, cap_value: float | None, cap_unit: str | None
) -> tuple[list[Any], list[str]]:
    """Apply fuel then capacity filters; never returns an empty list silently — when a filter would
    remove everything the original list is kept and a warning explains why."""
    warnings: list[str] = []
    out = rules
    if fuel and fuel != "unknown":
        keep = [r for r in out if rule_fuel_ok(r.conditions, fuel)]
        if keep:
            out = keep
        else:
            warnings.append(
                f"nhiên liệu {fuel!r} không khớp dòng thiết bị nào — giữ nguyên để tham khảo"
            )
    if cap_value is not None and cap_unit:
        keep = [r for r in out if rule_capacity_ok(r.conditions, cap_value, cap_unit)]
        if keep:
            out = keep
        else:
            warnings.append(
                f"công suất {cap_value:g} {cap_unit} không thỏa điều kiện của dòng nào — giữ nguyên để tham khảo"
            )
    return out, warnings
