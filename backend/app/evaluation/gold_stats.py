"""Distribution and coverage statistics of the gold set (reported in the thesis, plan §2.6)."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.evaluation.gold_resolver import Resolver
from app.evaluation.gold_schema import GoldItem


def distributions(items: list[GoldItem]) -> dict[str, Any]:
    def c(key):
        return dict(sorted(Counter(key(it) for it in items).items(), key=lambda kv: str(kv[0])))

    tier_split: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    strat_split: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for it in items:
        tier_split[it.tier][it.split or "none"] += 1
        strat_split[f"{it.tier}:{it.stratum or '-'}"][it.split or "none"] += 1
    return {
        "n": len(items),
        "tier_split": {k: dict(v) for k, v in sorted(tier_split.items())},
        "stratum_split": {k: dict(v) for k, v in sorted(strat_split.items())},
        "domain": c(lambda it: it.domain),
        "intent": c(lambda it: it.intent),
        "difficulty": c(lambda it: it.difficulty or "unset"),
        "source": c(lambda it: it.source),
        "answer_type": c(lambda it: it.answer_type),
        "trap_type": c(lambda it: it.trap_type or "-"),
        "must_abstain": c(lambda it: it.must_abstain),
        "mode": c(lambda it: it.expected_behaviour.mode),
        "check_level": c(lambda it: it.check_level),
        "status": c(lambda it: it.status),
        "tags": dict(sorted(Counter(t for it in items for t in it.tags).items())),
        "paraphrase_share": (
            sum(1 for it in items if it.source == "paraphrase_llm_verified") / len(items)
            if items
            else 0.0
        ),
        "alias_variant_share_threshold": _share(
            items, lambda it: it.intent == "threshold", lambda it: "alias_variant" in it.tags
        ),
        "question_len_words": _quantiles([len(it.question.split()) for it in items]),
    }


def _share(items, pred, cond) -> float | None:
    pool = [it for it in items if pred(it)]
    if not pool:
        return None
    return sum(1 for it in pool if cond(it)) / len(pool)


def _quantiles(xs: list[int]) -> dict[str, float]:
    if not xs:
        return {}
    s = sorted(xs)

    def q(p):
        return s[min(len(s) - 1, int(p * (len(s) - 1)))]

    return {"min": s[0], "p25": q(0.25), "p50": q(0.5), "p75": q(0.75), "max": s[-1]}


def coverage(
    items: list[GoldItem], resolver: Resolver, all_rules: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    rule_codes = {rc for it in items for rc in it.target_rule_codes}
    params = set()
    tables = Counter()
    chunk_types = Counter()
    documents = Counter()
    for it in items:
        for code in it.target_rule_codes:
            rule = resolver.rule(code)
            if rule:
                params.add(rule.get("parameter_id") or rule.get("parameter_raw"))
                tables[rule.get("table_code")] += 1
        for rc in it.relevant:
            ch = resolver.chunk(rc.chunk_id)
            if ch:
                chunk_types[ch.get("chunk_type")] += 1
                documents[ch.get("document_id")] += 1
        if it.source_ref and it.source_ref.table_code:
            tables[it.source_ref.table_code] += 0
    zones = Counter(
        (it.domain, it.expected_conditions.zone) for it in items if it.expected_conditions.zone
    )
    flows = Counter(
        it.expected_conditions.flow_band for it in items if it.expected_conditions.flow_band
    )
    o2 = Counter(
        it.expected_conditions.o2_ref for it in items if it.expected_conditions.o2_ref is not None
    )
    fuels = Counter(it.expected_conditions.fuel for it in items if it.expected_conditions.fuel)
    rbs = Counter(
        it.expected_conditions.receiving_body
        for it in items
        if it.expected_conditions.receiving_body
    )
    factors = Counter(f for it in items for f in it.difficulty_factors)
    out = {
        "distinct_target_rules": len(rule_codes),
        "distinct_parameters": len(params),
        "tables": dict(tables),
        "chunk_types": dict(chunk_types),
        "documents": dict(documents),
        "zones": {f"{d}:{z}": n for (d, z), n in sorted(zones.items())},
        "flow_bands": dict(flows),
        "o2_refs": {str(k): v for k, v in sorted(o2.items())},
        "fuels": dict(fuels),
        "receiving_bodies": dict(rbs),
        "difficulty_factors": dict(sorted(factors.items())),
        "trap_items": sum(1 for it in items if it.trap_type),
        "frequency_items": sum(1 for it in items if it.intent == "frequency"),
    }
    if all_rules is not None:
        out["rules_total"] = len(all_rules)
        out["rule_coverage"] = len(rule_codes) / len(all_rules) if all_rules else None
    return out


def render_markdown(
    items: list[GoldItem],
    resolver: Resolver,
    *,
    show_test: bool = False,
    all_rules: list[dict[str, Any]] | None = None,
) -> str:
    d = distributions(items)
    cov = coverage(items, resolver, all_rules)
    lines = ["# Gold set v2 — thống kê", "", f"Tổng: {d['n']} câu", ""]
    lines += ["## Tầng × split", "", "| Tầng | dev | test | none |", "|---|---|---|---|"]
    for t, v in d["tier_split"].items():
        lines.append(f"| {t} | {v.get('dev', 0)} | {v.get('test', 0)} | {v.get('none', 0)} |")
    lines += [
        "",
        "## Tầng lấy mẫu × split",
        "",
        "| Tầng | dev | test | none |",
        "|---|---|---|---|",
    ]
    for t, v in d["stratum_split"].items():
        lines.append(f"| {t} | {v.get('dev', 0)} | {v.get('test', 0)} | {v.get('none', 0)} |")
    for name in (
        "domain",
        "intent",
        "difficulty",
        "source",
        "answer_type",
        "trap_type",
        "mode",
        "check_level",
        "status",
    ):
        lines += ["", f"## {name}", ""]
        for k, v in d[name].items():
            lines.append(f"- {k}: {v}")
    lines += ["", "## Chỉ số khác", ""]
    lines.append(f"- paraphrase_share: {d['paraphrase_share']:.1%}")
    if d["alias_variant_share_threshold"] is not None:
        lines.append(f"- alias_variant share (threshold): {d['alias_variant_share_threshold']:.1%}")
    lines.append(f"- question length (words): {d['question_len_words']}")
    lines += ["", "## Phủ", ""]
    for k, v in cov.items():
        lines.append(f"- {k}: {v}")
    if show_test:
        lines += ["", "## Câu hỏi (kể cả test)", ""]
        for it in items:
            lines.append(f"- `{it.id}` [{it.tier}/{it.split}/{it.stratum}] {it.question}")
    else:
        lines += ["", "## Câu hỏi dev (test ẩn — dùng --show-test)", ""]
        for it in items:
            if it.split == "dev":
                lines.append(f"- `{it.id}` [{it.tier}/{it.stratum}] {it.question}")
    return "\n".join(lines) + "\n"
