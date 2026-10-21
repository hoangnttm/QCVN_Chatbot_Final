"""Intermediate artefacts produced by the ingestion pipeline (PLAN.md §4, §5).

These are plain Pydantic models serialised to data/derived/<document_id>/*.json so every stage can
be inspected and snapshot-tested before anything is written to a database.
"""

from typing import Any

from pydantic import BaseModel, Field


class Span(BaseModel):
    """Where a node comes from — 1-based inclusive line numbers of the *original* source file
    (ADR-0003). PDF fields are filled by pdf_extract's page map."""

    file_hash: str
    line_start: int
    line_end: int
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None
    printed_page: str | None = None


class Node(BaseModel):
    node_id: str
    document_id: str
    kind: str
    order: int
    depth: int
    parent_id: str | None = None
    number: str | None = None
    label: str | None = Field(default=None, description="point letter a/b/đ")
    title: str | None = None
    term: str | None = Field(default=None, description="Definition term")
    text: str | None = Field(default=None, description="plain text (markup + LaTeX flattened)")
    raw: str = Field(description="raw source lines joined with \\n")
    span: Span
    citation_label: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)
    table_lines: list[str] | None = Field(
        default=None, description="raw pipe-table lines when kind is a table block"
    )


class CoverageReport(BaseModel):
    total_nonblank: int
    covered: int
    uncovered: list[tuple[int, str]] = Field(default_factory=list, description="(line_no, text)")

    @property
    def ratio(self) -> float:
        return self.covered / self.total_nonblank if self.total_nonblank else 1.0


class ParsedStructure(BaseModel):
    document_id: str
    file_hash: str
    grammar_path: str
    line_range: tuple[int, int]
    nodes: list[Node]
    coverage: CoverageReport
    warnings: list[str] = Field(default_factory=list)

    def kind_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for n in self.nodes:
            counts[n.kind] = counts.get(n.kind, 0) + 1
        return dict(sorted(counts.items()))

    def by_id(self) -> dict[str, Node]:
        return {n.node_id: n for n in self.nodes}


# ----------------------------------------------------------------------------- tables (spec 4.4)


class Value(BaseModel):
    """One limit cell: '≤ 200(4)' -> op '<=', value 200, ref_o2 4."""

    op: str  # <=, <, >=, >, =, between
    value: float | None = None
    min: float | None = None
    max: float | None = None
    ref_o2: float | None = None
    raw: str


class Condition(BaseModel):
    # flow_regime | equipment_path | fuel_type | capacity | receiving_body | sector_scope |
    # co_processing | exclusion | zone | other
    type: str
    op: str | None = None
    value: Any = None
    unit: str | None = None
    alt: dict[str, Any] | None = None
    text: str | None = None
    inherited_from: str | None = Field(default=None, description="row_id of the ancestor row")


class Method(BaseModel):
    code: str
    order: int
    stage: str  # sampling | field | lab


class TableRow(BaseModel):
    row_id: str
    table_code: str
    row_no: str
    row_path: list[str]
    is_group_header: bool = False
    inherits_from: str | None = None
    cells: dict[str, str] = Field(description="plain-text cells keyed by logical column")
    raw_cells: dict[str, str] = Field(description="raw cells (Markdown/LaTeX kept)")
    entities: dict[str, Any] = Field(default_factory=dict)
    values: dict[str, Value] = Field(default_factory=dict)
    conditions: list[Condition] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    methods: list[Method] = Field(default_factory=list)
    conditional_parameters: list[dict[str, Any]] = Field(default_factory=list)
    vsic_codes: list[str] = Field(default_factory=list)
    coefficient: dict[str, Any] | None = None
    footnote_refs: list[str] = Field(default_factory=list)
    span: Span
    citation_label: str
    parse_warnings: list[str] = Field(default_factory=list)


class ParsedTable(BaseModel):
    table_code: str
    document_id: str
    node_id: str
    title: str | None = None
    columns: list[str]
    header_rows: list[list[str]]
    rows: list[TableRow]
    config: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def data_rows(self) -> list[TableRow]:
        return [r for r in self.rows if not r.is_group_header]


class ParsedTables(BaseModel):
    document_id: str
    tables: list[ParsedTable]
    warnings: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------- rules & chunks


class LimitRule(BaseModel):
    """One threshold = one value cell of a table row: the row × its column (a zone, a flow tier,
    an averaging period …; `zone` is empty for tables without a zone axis).
    Deterministically derived; never touched by an LLM (PLAN.md §11)."""

    rule_code: str
    document_id: str
    domain: str
    table_code: str
    row_id: str
    parameter_raw: str
    parameter_id: str | None = None
    unit_raw: str
    unit_id: str | None = None
    zone: str | None
    zone_scheme: str | None = Field(
        default=None, description="what the zone code is scoped by: the document that defines it"
    )
    column_key: str | None = Field(default=None, description="value column of the cell")
    cell_raw: str | None = Field(default=None, description="the cell as printed (plain text)")
    address: list[dict[str, Any]] = Field(
        default_factory=list, description="legal address down to the cell (app.ingestion.address)"
    )
    op: str
    value: float | None = None
    min: float | None = None
    max: float | None = None
    ref_o2: float | None = None
    conditions: list[Condition] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    equipment_type_id: str | None = None
    citation_label: str
    span: Span
    footnote_refs: list[str] = Field(default_factory=list)
    review_status: str = "pending"


class EvidenceChunk(BaseModel):
    chunk_id: str
    document_id: str
    chunk_type: str  # clause, point, definition, paragraph, footnote, preamble, table_row, formula, registry_fact
    domain: str
    scope: str = "in_scope"
    source_id: str = Field(description="node_id or row_id the chunk was extracted from")
    text: str = Field(description="plain text of the unit itself")
    context_text: str = Field(
        description="text used for BM25/embedding: label + parents + unit text"
    )
    citation_label: str
    span: Span
    entities: dict[str, Any] = Field(default_factory=dict)
    footnote_refs: list[str] = Field(default_factory=list)
    text_status: str = "official"


class ExtractedRelation(BaseModel):
    type: str  # REPLACES | REPEALS | REFERENCES
    target_code: str
    target_document_id: str | None = None
    scope: str = "full"
    target_part: str | None = None
    condition: str | None = None
    source_node_id: str
    citation_label: str
    span: Span


class TransitionRule(BaseModel):
    rule_id: str
    document_id: str
    kind: str  # transition | roadmap | effective
    text: str
    dates: list[str] = Field(default_factory=list, description="ISO dates found in the text")
    relative_dates: list[str] = Field(
        default_factory=list, description="phrases resolved via registry facts"
    )
    source_node_id: str
    citation_label: str
    span: Span


class MonitoringRequirement(BaseModel):
    """A monitoring rule stated in prose (TT 10/2021 Đ3, Đ16–18, PL5, PL6) — PLAN.md §4.1 C.
    `text` is always the verbatim node text; `summary`/`conditions` come from the curated
    mapping file and are reviewed by the expert (review_status)."""

    requirement_id: str
    document_id: str
    # sampling_condition | sampling_port | direct_measurement | method_precedence | cems |
    # frequency_pointer
    requirement_type: str
    domain: str  # wastewater | emission | both | other
    summary: str
    text: str
    conditions: list[Condition] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list, description="parameter ids")
    references: list[str] = Field(default_factory=list, description="appendix/article referred to")
    source_node_id: str
    evidence_chunk_id: str
    citation_label: str
    span: Span
    review_status: str = "pending"


class ExtractedRelations(BaseModel):
    document_id: str
    relations: list[ExtractedRelation] = Field(default_factory=list)
    transition_rules: list[TransitionRule] = Field(default_factory=list)
    effective_date_from_text: str | None = None
    mismatches: list[str] = Field(
        default_factory=list, description="text vs registry disagreements → review"
    )


class IngestionReport(BaseModel):
    document_id: str
    text_status: str
    coverage_ratio: float
    node_counts: dict[str, int]
    table_row_counts: dict[str, int]
    rules: int
    chunks: int
    relations: int = 0
    transition_rules: int = 0
    monitoring_requirements: int = 0
    unresolved_entities: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
