"""Structured lookup — the fast path for threshold questions (PLAN.md §6.3, UC-02).

When the question names a parameter (and optionally zone, flow, fuel, capacity, receiving body),
the answer lives in `limit_rules`; we fetch those rows directly and put their evidence chunks at the
top of the pack so numeric accuracy never depends on similarity search.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applicability.matching import filter_rules
from app.db import models as m
from app.ontology import corpus as corpus_mod
from app.retrieval.query import QueryUnderstanding

RBT_KEYWORDS = {
    "lake": ["hồ"],
    "river": ["sông"],
    "sea": ["biển"],
    "saline_brackish": ["mặn", "lợ"],
}


class StructuredHit(BaseModel):
    rule_code: str
    row_id: str
    chunk_id: str
    document_id: str
    table_code: str
    parameter_id: str | None
    parameter_raw: str
    zone: str | None = None
    op: str
    value: float | None = None
    min: float | None = None
    max: float | None = None
    unit_raw: str
    ref_o2: float | None = None
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    citation_label: str
    review_status: str


def _hit(r: m.LimitRule) -> StructuredHit:
    """One limit rule as a structured hit (shared by the v1 lookup and the v2 rows)."""
    return StructuredHit(
        rule_code=r.rule_code,
        row_id=r.row_id,
        chunk_id=f"chunk:{r.row_id}",
        document_id=r.document_id,
        table_code=r.table_code,
        parameter_id=r.parameter_id,
        parameter_raw=r.parameter_raw,
        zone=r.zone,
        op=r.op,
        value=r.value,
        min=r.min,
        max=r.max,
        unit_raw=r.unit_raw,
        ref_o2=r.ref_o2,
        conditions=[
            c.get("text") or f"{c.get('type')} {c.get('op') or ''} {c.get('value')}"
            for c in r.conditions
            if c.get("type") != "zone"
        ],
        exceptions=list(r.exceptions),
        citation_label=r.citation_label,
        review_status=r.review_status,
    )


def lookup(
    session: Session, qu: QueryUnderstanding, limit: int = 8
) -> tuple[list[StructuredHit], list[str]]:
    if not qu.parameters:
        return [], []
    corpus = corpus_mod.current(session)
    # threshold tables of the question's domain, or of every regulated domain when it is unclear
    tables = corpus.threshold_tables(qu.domain)
    stmt = (
        select(m.LimitRule)
        .where(m.LimitRule.table_code.in_(tables), m.LimitRule.parameter_id.in_(qu.parameters))
        .order_by(m.LimitRule.line_start, m.LimitRule.rule_code)
    )
    if qu.zone:
        stmt = stmt.where(m.LimitRule.zone == qu.zone)
    rules = list(session.execute(stmt).scalars())
    warnings: list[str] = []
    # wastewater: flow regime (Bảng 1) and receiving-body rows (T-P 4.1–4.3)
    if qu.flow_m3_day is not None:
        regime = "F<=2000" if qu.flow_m3_day <= 2000 else "F>2000"
        rules = [
            r
            for r in rules
            if not any(c.get("type") == "flow_regime" for c in r.conditions)
            or any(c.get("type") == "flow_regime" and c.get("text") == regime for c in r.conditions)
        ]
    if qu.receiving_body:
        kws = RBT_KEYWORDS.get(qu.receiving_body, [])
        keep = []
        for r in rules:
            rb = [c for c in r.conditions if c.get("type") == "receiving_body"]
            if not rb or any(any(k in (c.get("value") or "") for k in kws) for c in rb):
                keep.append(r)
        if keep:
            rules = keep
    # rules bound to an equipment type: fuel / capacity
    eq_rules = [r for r in rules if r.equipment_type_id]
    if eq_rules and (qu.fuel_type or qu.capacity_value is not None):
        kept, w = filter_rules(eq_rules, qu.fuel_type, qu.capacity_value, qu.capacity_unit)
        warnings += w
        rules = [r for r in rules if not r.equipment_type_id] + kept

    # most specific first: rows whose own conditions state the fuel / capacity the user gave,
    # then rows whose equipment label echoes the question (e.g. "hơi" for a boiler)
    q = qu.question.lower()

    def specificity(r: m.LimitRule) -> int:
        score = 0
        for c in r.conditions:
            if c.get("type") == "fuel_type" and qu.fuel_type and c.get("value") == qu.fuel_type:
                score += 2
            if c.get("type") == "capacity" and qu.capacity_value is not None:
                cands = [c] + ([c["alt"]] if c.get("alt") else [])
                if any(x.get("unit") == qu.capacity_unit for x in cands):
                    score += 2
            if c.get("type") == "equipment_path" and c.get("text"):
                label = str(c["text"]).lower()
                if any(cue in q and cue in label for cue in corpus.equipment_cue_words):
                    score += 1
        if r.equipment_type_id and "khac" in r.equipment_type_id:
            score -= 1
        return score

    rules = sorted(rules, key=lambda r: (-specificity(r), r.line_start, r.rule_code))
    hits = [_hit(r) for r in rules]
    if len(hits) > limit:
        warnings.append(
            f"{len(hits)} dòng ngưỡng khớp thông số — chỉ đưa {limit} dòng đầu vào evidence"
        )
        hits = hits[:limit]
    return hits, warnings


def hits_for_codes(session: Session, codes: list[str]) -> list[StructuredHit]:
    """Structured hits for the rows query understanding v2 settled on (D74), in the order given;
    unknown codes are skipped."""
    rows = {
        r.rule_code: r
        for r in session.execute(
            select(m.LimitRule).where(m.LimitRule.rule_code.in_(codes))
        ).scalars()
    }
    return [_hit(rows[c]) for c in codes if c in rows]
