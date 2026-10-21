"""Candidate limit facts of a threshold question and what is still missing to single one out
(plan docs/plans/2026-10-12-g3-tach-cau-va-du-kien-thieu.md, part B).

`CandidateStore` holds the limit rules and their conditions (fact store of plan step G2, read
only). `resolve` filters them by the slots a question states, groups what is left by *answer*
(operator, value, unit, reference oxygen) and says, following decision D38: one answer → answer;
two or three → list them; more → ask for the dimension that separates the answers best.

A slot only filters the rules that carry a condition on its dimension: a flow stated for a
parameter that does not depend on the flow is ignored, it does not remove the answer. Nothing is
assumed for a dimension the question does not state (decision D65).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.ontology.schema import Dictionaries
from app.retrieval.tokenize import normalize

LIST_LIMIT = 3  # D38: up to this many answers are listed, more are asked about
DOMAIN = "domain"  # pseudo-dimension: a parameter regulated in several domains
_OPS = {
    "<=": lambda x, v: x <= v,
    "<": lambda x, v: x < v,
    ">=": lambda x, v: x >= v,
    ">": lambda x, v: x > v,
    "=": lambda x, v: x == v,
}


@dataclass
class Condition:
    dimension: str
    op: str | None
    number: float | None
    text: str | None
    unit: str | None
    raw: str | None

    def key(self) -> str:
        """Value of the condition as one comparable string."""
        if self.text is not None:
            return self.text
        return f"{self.op or ''}{self.number:g}" if self.number is not None else (self.raw or "")


@dataclass
class Candidate:
    rule_code: str
    document_id: str
    domain: str
    table_code: str
    row_id: str
    parameter_id: str | None
    answer: tuple[Any, ...]
    citation_label: str
    equipment_type_id: str | None
    footnote_refs: list[str]
    conditions: dict[str, list[Condition]] = field(default_factory=dict)

    def path(self) -> list[str]:
        for c in self.conditions.get("equipment_path", []):
            try:
                return [str(x) for x in json.loads(c.text or "[]")]
            except ValueError:
                return []
        return []

    def value_of(self, dimension: str) -> str | None:
        if dimension == DOMAIN:
            return self.domain
        cs = self.conditions.get(dimension)
        return " + ".join(c.key() for c in cs) if cs else None

    def shown(self, dimension: str) -> str | None:
        """The value as a reader sees it: the label of an equipment row instead of its path."""
        cs = self.conditions.get(dimension)
        if cs and dimension == "equipment_path":
            return " + ".join(c.raw or c.key() for c in cs)
        return self.value_of(dimension)


class Option(BaseModel):
    answer: str
    key: list[Any] = Field(default_factory=list)  # op, value, min, max, unit, reference O2
    conditions: dict[str, str] = Field(default_factory=dict)  # what tells this answer apart
    legal_addresses: list[str] = Field(default_factory=list)
    rule_codes: list[str] = Field(default_factory=list)


class Clarification(BaseModel):
    mode: str  # answer | list | ask | none_found | not_applicable
    n_candidates: int = 0
    n_answers: int = 0
    missing: list[str] = Field(default_factory=list, description="slots to ask for, in order")
    options: list[Option] = Field(default_factory=list)
    ask_choices: list[str] = Field(default_factory=list, description="values of missing[0]")
    emptied_by: str | None = Field(
        default=None, description="the stated slot no rule of the table satisfies"
    )
    rule_codes: list[str] = Field(default_factory=list)


class CandidateStore:
    """Limit rules of the corpus in memory (about a thousand rows): built once per process."""

    def __init__(self, session: Session, d: Dictionaries) -> None:
        conds: dict[str, dict[str, list[Condition]]] = defaultdict(lambda: defaultdict(list))
        for c in session.execute(
            select(m.RuleCondition).order_by(m.RuleCondition.rule_code, m.RuleCondition.position)
        ).scalars():
            conds[c.rule_code][c.dimension].append(
                Condition(c.dimension, c.op, c.value_num, c.value_text, c.unit, c.raw_text)
            )
        self.rules: list[Candidate] = []
        for r in session.execute(select(m.LimitRule).order_by(m.LimitRule.rule_code)).scalars():
            self.rules.append(
                Candidate(
                    rule_code=r.rule_code,
                    document_id=r.document_id,
                    domain=r.domain,
                    table_code=r.table_code,
                    row_id=r.row_id,
                    parameter_id=r.parameter_id,
                    answer=(r.op, r.value, r.min, r.max, r.unit_id, r.ref_o2),
                    citation_label=r.citation_label,
                    equipment_type_id=r.equipment_type_id,
                    footnote_refs=list(r.footnote_refs or []),
                    conditions=dict(conds.get(r.rule_code, {})),
                )
            )
        self.documents = {r.document_id for r in self.rules}
        # group rows carry no value, hence no rule: their label and step come from the table rows
        self.block: dict[str, str] = {}  # row_id -> parameter block of its table
        self.headers: dict[tuple[str, str, str], str] = {}  # (table, block, label) -> path step
        self.step_label: dict[tuple[str, str, str], str] = {}  # (table, block, step) -> label
        tables = {r.table_code for r in self.rules}
        for row in session.execute(
            select(m.TableRow).where(m.TableRow.table_code.in_(tables)).order_by(m.TableRow.row_id)
        ).scalars():
            block = str(row.row_path[0]) if row.row_path else ""
            self.block[row.row_id] = block
            for c in row.conditions or []:
                if c.get("type") == "equipment_path" and c.get("value") and c.get("text"):
                    key = (row.table_code, block, normalize(c["text"]))
                    self.headers.setdefault(key, str(c["value"][-1]))
                    self.step_label.setdefault(
                        (row.table_code, block, str(c["value"][-1])), c["text"]
                    )
        self.equipment_name = {e.equipment_id: normalize(e.name) for e in d.equipment_types}
        self.slot_of = {c.name: c.slot or c.name for c in d.condition_dimensions}
        self.dimension_of = {v: k for k, v in self.slot_of.items()}
        self.ask_order = {c.name: c.ask_order for c in d.condition_dimensions} | {DOMAIN: 0}
        # a receiving-body code is stated as a word; the tables write it out ("sông, vùng nước biển")
        self.receiving_words = {
            r.code: [normalize(w) for w in (r.name, *r.aliases)] for r in d.receiving_body_types
        }
        self.unit_symbol = {u.unit_id: u.symbol for u in d.units}

    # ------------------------------------------------------------------ filters

    def _matches(self, cand: Candidate, dim: str, slot: Any) -> bool:
        """Does a rule that depends on `dim` accept the value the question states?"""
        cs = cand.conditions[dim]
        if dim in ("flow_regime", "capacity"):
            try:
                x = float(slot.value)
            except ValueError:
                return True
            for c in cs:
                if c.number is None or c.op not in _OPS:
                    continue
                if slot.unit and c.unit and slot.unit != c.unit:
                    continue  # another kind of capacity (MW against tấn hơi/giờ): no evidence
                if not _OPS[c.op](x, c.number):
                    return False
            return True
        if dim == "receiving_body":
            words = self.receiving_words.get(slot.value, [])
            text = normalize(" ".join((c.text or "") + " " + (c.raw or "") for c in cs))
            return any(w and w in text for w in words) if words else True
        return any(c.text == slot.value for c in cs)

    def _equipment(self, cands: list[Candidate], equipment_id: str) -> list[Candidate] | None:
        """Rules of the equipment label, and of the rows under it in the same block of the table.
        None when no rule carries the label (a group header): the slot then filters nothing."""
        name = self.equipment_name.get(equipment_id, "")
        anchors = set()
        for c in cands:
            block = self.block.get(c.row_id, "")
            if c.equipment_type_id == equipment_id and c.path():
                anchors.add((c.table_code, block, c.path()[-1]))
            step = self.headers.get((c.table_code, block, name))
            if step:
                anchors.add((c.table_code, block, step))
        if not anchors:
            return None
        return [
            c
            for c in cands
            if any(
                (c.table_code, self.block.get(c.row_id, ""), step) in anchors for step in c.path()
            )
        ]

    # ------------------------------------------------------------------ resolve

    def candidates(
        self, slots: list[Any], domain: str | None, document_ids: list[str] | None = None
    ) -> tuple[list[Candidate], str | None]:
        """Rules left after every stated slot, and the slot that emptied the set (if one did)."""
        stated: dict[str, list[Any]] = defaultdict(list)
        for s in slots:
            if not s.negated:
                stated[s.dimension].append(s)
        params = {s.value for s in stated.get("parameter", [])}
        cands = [c for c in self.rules if c.parameter_id in params]
        if domain and any(c.domain == domain for c in cands):
            cands = [c for c in cands if c.domain == domain]
        docs = [x for x in (document_ids or []) if x in self.documents]
        if docs and any(c.document_id in docs for c in cands):
            cands = [c for c in cands if c.document_id in docs]
        for slot_name, values in stated.items():
            if slot_name in ("parameter", "document") or not cands:
                continue
            if slot_name == "equipment":
                kept: list[Candidate] = []
                seen_anchor = False
                for s in values:
                    sub = self._equipment(cands, s.value)
                    if sub is not None:
                        seen_anchor = True
                        kept += [c for c in sub if c not in kept]
                if seen_anchor:
                    if not kept:
                        return [], slot_name
                    cands = kept
                continue
            dim = self.dimension_of.get(slot_name)
            if dim is None:
                continue
            carrying = [c for c in cands if dim in c.conditions]
            if not carrying:
                continue  # the table does not depend on this dimension: the slot says nothing
            ok = [c for c in carrying if any(self._matches(c, dim, s) for s in values)]
            if not ok:
                return [], slot_name
            cands = ok + [c for c in cands if dim not in c.conditions]
        return cands, None

    def resolve(
        self, slots: list[Any], domain: str | None, document_ids: list[str] | None = None
    ) -> Clarification:
        if not any(s.dimension == "parameter" and not s.negated for s in slots):
            return Clarification(mode="not_applicable", missing=["parameter"])
        cands, emptied = self.candidates(slots, domain, document_ids)
        if not cands:
            return Clarification(mode="none_found", emptied_by=emptied)
        return self.summarize(cands, slots)

    def narrow(self, rule_codes: list[str], slots: list[Any]) -> Clarification:
        """The same verdict on a subset of the candidates (the rows a second reader kept)."""
        keep = set(rule_codes)
        return self.summarize([c for c in self.rules if c.rule_code in keep], slots)

    def describe(self, rule_code: str) -> str:
        """One candidate as a reader sees it: its legal address and every condition in the
        words of the table."""
        c = next(x for x in self.rules if x.rule_code == rule_code)
        block = self.block.get(c.row_id, "")
        parts = []
        for dim, cs in c.conditions.items():
            text = "; ".join(x.raw or x.key() for x in cs)
            if dim == "equipment_path":  # the row under its groups: two groups share row labels
                labels = [self.step_label.get((c.table_code, block, s)) for s in c.path()]
                text = " › ".join(x for x in labels if x) or text
            parts.append(f"{self.slot_of.get(dim, dim)}: {text}")
        return f"{c.citation_label} — " + " | ".join(parts)

    def summarize(self, cands: list[Candidate], slots: list[Any]) -> Clarification:
        answers = {c.answer for c in cands}
        out = Clarification(
            mode="answer",
            n_candidates=len(cands),
            n_answers=len(answers),
            rule_codes=[c.rule_code for c in cands],
        )
        if len(answers) == 1:
            out.options = self._options(cands, [])
            return out
        stated = {self.dimension_of.get(s.dimension, s.dimension) for s in slots if not s.negated}
        out.missing, first_values = self._separating(cands, stated)
        if len(answers) <= LIST_LIMIT:
            out.mode = "list"
            out.options = self._options(cands, [self.dimension_of.get(x, x) for x in out.missing])
        else:
            out.mode = "ask"
            out.ask_choices = first_values
        return out

    def _separating(self, cands: list[Candidate], stated: set[str]) -> tuple[list[str], list[str]]:
        """Dimensions to ask for, greedily: at each step the one whose answer leaves the fewest
        answers on average; stop when every group holds one answer."""
        groups = [cands]
        chosen: list[str] = []
        first_values: list[str] = []
        dims = {DOMAIN} | {d for c in cands for d in c.conditions}
        if len({c.domain for c in cands}) > 1:  # which regulation at all comes first
            chosen.append(DOMAIN)
            first_values = sorted({c.domain for c in cands})
            groups = self._split(cands, DOMAIN)
        while any(len({c.answer for c in g}) > 1 for g in groups):
            best: tuple[float, int, str] | None = None
            for dim in sorted(dims - set(chosen) - (stated - {DOMAIN})):
                parts = [p for g in groups for p in self._split(g, dim)]
                if len(parts) == len(groups):
                    continue  # the dimension separates nothing
                n = sum(len(p) for p in parts)
                expected = sum(len(p) / n * len({c.answer for c in p}) for p in parts)
                key = (round(expected, 6), self.ask_order.get(dim, 99), dim)
                if best is None or key < best:
                    best = key
            if best is None:
                break
            dim = best[2]
            if not chosen:
                first_values = sorted({c.shown(dim) or "" for c in cands} - {""})
            chosen.append(dim)
            groups = [p for g in groups for p in self._split(g, dim)]
        return [self.slot_of.get(d, d) for d in chosen], first_values

    @staticmethod
    def _split(group: list[Candidate], dim: str) -> list[list[Candidate]]:
        parts: dict[str | None, list[Candidate]] = defaultdict(list)
        for c in group:
            parts[c.value_of(dim)].append(c)
        return list(parts.values())

    def _options(self, cands: list[Candidate], dims: list[str]) -> list[Option]:
        by_answer: dict[tuple[Any, ...], list[Candidate]] = defaultdict(list)
        for c in cands:
            by_answer[c.answer].append(c)
        out = []
        for answer, group in by_answer.items():
            tells: dict[str, str] = {}
            for dim in dims:
                values = sorted({c.shown(dim) or "" for c in group} - {""})
                if values:
                    tells[self.slot_of.get(dim, dim)] = "; ".join(values)
            out.append(
                Option(
                    answer=self.render(answer),
                    key=list(answer),
                    conditions=tells,
                    legal_addresses=[c.citation_label for c in group],
                    rule_codes=[c.rule_code for c in group],
                )
            )
        return out

    def render(self, answer: tuple[Any, ...]) -> str:
        op, value, lo, hi, unit, o2 = answer
        symbol = self.unit_symbol.get(unit, unit or "")
        if lo is not None and hi is not None:
            text = f"{lo:g} – {hi:g}"
        else:
            sign = {"<=": "≤", ">=": "≥"}.get(op, op or "")
            text = f"{sign} {value:g}" if value is not None else sign
        text = f"{text} {symbol}".strip()
        return f"{text} (O2 {o2:g} %)" if o2 is not None else text
