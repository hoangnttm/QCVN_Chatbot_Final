"""RQ7(a) — controlled wrong answers for the citation validator (plan 2026-09-29 §4.3).

From every table item of the gold set (tiers A and M: the answer is one cell) we build synthetic
answers that cite real chunks and compare which rules catch them:

- P0 correct       the gold value, citing the gold row (false-alarm control)
- P1 wrong column  a value of the same row, same flow band, other zone (forbidden_numbers)
- P2 wrong band    a value of the same row, same zone, other flow band (QCVN 40 Bảng 1)
- P3 sibling row   the value of another row of the same table, same parameter and column
                   (other equipment / sub-row), citing that other row
- P5 fabricated    a number absent from every cited chunk (positive control for rule 3)

Rules 1–7 (`validator.validate`) and rule 8 (`graph_verify.check`, enforce) run on the same
answer, with the question understood by the production rules (`understand`). Deterministic; no
LLM involved.
"""

from __future__ import annotations

import datetime as dt
import random
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.evaluation.gold_schema import GoldItem
from app.graph.runtime import GraphRuntime
from app.qa import graph_verify
from app.qa.models import Answer, Basis
from app.qa.validator import numbers, validate
from app.retrieval.query import understand
from app.retrieval.retriever import EvidenceItem, RetrievalService

KINDS = ("P0", "P1", "P2", "P3", "P5")


def _evidence(session: Session, ids: list[str], codes: dict[str, str]) -> list[EvidenceItem]:
    rows = {
        c.chunk_id: c
        for c in session.execute(
            select(m.EvidenceChunk).where(m.EvidenceChunk.chunk_id.in_(ids))
        ).scalars()
    }
    return [
        EvidenceItem(
            rank=i,
            chunk_id=c.chunk_id,
            citation_label=c.citation_label,
            document_id=c.document_id,
            document_code=codes.get(c.document_id, c.document_id),
            chunk_type=c.chunk_type,
            domain=c.domain,
            text_status=c.text_status,
            score=1.0,
            sources=["perturb"],
            text=c.text,
        )
        for i, cid in enumerate(ids, start=1)
        if (c := rows.get(cid)) is not None
    ]


def _column(reason: str | None) -> tuple[str | None, str | None]:
    """'cùng dòng, cột F<=2000/B' → ('F<=2000', 'B'); 'cùng dòng, cột A' → (None, 'A')."""
    col = (reason or "").rsplit("cột", 1)[-1].strip()
    if "/" in col:
        band, zone = col.split("/", 1)
        return band, zone
    return None, col or None


def _unit(it: GoldItem) -> str:
    u = next((n.unit for n in it.expected_numbers if n.role in ("limit", "range_max")), "") or ""
    return "" if u in ("-", "–") else u


def build_cases(session: Session, items: list[GoldItem], seed: int = 7) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    # ordered: the sibling rows a case is drawn from must not depend on the physical row order
    rules = list(session.execute(select(m.LimitRule).order_by(m.LimitRule.rule_code)).scalars())
    by_key: dict[tuple[str, str | None, str], list[m.LimitRule]] = defaultdict(list)
    by_code = {r.rule_code: r for r in rules}
    for r in rules:
        by_key[(r.table_code, r.parameter_id, r.zone)].append(r)
    cases: list[dict[str, Any]] = []
    for it in items:
        if it.tier not in ("A", "M") or not it.target_rule_codes:
            continue
        gold = by_code.get(it.target_rule_codes[0])
        lim = next((n for n in it.expected_numbers if n.role == "limit"), None)
        if gold is None or lim is None:
            continue  # ranges (pH …) have two numbers; the controlled swap needs one
        hops = [r.chunk_id for r in it.grade2() if r.chunk_id != f"chunk:{gold.row_id}"]
        cite = [f"chunk:{gold.row_id}", *hops]
        unit = _unit(it)

        def add(
            kind: str,
            value: str,
            cites: list[str],
            note: str,
            it=it,
            unit=unit,
            o2: float | None = gold.ref_o2,
        ) -> None:
            tail = f" {unit}" + (f", ôxy tham chiếu {o2:g} %" if o2 is not None else "")
            text = f"Giới hạn là {value.replace('.', ',')}{tail}.".replace("  ", " ")
            cases.append(
                {
                    "item": it,
                    "kind": kind,
                    "value": value,
                    "cite": cites,
                    "text": text,
                    "note": note,
                }
            )

        add("P0", lim.value, cite, "gold value")
        band, zone = (it.expected_conditions.flow_band, it.expected_conditions.zone)
        p1 = [
            f
            for f in it.forbidden_numbers
            if _column(f.reason)[0] == band and _column(f.reason)[1] != zone
        ]
        p2 = [
            f
            for f in it.forbidden_numbers
            if band and _column(f.reason)[0] not in (None, band) and _column(f.reason)[1] == zone
        ]
        if p1:
            f = rng.choice(p1)
            add("P1", f.value, cite, f.reason or "")
        if p2:
            f = rng.choice(p2)
            add("P2", f.value, cite, f.reason or "")
        sib = [
            r
            for r in by_key.get((gold.table_code, gold.parameter_id, gold.zone), [])
            if r.row_id != gold.row_id
            and r.value is not None
            and next(iter(numbers(format(r.value, "f"))), None) != lim.value
            and (band is None or f".{band}." in r.rule_code)  # QCVN 40 Bảng 1: same flow band
        ]
        if sib:
            r = rng.choice(sib)
            v = next(iter(numbers(format(r.value, "f"))))
            # the sibling row's own O₂ reference: the answer is consistent with the chunk it cites
            add("P3", v, [f"chunk:{r.row_id}", *hops], f"row {r.row_id}", o2=r.ref_o2)
        fake = next(iter(numbers(format(float(lim.value) * 1.5 + 1, "f"))))
        add("P5", fake, cite, "fabricated")
    return cases


def run(
    session: Session, svc: RetrievalService, items: list[GoldItem], seed: int = 7
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    svc.ensure(session)
    assert svc.aliases is not None
    codes = {d.document_id: d.document_code for d in session.execute(select(m.Document)).scalars()}
    rt: GraphRuntime = svc.graph
    rows: list[dict[str, Any]] = []
    for c in build_cases(session, items, seed):
        it: GoldItem = c["item"]
        qu = understand(it.question, svc.aliases, it.as_of_date, svc.doc_domains)
        ev = _evidence(session, c["cite"], codes)
        ans = Answer(
            conclusion=c["text"],
            basis=[Basis(text=c["text"], citations=c["cite"])],
            citations=c["cite"],
        )
        v, _ = validate(ans.model_copy(deep=True), ev, qu, it.question)
        gc = graph_verify.check(
            ans, ev, qu, it.question, rt, mode="enforce", active_label=svc.index_label
        )
        rows.append(
            {
                "item_id": it.id,
                "tier": it.tier,
                "stratum": it.stratum,
                "kind": c["kind"],
                "value": c["value"],
                "cite": c["cite"],
                "note": c["note"],
                "anchors": {"zone": qu.zone, "flow": qu.flow_m3_day, "parameters": qu.parameters},
                "v17_reject": not v.ok,
                "v17_errors": v.errors,
                "v8_reject": not gc.ok,
                "v8_skipped": gc.skipped,
                "v8_status": [p.status for p in gc.paths],
                "v8_errors": gc.errors,
                "v8_ms": gc.latency_ms,
            }
        )
    return rows, summarize(rows)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"generated_at": dt.datetime.now(dt.UTC).isoformat(), "kinds": {}}
    by = defaultdict(list)
    for r in rows:
        by[r["kind"]].append(r)
    for k in KINDS:
        lst = by.get(k, [])
        if not lst:
            continue
        n = len(lst)
        v17 = sum(r["v17_reject"] for r in lst)
        v8 = sum(r["v8_reject"] for r in lst)
        both = sum(r["v17_reject"] or r["v8_reject"] for r in lst)
        out["kinds"][k] = {
            "n": n,
            "rules_1_7": v17 / n,
            "rule_8": v8 / n,
            "rules_1_8": both / n,
            "v8_skipped": sum(1 for r in lst if r["v8_skipped"]) / n,
            "v8_status": dict(Counter(s for r in lst for s in r["v8_status"])),
        }
    ms = sorted(r["v8_ms"] for r in rows)
    if ms:
        out["v8_latency_ms"] = {"p50": ms[len(ms) // 2], "p95": ms[int(len(ms) * 0.95) - 1]}
    return out
