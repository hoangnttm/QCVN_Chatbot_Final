"""Applicability assessment I/O models (PLAN.md §7). Everything is traceable: every rule that fired
carries the evidence chunk ids it rests on, every selected LimitRule keeps its citation label."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

Domain = Literal["wastewater", "emission"]


class Evidence(BaseModel):
    chunk_id: str
    citation_label: str
    document_id: str
    text_status: str


class AppliedRule(BaseModel):
    rule_id: str
    title: str
    outcome: Literal["applied", "not_triggered", "skipped_missing"]
    message: str | None = None
    detail: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class Candidate(BaseModel):
    document_id: str
    document_code: str
    status: Literal["candidate", "excluded", "indirect"]
    reason: str
    rule_ids: list[str] = Field(default_factory=list)
    pointers: list[str] = Field(default_factory=list, description="other QCVN (outside corpus)")


class SelectedLimit(BaseModel):
    rule_code: str
    table_code: str
    row_id: str
    chunk_id: str
    parameter_id: str | None
    parameter_raw: str
    zone: str
    op: str
    value: float | None = None
    min: float | None = None
    max: float | None = None
    unit_id: str | None = None
    unit_raw: str
    ref_o2: float | None = None
    conditions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    citation_label: str
    review_status: str
    selected_by: list[str] = Field(default_factory=list, description="rule ids")


class MissingInformation(BaseModel):
    field: str
    label: str
    reason: str
    rule_ids: list[str] = Field(default_factory=list)


class TransitionOutcome(BaseModel):
    applies: Literal["new_standard", "old_standard_until_deadline", "not_yet_effective", "unknown"]
    document_id: str
    effective_date: date | None
    effective_source: str | None = None
    deadline: date | None
    old_standards: list[str] = Field(default_factory=list)
    explanation: str


class AssessmentRequest(BaseModel):
    domain: Domain
    context: dict[str, Any] = Field(default_factory=dict)


class AssessmentResult(BaseModel):
    assessment_id: str | None = None
    domain: Domain
    rules_version: int
    as_of_date: date
    effective_zone: str | None = None
    zone_defaulted: bool = False
    candidates: list[Candidate] = Field(default_factory=list)
    transition: TransitionOutcome | None = None
    applied_rules: list[AppliedRule] = Field(default_factory=list)
    parameter_set: list[str] = Field(default_factory=list, description="parameter ids to control")
    parameter_notes: list[str] = Field(default_factory=list)
    limit_rules: list[SelectedLimit] = Field(default_factory=list)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    evidence_path: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str
