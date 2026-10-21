"""Inter-annotator agreement between the author's gold and the blind second check (plan §2.6):
number-set agreement with Wilson CI, Cohen's κ on must_abstain, label agreement, disagreement
taxonomy. Pure stdlib."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.gold_schema import GoldItem
from app.qa.validator import numbers


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def cohen_kappa(pairs: list[tuple[Any, Any]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(1 for a, b in pairs if a == b) / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if pe == 1:
        return 1.0
    return (po - pe) / (1 - pe)


_LABEL_KEY = re.compile(
    r"(Bảng \d+|TT [\d.a-z]+|Mục [\d.]+|Điều \d+|Khoản \d+|Cột [ABC]|Phụ lục \d+)", re.I
)


def label_key(label: str) -> frozenset[str]:
    return frozenset(m.group(0).lower() for m in _LABEL_KEY.finditer(label or ""))


@dataclass
class AgreementReport:
    n: int = 0
    numbers_agree: int = 0
    numbers_ci: tuple[float, float] = (0.0, 0.0)
    kappa_abstain: float | None = None
    abstain_agree: int = 0
    labels_agree: int = 0
    labels_partial: int = 0
    taxonomy: Counter = field(default_factory=Counter)
    disagreements: list[dict[str, Any]] = field(default_factory=list)


def _yes(v: str | None) -> bool | None:
    if v is None:
        return None
    t = v.strip().lower()
    if t in ("có", "co", "yes", "true", "1", "đúng"):
        return True
    if t in ("không", "khong", "no", "false", "0"):
        return False
    return None


def compare(items: list[GoldItem], seconds: list[dict[str, Any]]) -> AgreementReport:
    by_id = {it.id: it for it in items}
    rep = AgreementReport()
    pairs: list[tuple[bool, bool]] = []
    for s in seconds:
        it = by_id.get(s.get("item_id"))
        if it is None:
            continue
        rep.n += 1
        gold_nums = {n.value for n in it.expected_numbers if n.role != "date"}
        second_nums = numbers(s.get("numbers") or s.get("answer") or "")
        nums_ok = gold_nums == second_nums or (not gold_nums and not second_nums)
        if nums_ok:
            rep.numbers_agree += 1
        sec_abs = _yes(s.get("must_abstain"))
        if sec_abs is not None:
            pairs.append((it.must_abstain, sec_abs))
            if sec_abs == it.must_abstain:
                rep.abstain_agree += 1
        gold_keys = set()
        for r in it.grade2():
            gold_keys |= label_key(r.citation_label)
        sec_keys = label_key(s.get("labels") or "")
        if gold_keys and sec_keys:
            if gold_keys == sec_keys:
                rep.labels_agree += 1
            elif gold_keys & sec_keys:
                rep.labels_partial += 1
        reasons = []
        if not nums_ok:
            if gold_nums and second_nums and len(gold_nums ^ second_nums) <= 1:
                reasons.append("wrong_cell_or_decimal")
            else:
                reasons.append("numbers")
        if sec_abs is not None and sec_abs != it.must_abstain:
            reasons.append("abstain_policy")
        if gold_keys and sec_keys and gold_keys != sec_keys:
            reasons.append("label")
        if reasons:
            for r in reasons:
                rep.taxonomy[r] += 1
            rep.disagreements.append(
                {
                    "item_id": it.id,
                    "reasons": reasons,
                    "gold": sorted(gold_nums),
                    "second": sorted(second_nums),
                    "second_answer": s.get("answer"),
                }
            )
    rep.numbers_ci = wilson_ci(rep.numbers_agree, rep.n)
    rep.kappa_abstain = cohen_kappa(pairs)
    return rep


def render_markdown(rep: AgreementReport) -> str:
    lines = ["# Đồng thuận A1–A2 (kiểm mù)", "", f"n = {rep.n}", ""]
    if rep.n:
        lo, hi = rep.numbers_ci
        share = rep.numbers_agree / rep.n
        kappa = "n/a" if rep.kappa_abstain is None else f"{rep.kappa_abstain:.3f}"
        lines.append(
            f"- Khớp tập số liệu: {rep.numbers_agree}/{rep.n} = {share:.1%}"
            f" (Wilson 95 % {lo:.1%}–{hi:.1%})"
        )
        lines.append(f"- Khớp phải-từ-chối: {rep.abstain_agree}/{rep.n}; Cohen κ = {kappa}")
        lines.append(
            f"- Nhãn trích dẫn: khớp hoàn toàn {rep.labels_agree}, khớp một phần {rep.labels_partial}"
        )
        lines.append("")
        lines.append("## Phân loại bất đồng")
        lines.append("")
        for k, v in rep.taxonomy.most_common():
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append("## Danh sách")
        lines.append("")
        for d in rep.disagreements:
            lines.append(
                f"- `{d['item_id']}` {', '.join(d['reasons'])} — gold {d['gold']}"
                f" vs A2 {d['second']} ({d.get('second_answer') or ''})"
            )
    return "\n".join(lines) + "\n"
