"""Pydantic schema for data/registry/documents.yaml (Document Registry v1, PLAN.md Phụ lục D).

The registry is the single place where a human declares what a source file *is*: its legal
code, whether the text is a signed original or a draft, and any dates that cannot be cited
from the text itself (registry facts, PLAN.md §4.5).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentType(StrEnum):
    circular = "circular"
    qcvn = "qcvn"
    external_stub = "external_stub"


class TextStatus(StrEnum):
    official = "official"  # file in sources/ is the signed text
    draft = "draft"  # file in sources/ is a draft (blank number/dates)
    unverified = "unverified"  # not yet compared with the signed text


class FactSource(StrEnum):
    from_text = "from_text"  # extracted from the document; has an evidence chunk
    admin_declared = "admin_declared"  # entered by the PO/admin; cited as a registry fact


FactType = Literal[
    "document_number",
    "issue_date",
    "signing_place",
    "effective_date",
    "partial_effective_date",
    "signer",
]

RelationType = Literal[
    "PROMULGATES",
    "PROMULGATED_BY",
    "REPLACES",
    "REPEALS",
    "AMENDS",
    "SUPPLEMENTS",
    "REFERENCES",
]


class SourceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(description="File name inside sources/")
    format: Literal["md", "pdf"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    line_range: tuple[int, int] | None = Field(
        default=None, description="1-based inclusive line range of this document inside the file"
    )
    page_count: int | None = None
    noise_lines: tuple[int, int] | None = Field(
        default=None, description="Converter junk to strip during sanitize (PLAN.md F5)"
    )
    note: str | None = None


class RegistryFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_type: FactType
    value: str
    source: FactSource
    evidence: str | None = Field(
        default=None, description="Where in the text this comes from (from_text only)"
    )
    scope: str | None = Field(default=None, description="e.g. 'Chương III' for partial dates")
    declared_by: str | None = None
    declared_at: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _provenance_rules(self) -> "RegistryFact":
        if self.source == FactSource.admin_declared and not (self.declared_by and self.declared_at):
            raise ValueError(
                f"admin_declared fact {self.fact_type} needs declared_by + declared_at"
            )
        if self.source == FactSource.from_text and not self.evidence:
            raise ValueError(f"from_text fact {self.fact_type} needs an evidence location")
        if self.fact_type.endswith("_date"):
            datetime.strptime(self.value, "%Y-%m-%d")  # raises on bad format
        return self


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RelationType
    target: str = Field(description="document_id of the target (may be an external stub)")
    scope: Literal["full", "partial"] = "full"
    target_part: str | None = Field(
        default=None, description="Table/chapter of the target when scope=partial"
    )
    evidence: str | None = None
    condition: str | None = Field(
        default=None, description="e.g. 'theo điều khoản chuyển tiếp tại Điều 3, Điều 4'"
    )


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(pattern=r"^[A-Z0-9_]+$")
    document_code: str
    document_type: DocumentType
    title: str
    issuer: str | None = None
    domain: str = Field(
        default="other",
        pattern=r"^[a-z_]+$",
        description='a domain of domains.yaml, "both" (every domain) or "other"',
    )
    text_status: TextStatus = TextStatus.unverified
    source: SourceFile | None = None
    grammar: str | None = Field(default=None, description="Path relative to data/")
    ingest_scope: Literal["full", "partial", "none"] = "none"
    scope_file: str | None = Field(
        default=None, description="Partition file for ingest_scope=partial (relative to data/)"
    )
    facts: list[RegistryFact] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _consistency(self) -> "Document":
        if self.document_type != DocumentType.external_stub:
            if self.source is None:
                raise ValueError(f"{self.document_id}: non-stub documents need a source file")
            if self.grammar is None:
                raise ValueError(f"{self.document_id}: non-stub documents need a grammar")
        if self.ingest_scope == "partial" and not self.scope_file:
            raise ValueError(f"{self.document_id}: ingest_scope=partial needs scope_file")
        if self.text_status == TextStatus.draft:
            for f in self.facts:
                if (
                    f.fact_type.endswith("_date")
                    and f.source == FactSource.from_text
                    and not f.scope
                ):
                    raise ValueError(
                        f"{self.document_id}: draft text cannot supply {f.fact_type} from_text "
                        "(PLAN.md §4.5) — declare it as admin_declared or scope it"
                    )
        return self

    def fact(self, fact_type: str) -> RegistryFact | None:
        return next((f for f in self.facts if f.fact_type == fact_type and not f.scope), None)


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    documents: list[Document]
    external_stubs: list[Document] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids_and_targets(self) -> "Registry":
        ids = [d.document_id for d in self.documents + self.external_stubs]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate document_id: {sorted(dupes)}")
        known = set(ids)
        for d in self.documents:
            for r in d.relations:
                if r.target not in known:
                    raise ValueError(
                        f"{d.document_id}: relation target {r.target!r} not in registry"
                    )
        return self

    def by_id(self, document_id: str) -> Document:
        for d in self.documents + self.external_stubs:
            if d.document_id == document_id:
                return d
        raise KeyError(document_id)
