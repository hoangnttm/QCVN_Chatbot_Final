"""Load data/rules/applicability_v1.yaml and resolve every evidence reference to a chunk id.

Evidence entries are citation labels (exact match on `evidence_chunks.citation_label`), registry
facts (`{registry_fact: {document_id, fact_type}}`), table footnotes (`{table_footnotes: code}`) or
monitoring requirements (`{monitoring: TT10.MR.xx}`). An unresolvable reference is an error: a rule
must never fire without citable evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applicability.models import Evidence
from app.db import models as m

OPS = {"eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte"}


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    op: str
    value: Any = None


class FieldOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str  # bool | enum | number | date | list | string
    label: str
    required_for: list[str] = Field(default_factory=list)
    options: list[FieldOption] | None = None
    options_from: str | None = None
    help: str | None = None


class DomainSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    fields: list[FieldSpec]


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    domain: str
    title: str
    action: str
    when: list[Condition] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    message: str
    evidence: list[Any] = Field(default_factory=list)
    evidence_resolved: list[Evidence] = Field(default_factory=list)


class RuleSet(BaseModel):
    version: int
    updated: Any
    disclaimer: str
    schemas: dict[str, DomainSchema]
    rules: list[Rule]

    def for_domain(self, domain: str) -> list[Rule]:
        return [r for r in self.rules if r.domain == domain and r.action != "qa_policy"]

    def by_id(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.id == rule_id)

    def field(self, domain: str, name: str) -> FieldSpec | None:
        return next((f for f in self.schemas[domain].fields if f.name == name), None)


def load_rules(path: Path) -> RuleSet:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    rs = RuleSet.model_validate(raw)
    ids = [r.id for r in rs.rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate rule ids: {sorted(dupes)}")
    for r in rs.rules:
        for c in r.when:
            if c.op not in OPS:
                raise ValueError(f"{r.id}: unknown op {c.op}")
        for f in r.requires:
            if r.domain != "both" and rs.field(r.domain, f) is None:
                raise ValueError(f"{r.id}: requires unknown field {f}")
    return rs


def _chunk_ev(c: m.EvidenceChunk, status: dict[str, str]) -> Evidence:
    return Evidence(
        chunk_id=c.chunk_id,
        citation_label=c.citation_label,
        document_id=c.document_id,
        text_status=status.get(c.document_id, "unverified"),
    )


def resolve_evidence(rs: RuleSet, session: Session) -> list[str]:
    """Fill `evidence_resolved` on every rule; return the list of unresolved references."""
    status = {d.document_id: d.text_status for d in session.execute(select(m.Document)).scalars()}
    problems: list[str] = []
    for r in rs.rules:
        out: list[Evidence] = []
        for ref in r.evidence:
            chunks: list[m.EvidenceChunk] = []
            if isinstance(ref, str):
                chunks = list(
                    session.execute(
                        select(m.EvidenceChunk).where(m.EvidenceChunk.citation_label == ref)
                    ).scalars()
                )
            elif "registry_fact" in ref:
                spec = ref["registry_fact"]
                chunks = list(
                    session.execute(
                        select(m.EvidenceChunk).where(
                            m.EvidenceChunk.chunk_type == "registry_fact",
                            m.EvidenceChunk.document_id == spec["document_id"],
                            m.EvidenceChunk.source_id
                            == f"{spec['document_id']}:registry_fact:{spec['fact_type']}",
                        )
                    ).scalars()
                )
            elif "table_footnotes" in ref:
                t = session.get(m.TableDef, ref["table_footnotes"])
                if t is not None:
                    chunks = list(
                        session.execute(
                            select(m.EvidenceChunk).where(
                                m.EvidenceChunk.chunk_type == "footnote",
                                m.EvidenceChunk.source_id.like(f"{t.node_id}/%"),
                            )
                        ).scalars()
                    )
            elif "monitoring" in ref:
                q = session.get(m.MonitoringRequirement, ref["monitoring"])
                if q is not None:
                    c = session.get(m.EvidenceChunk, q.evidence_chunk_id)
                    chunks = [c] if c else []
            if not chunks:
                problems.append(f"{r.id}: evidence not found: {ref!r}")
                continue
            out.extend(_chunk_ev(c, status) for c in chunks)
        r.evidence_resolved = out
    return problems


def evaluate(conds: list[Condition], ctx: dict[str, Any]) -> bool:
    for c in conds:
        v = ctx.get(c.field)
        if c.op == "eq" and not (v == c.value):
            return False
        if c.op == "ne" and not (v != c.value):
            return False
        if c.op == "in" and v not in (c.value or []):
            return False
        if c.op == "not_in" and v in (c.value or []):
            return False
        if c.op in ("gt", "gte", "lt", "lte"):
            if v is None:
                return False
            if c.op == "gt" and not v > c.value:
                return False
            if c.op == "gte" and not v >= c.value:
                return False
            if c.op == "lt" and not v < c.value:
                return False
            if c.op == "lte" and not v <= c.value:
                return False
    return True
