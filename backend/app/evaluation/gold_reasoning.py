"""Measurement sets and metrics of plan step 3.4 (docs/plans/2026-10-12-g3-tach-cau-va-du-kien-
thieu.md, parts C and G7): missing facts, sub-questions and hops.

Nothing is authored by an LLM and no label comes from the fact store under test:

- *partial* questions are T1 template questions with one or two conditions left out; what the
  system should then do (answer / list / ask, which dimensions, which values) is computed from
  the raw tables read by `rawtables.py`, the reader that is independent of the parser;
- *composite* questions join a wastewater and an emission T1 question; the expected
  sub-questions are the two source items;
- *hop* labels of the multi-hop template items come from their pattern tag and from the
  must-cite chunk that is not the table row.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.evaluation import stats
from app.evaluation.gold_frame import CELL_DIMENSIONS, document_key, flow_band
from app.evaluation.gold_generate import DOMAIN_OF_TABLE, Generator
from app.evaluation.gold_schema import GoldItem
from app.evaluation.rawtables import RawCell

LIST_LIMIT = 3  # decision D38
HOP_OF_PATTERN = {
    "dz": "default_value",
    "zd": "zone_definition",
    "tr": "roadmap",
    "rp": "replacement",
    "fn_o2": "footnote",
    "fn_toc": "footnote",
}
SLOT_CHANGING = {"default_value", "replacement"}
DOMAIN_PHRASE = {"wastewater": "nước thải công nghiệp", "emission": "khí thải công nghiệp"}
FLOW_VALUE = {"F<=2000": "1.500", "F>2000": "3.000"}
# variants of a partial question: (kept conditions, sentence)
PARTIAL: dict[str, list[tuple[tuple[str, ...], str]]] = {
    "flow_zone": [
        ((), "Giới hạn {param} trong {domain} là bao nhiêu?"),
        (("zone",), "{param} trong {domain}, cột {zone}, tối đa bao nhiêu?"),
        (("flow",), "{domain_cap} lưu lượng {flow} m3/ngày thì {param} tối đa bao nhiêu?"),
    ],
    "zone": [((), "Giới hạn {param} trong {domain} là bao nhiêu?")],
    "equipment_zone": [
        (("zone",), "{param} trong {domain}, cột {zone}, tối đa bao nhiêu?"),
        (("equipment",), "Giới hạn {param} trong khí thải của {equipment} là bao nhiêu?"),
    ],
}
COMPOSITE = [
    ("sequence", "{a}; còn {b}"),
    ("compare", "So sánh giúp tôi hai trường hợp. {A} {B}"),
]


def _answer(c: RawCell) -> tuple[Any, ...]:
    return (c.op, c.value, c.vmin, c.vmax, c.o2_ref)


def _kind(cell: RawCell) -> str:
    if cell.flow_band:
        return "flow_zone"
    return "equipment_zone" if cell.equipment_path else "zone"


def generate_partial(
    settings: Settings, items: list[GoldItem], *, n: int, seed: int
) -> list[dict[str, Any]]:
    """Partial questions from T1 items, labelled from the raw tables."""
    gen = Generator(settings, settings.data_dir / "gold" / "templates_v2.yaml", seed)
    cells = {c.cell_key: c for t in DOMAIN_OF_TABLE for c in gen.reader.cells(t)}
    param_of = {}
    for key, c in cells.items():
        rule = gen.resolver.rule(key)
        if rule and rule.get("parameter_id"):
            param_of[key] = (DOMAIN_OF_TABLE[c.table_code], rule["parameter_id"])
    universe: dict[tuple[str, str], list[RawCell]] = defaultdict(list)
    for key, pid in param_of.items():
        universe[pid].append(cells[key])

    rng = random.Random(seed)
    pool = [it for it in items if it.target_rule_codes and it.target_rule_codes[0] in param_of]
    rng.shuffle(pool)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    turn: dict[str, int] = defaultdict(int)
    for it in pool:
        if len(out) >= n:
            break
        cell = cells[it.target_rule_codes[0]]
        kind = _kind(cell)
        if kind == "zone" and "." in cell.row_key:
            continue  # rows by receiving body / sector: the question would need those words
        variants = PARTIAL[kind]
        kept, sentence = variants[turn[kind] % len(variants)]
        domain, pid = param_of[cell.cell_key]
        mark = (pid, domain, ",".join(kept) + (cell.zone if "zone" in kept else ""))
        if mark in seen:
            continue
        seen.add(mark)
        turn[kind] += 1
        cands = list(universe[(domain, pid)])
        if "zone" in kept:
            cands = [c for c in cands if c.zone == cell.zone]
        if "flow" in kept:
            cands = [c for c in cands if c.flow_band in (None, cell.flow_band)]
        if "equipment" in kept:
            cands = [
                c for c in cands if (c.table_code, c.row_key) == (cell.table_code, cell.row_key)
            ]
        answers = sorted({_answer(c) for c in cands}, key=str)
        mode = "answer" if len(answers) == 1 else ("list" if len(answers) <= LIST_LIMIT else "ask")
        missing = []
        if len(answers) > 1:
            for dim, value in (
                ("zone", lambda c: c.zone),
                ("flow", lambda c: c.flow_band),
                ("equipment", lambda c: (c.table_code, c.row_key) if c.equipment_path else None),
            ):
                if dim not in kept and len({value(c) for c in cands}) > 1:
                    missing.append(dim)
        label = ", ".join(x[0].lower() + x[1:] for x in cell.row_label_path[1:])
        question = sentence.format(
            param=cell.parameter,
            domain=DOMAIN_PHRASE[domain],
            domain_cap=DOMAIN_PHRASE[domain].capitalize(),
            zone=cell.zone,
            flow=FLOW_VALUE.get(cell.flow_band or "", ""),
            equipment=label,
        )
        out.append(
            {
                "id": f"G-PQ-{len(out) + 1:03d}",
                "split": it.split,
                "source_item": it.id,
                "question": question,
                "domain": domain,
                "parameter_id": pid,
                "kept": list(kept),
                "expected": {
                    "mode": mode,
                    "n_answers": len(answers),
                    "missing": missing,
                    "answers": [list(a) for a in answers],
                },
            }
        )
    return out


def generate_composite(items: list[GoldItem], *, n: int, seed: int) -> list[dict[str, Any]]:
    """Two T1 questions of different domains joined into one."""
    rng = random.Random(seed)
    out: list[dict[str, Any]] = []
    for split in ("dev", "val"):
        by_domain: dict[str, list[GoldItem]] = defaultdict(list)
        for it in items:
            if it.split == split and it.frame is not None:
                by_domain[it.domain].append(it)
        a_items, b_items = by_domain["wastewater"], by_domain["emission"]
        rng.shuffle(a_items)
        rng.shuffle(b_items)
        share = n * len([i for i in items if i.split == split]) // max(1, len(items))
        for a, b in list(zip(a_items, b_items, strict=False))[: max(1, share)]:
            relation, sentence = COMPOSITE[len(out) % len(COMPOSITE)]
            first, second = (a, b) if len(out) % 4 < 2 else (b, a)
            qa, qb = first.question.strip(), second.question.strip()
            question = sentence.format(a=qa.rstrip("?"), b=qb[0].lower() + qb[1:], A=qa, B=qb)
            out.append(
                {
                    "id": f"G-CQ-{len(out) + 1:03d}",
                    "split": split,
                    "source_items": [first.id, second.id],
                    "question": question,
                    "expected": {
                        "relation": relation,
                        "sub_questions": [
                            {
                                "domain": x.domain,
                                "slots": [
                                    [s.dimension, s.value]
                                    for s in x.frame.slots  # type: ignore[union-attr]
                                ],
                            }
                            for x in (first, second)
                        ],
                    },
                }
            )
    return out[:n]


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------- scoring


def _slot_set(slots: list[Any], codes: dict[str, str]) -> set[tuple[str, str]]:
    out = set()
    for s in slots:
        if s.negated or s.dimension not in CELL_DIMENSIONS or s.source == "hop":
            continue
        value = s.value
        if s.dimension == "flow":
            value = flow_band(float(s.value))
        elif s.dimension == "document":
            value = document_key(codes.get(s.value, s.value)) or s.value
        out.add((s.dimension, value))
    return out


def score_partial(row: dict[str, Any], frame: Any) -> dict[str, Any]:
    exp = row["expected"]
    clar = frame.clarification or {}
    mode = clar.get("mode", "none")
    got = {
        tuple(o["key"][:4] + o["key"][5:]) for o in clar.get("options", [])
    }  # drop the unit: (op, value, min, max, o2)
    want = {tuple(a) for a in exp["answers"]}
    miss_g, miss_p = set(exp["missing"]), set(frame.missing)
    f1 = None
    if miss_g or miss_p:
        tp = len(miss_g & miss_p)
        f1 = 2 * tp / (len(miss_g) + len(miss_p))
    return {
        "id": row["id"],
        "split": row["split"],
        "expected_mode": exp["mode"],
        "mode": mode,
        "mode_ok": float(mode == exp["mode"]),
        # safety: a single value given while the tables hold several answers
        "unsafe_answer": float(mode == "answer" and exp["mode"] != "answer"),
        "n_answers_ok": float(clar.get("n_answers") == exp["n_answers"]),
        "missing_f1": f1,
        "first_missing_ok": (
            float(bool(frame.missing) and frame.missing[0] in miss_g)
            if exp["mode"] == "ask"
            else None
        ),
        "options_ok": float(got == want) if exp["mode"] in ("answer", "list") else None,
        "detail": {"missing": frame.missing, "expected_missing": exp["missing"]},
    }


def score_composite(row: dict[str, Any], frame: Any, codes: dict[str, str]) -> dict[str, Any]:
    exp = row["expected"]["sub_questions"]
    subs = frame.sub_questions
    domains_ok = sorted(s.domain for s in subs) == sorted(e["domain"] for e in exp)
    tp = fp = fn = 0
    for e in exp:
        gold = {(d, v) for d, v in e["slots"] if d in CELL_DIMENSIONS}
        sub = next((s for s in subs if s.domain == e["domain"]), None)
        got = _slot_set(sub.slots, codes) if sub else set()
        tp, fp, fn = tp + len(gold & got), fp + len(got - gold), fn + len(gold - got)
    return {
        "id": row["id"],
        "split": row["split"],
        "split_ok": float(len(subs) == len(exp) and domains_ok),
        "relation_ok": float(frame.relation == row["expected"]["relation"]),
        "slots": (tp, fp, fn),
        "detail": {"domains": [s.domain for s in subs]},
    }


def hop_label(item: GoldItem) -> tuple[str, str] | None:
    """(hop type, chunk) a multi-hop template item needs."""
    pattern = next((t.removeprefix("pattern_") for t in item.tags if t.startswith("pattern_")), "")
    kind = HOP_OF_PATTERN.get(pattern)
    row = f"chunk:{item.cluster_id}"  # the table row of the cell
    hop_chunks = [r.chunk_id for r in item.grade2() if r.chunk_id != row]
    return (kind, hop_chunks[0]) if kind and hop_chunks else None


def score_hops(item: GoldItem, frame: Any) -> dict[str, Any]:
    label = hop_label(item)
    types = [h.type for h in frame.hops]
    row: dict[str, Any] = {
        "id": item.id,
        "split": item.split,
        "group": item.group,
        "types": types,
        "slot_changing": float(any(t in SLOT_CHANGING for t in types)),
    }
    if label is None:
        return row
    kind, chunk = label
    mine = [h for h in frame.hops if h.type == kind]
    resolved = any(chunk in h.chunk_ids and h.status == "resolved" for h in mine)
    cond = item.expected_conditions
    effect_ok = None
    if kind == "default_value":
        effect_ok = float(any(h.effect.get("value") == cond.zone for h in mine))
    elif kind == "replacement":
        effect_ok = float(
            any(
                h.effect.get("to") == (item.source_ref.document_id if item.source_ref else "")
                for h in mine
            )
        )
    clar = frame.clarification or {}
    row |= {
        "expected_type": kind,
        "detected": float(bool(mine)),
        "extra_types": [t for t in types if t != kind],
        "resolved": float(resolved),
        "effect_ok": effect_ok,
        # both bases: the cell is singled out (one answer, the gold rule among the cells) and the
        # second basis is the gold chunk
        "both_bases": float(
            resolved
            and clar.get("mode") == "answer"
            and item.target_rule_codes[0] in clar.get("rule_codes", [])
        ),
    }
    return row


def mean(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return stats.summarize_metric([r.get(key) for r in rows])


def micro_f1(rows: list[dict[str, Any]]) -> float | None:
    tp = sum(r["slots"][0] for r in rows)
    fp = sum(r["slots"][1] for r in rows)
    fn = sum(r["slots"][2] for r in rows)
    return 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
