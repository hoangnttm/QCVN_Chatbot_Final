"""Gold set schema v2/v3 (`data/gold/gold_qa_v3.jsonl`, all tier files share it).

Every v1 field keeps its name and meaning so `scripts/eval.py` / `scripts/qa_eval.py` still read
the file. New fields anchor each item to the raw source (`source_ref`), give graded relevance by
`chunk_id`, explicit expected numbers/conditions, expected behaviour for negatives, provenance
and verification trails. Field semantics: `data/gold/README.md`.

v3 (plan 2026-10-06, round 1) adds the `val` split (the former test split, exposed once), the
question `group` (T1, T2, B1-B6), and the fact layer anchors `fact_ids` / `legal_address`."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Domain = Literal["wastewater", "emission", "cross_domain", "out_of_scope"]
Intent = Literal[
    "threshold",
    "method",
    "scope",
    "definition",
    "effectivity",
    "applicability",
    "sampling",
    "frequency",
    "other",
]
# intents of the query frame (plan G3, D54): the nine above plus three; an item may carry several
IntentV2 = Literal[
    "threshold",
    "method",
    "scope",
    "definition",
    "effectivity",
    "applicability",
    "sampling",
    "frequency",
    "management",
    "compliance_check",
    "comparison",
    "other",
]
Split = Literal["dev", "val", "test"]
Group = Literal["T1", "T2", "B1", "B2", "B3", "B4", "B5", "B6", "R", "C", "G"]
Tier = Literal["A", "B", "C", "M"]
Difficulty = Literal["easy", "medium", "hard"]
Source = Literal[
    "authored_from_table",
    "authored_from_clause",
    "pilot_log",
    "paraphrase_llm_verified",
    "template_generated",
    "v1_migrated",
]
AnswerType = Literal["value", "range", "list", "text", "date", "boolean", "pointer", "none"]
NumberRole = Literal[
    "limit",
    "range_min",
    "range_max",
    "o2_ref",
    "date",
    "factor",
    "count",
    "threshold_condition",
    "other",
]
Mode = Literal["answer", "abstain", "pointer", "clarify"]
MustNot = Literal[
    "pass_fail_verdict",
    "follow_injected_instruction",
    "numbers_outside_evidence",
    "cite_outside_pack",
    "unit_conversion",
    "invent_frequency",
    "use_replaced_values",
]
Warning = Literal[
    "draft_document",
    "external_document",
    "admin_declared_fact",
    "missing_information",
    "out_of_scope",
    "cross_domain_citation",
]
TrapType = Literal[
    "false_premise",
    "injection",
    "pass_fail_request",
    "out_of_scope",
    "missing_information",
    "routing_trap",
    "replaced_standard",
    "unit_conversion",
    "cross_domain_parameter",
    "image_only_formula",
]
CheckLevel = Literal["authored", "self_verified", "double_checked", "disputed"]
Status = Literal["draft", "reviewed", "rejected"]

DIFFICULTY_FACTORS: tuple[str, ...] = (
    "zone",
    "flow_band",
    "receiving_body",
    "sector_scope",
    "equipment_depth2",
    "equipment_depth3",
    "fuel",
    "capacity",
    "o2_ref",
    "footnote",
    "exception",
    "inheritance",
    "alias_variant",
    "cross_document",
    "temporal",
    "partial_replacement",
    "draft_registry_fact",
    "anomaly",
    "cross_domain_parameter",
    "false_premise",
    "injection",
    "no_gold_pointer",
    # tier M (plan 2026-09-29 §4.6): >= 2 grade-2 chunks in different places
    "multi_hop",
    "zone_by_definition",
    "default_zone",
)
HARD_FACTORS: frozenset[str] = frozenset(
    {
        "inheritance",
        "exception",
        "cross_document",
        "temporal",
        "partial_replacement",
        "draft_registry_fact",
        "anomaly",
        "false_premise",
        "injection",
    }
)
ID_RE = re.compile(
    r"^G-(WW|EM|XD|MET|DEF|SAM|EFF|TRN|FRQ|OOS|MIS|TRP|TA|TC|TM|B[1-6]|T[12])-\d{3,4}$"
)


def compute_difficulty(factors: list[str]) -> Difficulty:
    """Score = |factors| + |factors ∩ HARD|; easy <= 2, medium 3-4, hard >= 5 (frozen with v2)."""
    s = set(factors)
    score = len(s) + len(s & HARD_FACTORS)
    if score <= 2:
        return "easy"
    if score <= 4:
        return "medium"
    return "hard"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(_Strict):
    """Primary ground truth: where in the raw source file the answer is read from."""

    document_id: str
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["text", "registry"] = Field(
        default="text", description="registry = admin-declared fact (documents.yaml), no text span"
    )
    line_start: int = Field(ge=0)
    line_end: int = Field(ge=0)
    pdf_page: int | None = None
    printed_page: str | None = None
    table_code: str | None = None
    row_no: str | None = None
    column_path: list[str] | None = None
    cell_raw: str | None = None

    @model_validator(mode="after")
    def _order(self) -> SourceRef:
        if self.line_end < self.line_start:
            raise ValueError("line_end < line_start")
        if self.kind == "text" and self.line_start < 1:
            raise ValueError(
                "text source_ref needs line_start >= 1 (registry facts use kind=registry)"
            )
        return self


class ExpectedNumber(_Strict):
    value: str = Field(
        description="canonical form as app.qa.validator.numbers() yields, e.g. '0.05'"
    )
    display: str | None = Field(default=None, description="as printed, e.g. '0,05'")
    unit: str | None = None
    role: NumberRole = "limit"
    must_appear: bool = True


class ForbiddenNumber(_Strict):
    value: str
    unit: str | None = None
    reason: str


class ExpectedConditions(_Strict):
    zone: str | None = None
    flow_band: Literal["F<=2000", "F>2000"] | None = None
    receiving_body: str | None = None
    sector_id: str | None = None
    sector_scope_text: str | None = None
    equipment_path: str | None = None
    equipment_id: str | None = None
    fuel: str | None = None
    capacity_text: str | None = None
    o2_ref: float | None = None
    exceptions: list[str] = Field(default_factory=list)
    footnote_rule: str | None = None
    transition_date: str | None = None
    document_code: str | None = None

    def axes(self) -> dict[str, Any]:
        out = {
            k: v
            for k, v in self.model_dump().items()
            if v not in (None, [], "") and k not in ("exceptions",)
        }
        return out


class RelevantChunk(_Strict):
    chunk_id: str
    citation_label: str
    grade: Literal[0, 1, 2]
    note: str | None = None
    source_ref: SourceRef | None = None


class ExpectedBehaviour(_Strict):
    mode: Mode = "answer"
    must_not: list[MustNot] = Field(default_factory=list)
    pointer_labels: list[str] = Field(default_factory=list)
    clarification_topics: list[str] = Field(default_factory=list)


class Verification(_Strict):
    verified_by: str | None = None
    verified_at: dt.date | None = None
    method: Literal["db_lookup", "api", "source_file", "pdf", "template"] | None = None
    source_document_id: str | None = None
    source_quote: str | None = Field(default=None, max_length=600)
    second_check_by: str | None = None
    second_check_at: dt.date | None = None
    second_check_result: Literal["agree", "agree_after_fix", "disagree"] | None = None
    disagreement_note: str | None = None
    expert_correction: str | None = None


class Provenance(_Strict):
    origin: Literal["human", "llm_paraphrase", "template"]
    llm_model: str | None = None
    prompt_id: str | None = None
    original_question: str | None = None
    human_edited: bool | None = None
    template_id: str | None = None


class FrameSlot(_Strict):
    dimension: str  # parameter | zone | flow | equipment | fuel_type | receiving_body | document …
    value: str
    negated: bool = False


class GoldFrame(_Strict):
    """What query understanding should read from the question itself (plan G3, section 3): the
    intents (the first one is the main intent) and the slots the question states. A value the
    answer needs but the question does not state (the default column of an unzoned facility) is
    not a slot."""

    intents: list[IntentV2] = Field(min_length=1)
    slots: list[FrameSlot] = Field(default_factory=list)
    sub_questions: list[str] = Field(
        default_factory=list, description="domain of each sub-question; empty = not decomposed"
    )
    missing: list[str] = Field(default_factory=list)
    intents_by: Literal["template", "reviewed"] = "template"
    slots_by: Literal["cell", "draft", "reviewed"] = "cell"


class GoldItem(_Strict):
    schema_version: Literal[2, 3] = 2
    id: str = Field(pattern=ID_RE.pattern)
    revision: int = Field(default=1, ge=1)
    tier: Tier = "B"
    question: str = Field(min_length=3)
    domain: Domain
    intent: Intent
    as_of_date: dt.date | None = None
    split: Split | None = None
    group: Group | None = None
    stratum: str | None = None
    difficulty: Difficulty | None = None
    difficulty_factors: list[str] = Field(default_factory=list)
    source: Source = "authored_from_table"
    answer_type: AnswerType = "value"
    expected_answer: str
    expected_numbers: list[ExpectedNumber] = Field(default_factory=list)
    forbidden_numbers: list[ForbiddenNumber] = Field(default_factory=list)
    expected_conditions: ExpectedConditions = Field(default_factory=ExpectedConditions)
    target_rule_codes: list[str] = Field(default_factory=list)
    source_ref: SourceRef | None = None
    relevant: list[RelevantChunk] = Field(default_factory=list)
    expected_citation_labels: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    legal_address: list[str] = Field(
        default_factory=list,
        description="legal address of every must-cite basis (Điều/Khoản/Điểm, Bảng/dòng/cột)",
    )
    frame: GoldFrame | None = None
    must_abstain: bool = False
    expected_behaviour: ExpectedBehaviour = Field(default_factory=ExpectedBehaviour)
    expected_warnings: list[Warning] = Field(default_factory=list)
    trap_type: TrapType | None = None
    paired_with: str | None = None
    cluster_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    judge_notes: str | None = None
    verification: Verification = Field(default_factory=Verification)
    provenance: Provenance | None = None
    check_level: CheckLevel = "authored"
    status: Status = "draft"
    reviewer: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _factors_known(self) -> GoldItem:
        bad = [f for f in self.difficulty_factors if f not in DIFFICULTY_FACTORS]
        if bad:
            raise ValueError(f"unknown difficulty_factors {bad}")
        return self

    def grade2(self) -> list[RelevantChunk]:
        return [r for r in self.relevant if r.grade == 2]

    def relevant_ids(self, min_grade: int = 1) -> set[str]:
        return {r.chunk_id for r in self.relevant if r.grade >= min_grade}


# ---------------------------------------------------------------- strata

_TIER_ORDER = {"A": 0, "B": 1, "C": 2, "M": 3}


class Stratum(_Strict):
    code: str
    title: str
    tier: Tier = "B"
    group: Group | None = None
    domain: str | None = None
    quota_dev: int = 0
    quota_test: int = 0
    tables: list[str] = Field(default_factory=list)
    chunk_types: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    notes: str | None = None

    @property
    def quota(self) -> int:
        return self.quota_dev + self.quota_test


class CoverageRule(_Strict):
    name: str
    kind: Literal[
        "table",
        "zone",
        "flow_band",
        "o2_ref",
        "fuel",
        "capacity",
        "receiving_body",
        "tag",
        "trap",
        "frequency",
        "as_of",
        "alias_share",
        "equipment_depth3",
        "inheritance",
        "exception",
        "footnote",
    ]
    values: list[Any] = Field(default_factory=list)
    min_each: int | None = None
    min_total: int | None = None
    min_share: float | None = None
    domains: list[str] = Field(default_factory=list)


class Strata(_Strict):
    version: int = 2
    seed: int = 20260924
    strata: list[Stratum]
    coverage: list[CoverageRule] = Field(default_factory=list)
    paraphrase_max_share: float = 0.20
    paraphrase_max_per_stratum: int = 2
    trap_min: int = 15

    def by_code(self) -> dict[str, Stratum]:
        return {s.code: s for s in self.strata}


def load_strata(path: Path) -> Strata:
    with open(path, encoding="utf-8") as fh:
        return Strata.model_validate(yaml.safe_load(fh))


# ---------------------------------------------------------------- io

_KEY_ORDER = list(GoldItem.model_fields.keys())


def load_gold(path: Path, *, tier: str | None = None, split: str | None = None) -> list[GoldItem]:
    items: list[GoldItem] = []
    with open(path, encoding="utf-8") as fh:
        for ln, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:  # pragma: no cover - reported by validator
                raise ValueError(f"{path}:{ln}: invalid JSON: {e}") from e
            if raw.get("schema_version", 1) not in (2, 3):
                raw = upgrade_v1(raw)
            try:
                item = GoldItem.model_validate(raw)
            except Exception as e:
                raise ValueError(f"{path}:{ln} ({raw.get('id')}): {e}") from e
            if tier and item.tier != tier:
                continue
            if split and item.split != split:
                continue
            items.append(item)
    return items


def dump_gold(items: list[GoldItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for it in sorted(items, key=lambda x: (_TIER_ORDER[x.tier], x.id)):
            d = it.model_dump(mode="json", exclude_none=False)
            ordered = {k: d[k] for k in _KEY_ORDER if k in d}
            fh.write(json.dumps(ordered, ensure_ascii=False) + "\n")


def upgrade_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """Structural upgrade of a v1 record (labels only). Chunk resolution and source anchoring are
    done by `gold_migrate.migrate_v1`, which needs the derived data."""
    out = dict(raw)
    out["schema_version"] = 2
    out.setdefault("tier", "B")
    out.setdefault("source", "v1_migrated")
    out.setdefault("split", "dev")
    if out.get("must_abstain"):
        out.setdefault("expected_behaviour", {"mode": "abstain"})
    return out
