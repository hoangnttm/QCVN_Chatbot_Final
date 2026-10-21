"""Risk scoring and deterministic sampling of table rows for expert review (PLAN.md §11).
Shared by the Excel pack (`scripts/review_pack.py`) and the admin review API."""

from __future__ import annotations

import math
import re
from typing import Any

from app.db import models as m


def natural_key(s: str) -> list[Any]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def risk_score(r: m.TableRow) -> int:
    score = 0
    if r.exceptions:
        score += 3
    if r.parse_warnings:
        score += 3
    if r.inherits_from:
        score += 2
    if any(c.get("type") not in ("zone", "equipment_path") for c in r.conditions):
        score += 2
    if any(v.get("op") == "between" for v in r.values.values()):
        score += 2
    if r.footnote_refs:
        score += 1
    if any(v.get("ref_o2") is not None for v in r.values.values()):
        score += 1
    return score


def sample_rows(rows: list[m.TableRow], n: int) -> list[m.TableRow]:
    """Risk-first, one row per distinct parameter first, then evenly spaced fill; stable order."""
    rows = sorted(rows, key=lambda r: (r.line_start, natural_key(r.row_id)))
    data = [r for r in rows if not r.is_group_header]
    if len(data) <= n:
        return data
    chosen: list[m.TableRow] = []
    seen_params: set[str] = set()
    for r in sorted(data, key=lambda r: (-risk_score(r), r.line_start, natural_key(r.row_id))):
        pid = r.entities.get("parameter_id") or r.cells.get("parameter") or r.row_id
        if risk_score(r) > 0 or pid not in seen_params:
            if len(chosen) >= n:
                break
            chosen.append(r)
            seen_params.add(pid)
    if len(chosen) < n:
        rest = [r for r in data if r not in chosen]
        step = max(1, len(rest) // (n - len(chosen)))
        chosen += rest[::step][: n - len(chosen)]
    return sorted(chosen, key=lambda r: (r.line_start, natural_key(r.row_id)))


def required_sample(rows_total: int, ratio: float = 0.10) -> int:
    """PLAN.md §11: bulk approval of a table needs a checked sample of >= 10 % of its rows."""
    return max(1, math.ceil(rows_total * ratio)) if rows_total > 0 else 0
