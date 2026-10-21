"""Pydantic schema for the canonical dictionaries under data/dictionaries/ (Data dictionary v1).

Every record carries `sources`: citation labels of where the canonical name/definition comes
from, so the ontology stays traceable to the corpus (PLAN.md §12 decision 4).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DomainCode = str  # a code of domains.yaml, or "both" for records shared by every domain


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list, description="citation labels")
    note: str | None = None


class EnvironmentalDomain(_Record):
    """A regulated domain. Adding one here (with its routing vocabulary) is all the code needs."""

    code: str = Field(pattern=r"^[a-z_]+$")
    qcvn_document_id: str
    routing_keywords: list[str] = Field(
        default_factory=list, description="a question using one of these leans to the domain"
    )
    unit_hints: list[str] = Field(
        default_factory=list, description="unit ids that lean a question to the domain"
    )
    parameter_phase_required: bool = Field(
        default=False, description="parameters of this domain must state their phase"
    )


class Unit(_Record):
    unit_id: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    symbol: str
    latex: str | None = None
    kind: Literal[
        "concentration",
        "temperature",
        "ph",
        "count",
        "color",
        "toxicity",
        "opacity",
        "flow",
        "power",
        "heat",
        "mass_rate",
        "dimensionless",
    ]


class Zone(_Record):
    """Cột A/B/C — meaning differs per domain (PLAN.md §1.4)."""

    domain: str
    code: str = Field(pattern=r"^[A-Z0-9]+$")
    definition: str
    scheme: str | None = Field(
        default=None,
        description="document whose column scheme defines the code (limit_rules.zone_scheme); "
        "two regulations of one domain may both have a 'Cột A' that means different things",
    )

    @property
    def scheme_id(self) -> str:
        return self.scheme or self.domain


class ReceivingBodyType(_Record):
    code: str = Field(pattern=r"^[a-z_]+$")


class FuelType(_Record):
    code: str = Field(pattern=r"^[a-z_]+$")


class ParameterGroup(_Record):
    group_id: str = Field(pattern=r"^[a-z_]+$")


class Parameter(_Record):
    parameter_id: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    symbol: str | None = None
    formula: str | None = Field(default=None, description="chemical formula, plain text")
    domains: list[DomainCode] = Field(min_length=1)
    group_id: str | None = None
    phase: Literal["gas", "particulate", "gas_and_particulate"] | None = Field(
        default=None, description="thể khí / thể hạt, for domains that require a phase"
    )
    default_unit_id: str | None = None


class IndustrySector(_Record):
    """Loại hình sản xuất, kinh doanh, dịch vụ — QCVN 40:2025 Phụ lục 2."""

    sector_id: str = Field(pattern=r"^[A-Z0-9_]+$")
    tt: str = Field(description="TT in Phụ lục 2 (1..54)")
    vsic_codes: list[str] = Field(description="Mã ngành kinh tế theo QĐ 27/2018 (cột 3)")
    group_roman: str = Field(description="I..X")
    group_letter: str | None = Field(default=None, description="A, B, C, D, E, G, M, Q, R")
    characteristic_parameters_raw: str = Field(description="cột 4, nguyên văn")
    other_parameters_raw: str = Field(description="cột 5, nguyên văn")
    characteristic_parameter_ids: list[str] = Field(
        default_factory=list, description="resolved later by resolve_entities; empty until then"
    )


class EquipmentType(_Record):
    """Thiết bị xả khí thải công nghiệp — QCVN 19:2024 Bảng 1/2 cột 4, path phân cấp."""

    equipment_id: str = Field(pattern=r"^[a-z0-9_.]+$")
    label_raw: str
    path: list[str] = Field(
        description="numbering path inside the parameter block, e.g. ['1','1.2','1.2.1']"
    )
    parent_id: str | None = None
    fuel_type: str | None = None
    capacity_condition_raw: str | None = None
    is_group_header: bool = False
    occurrences: list[str] = Field(
        default_factory=list, description="table row citation labels where this label appears"
    )


class AliasRule(BaseModel):
    """Query-side alias expansion (not entity aliases): 'cột B' -> zone B, 'bụi' -> PM."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(description="lower-cased, diacritics kept; matched as a token sequence")
    entity_type: Literal[
        "parameter", "zone", "unit", "sector", "equipment", "domain", "receiving_body", "fuel"
    ]
    entity_id: str
    note: str | None = None


class ConditionDimension(BaseModel):
    """One dimension a limit value may depend on (zone, flow regime, fuel, averaging period …)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z_]+$")
    label: str
    value_kind: Literal["code", "number", "path", "text", "flag"]
    scoped_by: Literal["document"] | None = Field(
        default=None, description="the codes only have a meaning inside one document"
    )
    linked_entity: str | None = Field(
        default=None, description="dictionary node the condition points to in the graph"
    )
    slot: str | None = Field(
        default=None, description="slot of a question that states this dimension; None = same name"
    )
    ask_order: int = Field(
        default=99, description="priority when the user must be asked for the dimension"
    )
    note: str | None = None


class IntentSpec(BaseModel):
    """One intent a question may carry (intents.yaml); the file order is the priority order."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z_]+$")
    name: str
    keywords: list[str] = Field(default_factory=list)
    main_with_slot: str | None = Field(
        default=None, description="main intent when the question also has a slot of this dimension"
    )
    always_main: bool = False
    secondary_only: bool = False


class ZoneDescription(BaseModel):
    """A way users describe a zone without naming its column (query understanding v2 only)."""

    model_config = ConfigDict(extra="forbid")

    pattern: str
    domain: str
    code: str


class HopSpec(BaseModel):
    """One kind of second basis a multi-hop question needs (hops.yaml)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern=r"^[a-z_]+$")
    lookup: str
    effect: Literal["fill_slot", "switch_document", "explain"]
    role: str
    dimension: str | None = None
    cues: list[str] = Field(default_factory=list)
    enabled: bool = True


class Dictionaries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domains: list[EnvironmentalDomain]
    units: list[Unit]
    zones: list[Zone]
    receiving_body_types: list[ReceivingBodyType]
    fuel_types: list[FuelType]
    parameter_groups: list[ParameterGroup]
    parameters: list[Parameter]
    industry_sectors: list[IndustrySector]
    equipment_types: list[EquipmentType]
    aliases: list[AliasRule]
    condition_dimensions: list[ConditionDimension] = Field(default_factory=list)
    intents: list[IntentSpec] = Field(default_factory=list)
    hops: list[HopSpec] = Field(default_factory=list)
    # routing.yaml: vocabulary that is not tied to one domain
    out_of_scope_keywords: list[str] = Field(default_factory=list)
    equipment_cue_words: list[str] = Field(default_factory=list)
    equipment_label_prefixes: list[str] = Field(default_factory=list)
    negation_cues: list[str] = Field(default_factory=list)
    split_cues: list[str] = Field(default_factory=list)
    zone_descriptions: list[ZoneDescription] = Field(default_factory=list)
