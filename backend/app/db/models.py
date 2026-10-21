"""PostgreSQL system of record (PLAN.md §4.1, ADR-0002).

Every row keeps its provenance: `document_id` -> `document_versions.sha256`, and a span
(line / PDF page) on structure nodes, table rows, rules and chunks. JSONB columns hold the
already-validated Pydantic payloads from the ingestion artefacts (spec 4.4) so the database mirrors
`data/derived/<doc>/*.json` one-to-one and the graph (Neo4j) can be rebuilt from it at any time.
"""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1536  # gemini-embedding-001 with output_dimensionality=1536 (ADR-0005)


class Base(DeclarativeBase):
    pass


class _Span:
    """Mixin: provenance of a row inside the immutable source file."""

    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False)
    pdf_page_start: Mapped[int | None] = mapped_column(Integer)
    pdf_page_end: Mapped[int | None] = mapped_column(Integer)
    printed_page: Mapped[str | None] = mapped_column(String(16))


# ----------------------------------------------------------------------------- A. registry


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_code: Mapped[str] = mapped_column(String(128), nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    issuer: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    text_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified")
    ingest_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    grammar: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_document_versions_doc_sha", "document_id", "sha256", unique=True),)


class RegistryFact(Base):
    """ADR-0004: dates/numbers, either from the text or declared by the admin."""

    __tablename__ = "registry_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    fact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # from_text | admin_declared
    evidence: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    declared_by: Mapped[str | None] = mapped_column(Text)
    declared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class DocumentRelation(Base):
    """REPLACES / REPEALS / PROMULGATES / SUPPLEMENTS / REFERENCES — from the registry
    (`origin='registry'`) or extracted from the text (`origin='text'`, with its node)."""

    __tablename__ = "document_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.document_id", ondelete="SET NULL")
    )
    target_code: Mapped[str | None] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(8), nullable=False, default="full")
    target_part: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)  # registry | text
    source_node_id: Mapped[str | None] = mapped_column(String(256))
    citation_label: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )

    __table_args__ = (Index("ix_document_relations_src", "source_document_id", "type"),)


# ----------------------------------------------------------------------------- B. structure


class StructureNode(_Span, Base):
    __tablename__ = "structure_nodes"

    node_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(256))
    number: Mapped[str | None] = mapped_column(String(32))
    label: Mapped[str | None] = mapped_column(String(8))
    title: Mapped[str | None] = mapped_column(Text)
    term: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    citation_label: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="in_scope")
    domain: Mapped[str | None] = mapped_column(String(16))
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_structure_nodes_doc_kind", "document_id", "kind"),
        Index("ix_structure_nodes_parent", "parent_id"),
        Index("ix_structure_nodes_label", "citation_label"),
    )


class TableDef(Base):
    __tablename__ = "tables"

    table_code: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    columns: Mapped[list] = mapped_column(JSONB, nullable=False)
    header_rows: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class TableRow(_Span, Base):
    """One JSON per logical row (docs/table-row-json-spec.md)."""

    __tablename__ = "table_rows"

    row_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    table_code: Mapped[str] = mapped_column(
        ForeignKey("tables.table_code", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    row_no: Mapped[str] = mapped_column(String(32), nullable=False)
    row_path: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_group_header: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inherits_from: Mapped[str | None] = mapped_column(String(160))
    cells: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_cells: Mapped[dict] = mapped_column(JSONB, nullable=False)
    entities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    exceptions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    methods: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    conditional_parameters: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    vsic_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    coefficient: Mapped[dict | None] = mapped_column(JSONB)
    footnote_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    citation_label: Mapped[str] = mapped_column(Text, nullable=False)
    parse_warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (Index("ix_table_rows_table", "table_code"),)


# ----------------------------------------------------------------------------- C/E. rules


class LimitRule(_Span, Base):
    """One value cell: row × column (zone, flow tier, averaging period …). `zone` is NULL for
    tables without a zone axis. Never written by an LLM (PLAN.md §11)."""

    __tablename__ = "limit_rules"

    rule_code: Mapped[str] = mapped_column(String(192), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    table_code: Mapped[str] = mapped_column(String(128), nullable=False)
    row_id: Mapped[str] = mapped_column(
        ForeignKey("table_rows.row_id", ondelete="CASCADE"), nullable=False
    )
    parameter_raw: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_id: Mapped[str | None] = mapped_column(String(64))
    unit_raw: Mapped[str] = mapped_column(Text, nullable=False)
    unit_id: Mapped[str | None] = mapped_column(String(32))
    zone: Mapped[str | None] = mapped_column(String(4))
    # --- fact layer (plan 2026-10-06 G2): the cell the rule was read from, by legal address
    zone_scheme: Mapped[str | None] = mapped_column(String(64))
    column_key: Mapped[str | None] = mapped_column(String(64))
    cell_raw: Mapped[str | None] = mapped_column(Text)
    address: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    trust: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )  # approved | auto_verified | pending
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    op: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    min: Mapped[float | None] = mapped_column(Float)
    max: Mapped[float | None] = mapped_column(Float)
    ref_o2: Mapped[float | None] = mapped_column(Float)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    exceptions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    equipment_type_id: Mapped[str | None] = mapped_column(String(96))
    citation_label: Mapped[str] = mapped_column(Text, nullable=False)
    footnote_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    __table_args__ = (
        Index("ix_limit_rules_param", "parameter_id"),
        Index("ix_limit_rules_doc_zone", "document_id", "zone"),
        Index("ix_limit_rules_equipment", "equipment_type_id"),
    )


class RuleCondition(Base):
    """One condition of a limit rule on a declared dimension (condition_dimensions.yaml):
    the queryable form of `limit_rules.conditions`."""

    __tablename__ = "rule_conditions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_code: Mapped[str] = mapped_column(
        ForeignKey("limit_rules.rule_code", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    op: Mapped[str | None] = mapped_column(String(8))
    value_num: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    raw_text: Mapped[str | None] = mapped_column(Text)
    inherited_from: Mapped[str | None] = mapped_column(String(160))
    scope: Mapped[str | None] = mapped_column(String(64))  # document a scoped code belongs to

    __table_args__ = (
        Index("ix_rule_conditions_rule", "rule_code"),
        Index("ix_rule_conditions_dim", "dimension", "value_text"),
    )


class Fact(Base):
    """Registry of citable statements: every limit, definition, transition clause, monitoring
    requirement, method, document relation and effective date, with the chunk that carries it,
    its legal address, a trust level and the interval it is in force."""

    __tablename__ = "facts"

    fact_id: Mapped[str] = mapped_column(String(320), primary_key=True)
    fact_type: Mapped[str] = mapped_column(String(24), nullable=False)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str | None] = mapped_column(String(288))
    address: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    address_label: Mapped[str | None] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(Text)  # column key, method code, relation …
    quote: Mapped[str | None] = mapped_column(Text)  # verbatim text of the cell / term / value
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    trust: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    ref_table: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(320), nullable=False)

    __table_args__ = (
        Index("ix_facts_doc_type", "document_id", "fact_type"),
        Index("ix_facts_chunk", "chunk_id"),
    )


class TransitionRule(_Span, Base):
    __tablename__ = "transition_rules"

    rule_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    dates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    relative_dates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source_node_id: Mapped[str] = mapped_column(String(256), nullable=False)
    citation_label: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )


class MonitoringRequirement(_Span, Base):
    """Prose monitoring rules from TT 10/2021 (PLAN.md §4.1 C); text verbatim, structure curated."""

    __tablename__ = "monitoring_requirements"

    requirement_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    requirement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    parameters: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    references: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source_node_id: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_chunk_id: Mapped[str] = mapped_column(String(288), nullable=False)
    citation_label: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    __table_args__ = (
        Index("ix_monitoring_requirements_doc_type", "document_id", "requirement_type"),
    )


# ----------------------------------------------------------------------------- H. evidence


class EvidenceChunk(_Span, Base):
    __tablename__ = "evidence_chunks"

    chunk_id: Mapped[str] = mapped_column(String(288), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    chunk_type: Mapped[str] = mapped_column(String(24), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="in_scope")
    source_id: Mapped[str] = mapped_column(String(288), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    context_text: Mapped[str] = mapped_column(Text, nullable=False)
    citation_label: Mapped[str] = mapped_column(Text, nullable=False)
    entities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    footnote_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    text_status: Mapped[str] = mapped_column(String(16), nullable=False, default="official")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_evidence_chunks_doc", "document_id"),
        Index("ix_evidence_chunks_domain_type", "domain", "chunk_type"),
        Index("ix_evidence_chunks_label", "citation_label"),
        # cosine HNSW over the Gemini embeddings (pgvector requires <= 2000 dims)
        Index(
            "ix_evidence_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class IndexVersion(Base):
    """A consistent snapshot of BM25 + vectors; only one is `active` (UC-08)."""

    __tablename__ = "index_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="building")
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    document_hashes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewDecision(Base):
    """Every expert / admin verdict on a rule, row, relation, fact or mapping (PLAN.md §11 audit).
    `target_type` names the table or sheet, `target_id` its key; bulk approvals keep the sample
    evidence in `note`."""

    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(288), nullable=False)
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # approved|rejected|needs_recheck
    correction: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source: Mapped[str | None] = mapped_column(Text)  # review pack file + sheet

    __table_args__ = (Index("ix_review_decisions_target", "target_type", "target_id"),)


class ApplicabilityAssessment(Base):
    """Stored result of POST /applicability-assessments (PLAN.md §7) — context + full result JSON."""

    __tablename__ = "applicability_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    rules_version: Mapped[int] = mapped_column(Integer, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QueryLog(Base):
    """Audit record per question (PLAN.md §8): understanding, retrieved chunks, model, validator."""

    __tablename__ = "query_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    intent: Mapped[str] = mapped_column(String(16), nullable=False)
    understanding: Mapped[dict] = mapped_column(JSONB, nullable=False)
    retrieved: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    structured_hits: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # answered|abstained|failed|error
    path: Mapped[str] = mapped_column(String(16), nullable=False)  # policy|llm|fallback|error
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(64))
    thinking_level: Mapped[str | None] = mapped_column(String(16))
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    latency_ms: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    validation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_id: Mapped[str | None] = mapped_column(String(36))  # anonymous browser id (X-Client-Id)
    error: Mapped[str | None] = mapped_column(Text)  # status=error: exception text
    cost_usd: Mapped[float | None] = mapped_column(Float)  # list-price estimate (core/pricing.py)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_query_logs_created_at", "created_at"),
        Index("ix_query_logs_client_created", "client_id", "created_at"),
        Index("ix_query_logs_status", "status"),
    )


class AnswerRecord(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    query_id: Mapped[str] = mapped_column(
        ForeignKey("query_logs.id", ondelete="CASCADE"), nullable=False
    )
    answer: Mapped[dict] = mapped_column(JSONB, nullable=False)
    abstained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_answers_query", "query_id"),)


class AnswerCitation(Base):
    __tablename__ = "answer_citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    answer_id: Mapped[str] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(String(288), nullable=False)
    citation_label: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_answer_citations_chunk", "chunk_id"),)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[str] = mapped_column(
        ForeignKey("query_logs.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 = helpful, -1 = not helpful
    comment: Mapped[str | None] = mapped_column(Text)
    client_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageDaily(Base):
    """Per-day counters per client key (`cid:<uuid>` or `ip:<host>`): quota and cost (M7)."""

    __tablename__ = "usage_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    client_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    prompt_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    thinking_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    abstained: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AlertEvent(Base):
    """Fired alert (M15): deduplicated by `dedup_key` while unresolved / inside the cooldown."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)  # info|warn|crit
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(Text)
    notified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    notify_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_alert_events_dedup", "dedup_key", "fired_at"),)


class AuditEvent(Base):
    """Admin actions (PLAN.md §11): index activation, review decisions, bulk approvals."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    target: Mapped[str] = mapped_column(String(288), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_events_created", "created_at"),)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
