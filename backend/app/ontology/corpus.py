"""What the code needs to know about the corpus, read from data (plan 2026-10-06, G1).

No module outside the dictionaries / registry names a domain, a document or a table: they ask a
`Corpus`, built from `data/dictionaries/` (domains, zones, routing keywords) and from the loaded
database (documents, threshold tables, pointer clauses). Adding a document or a domain is then a
change of data, not of code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as m
from app.ontology.loader import load_dictionaries
from app.ontology.schema import Dictionaries

# a document / chunk / parameter that belongs to every regulated domain
SHARED = "both"
REGULATION_TYPE = "qcvn"
STUB_TYPE = "external_stub"
FREQUENCY_POINTER = "frequency_pointer"


@dataclass(frozen=True)
class DomainSpec:
    code: str
    name: str
    keywords: tuple[str, ...] = ()  # routing: a question using one of these leans to the domain
    unit_hints: tuple[str, ...] = ()  # unit ids that lean to the domain (mg/L, mg/Nm³ …)


@dataclass(frozen=True)
class DocumentSpec:
    document_id: str
    document_code: str
    document_type: str
    domain: str


@dataclass
class Corpus:
    domains: dict[str, DomainSpec] = field(default_factory=dict)  # regulated domains, in order
    out_of_scope_keywords: tuple[str, ...] = ()
    equipment_cue_words: tuple[str, ...] = ()  # words that tie a question to an equipment label
    zone_codes: dict[str, tuple[str, ...]] = field(default_factory=dict)  # domain -> zone codes
    # (domain, code) -> column schemes that define the code (graph node zone:{scheme}:{code})
    zone_schemes: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    documents: dict[str, DocumentSpec] = field(default_factory=dict)  # corpus documents (no stubs)
    limit_tables: dict[str, tuple[str, ...]] = field(default_factory=dict)  # domain -> table codes
    frequency_pointers: tuple[
        str, ...
    ] = ()  # chunks that point to the out-of-corpus frequency rules

    # ------------------------------------------------------------------ domains

    def domain_codes(self) -> list[str]:
        return list(self.domains)

    def is_regulated(self, domain: str | None) -> bool:
        return domain in self.domains

    def search_domains(self, domain: str | None) -> set[str] | None:
        """Chunk domains a question of `domain` may use; None = no restriction (cross-domain,
        unknown, out of scope)."""
        return {domain, SHARED} if domain in self.domains else None  # type: ignore[arg-type]

    def other_domains(self, domain: str) -> set[str]:
        return {d for d in self.domains if d != domain}

    def candidates(self, domain: str | None) -> list[str]:
        """The domain itself when it is a regulated one, else every regulated domain."""
        return [domain] if domain in self.domains else list(self.domains)  # type: ignore[list-item]

    def members(self, domain: str | None) -> list[str]:
        """Regulated domains a record of `domain` belongs to (`both` → all of them)."""
        if domain == SHARED:
            return list(self.domains)
        return [domain] if domain in self.domains else []  # type: ignore[list-item]

    def zones(self, domain: str | None = None) -> tuple[str, ...]:
        """Zone codes of one domain, or of all domains (deduplicated, in dictionary order)."""
        if domain is not None:
            return self.zone_codes.get(domain, ())
        return tuple(dict.fromkeys(z for zs in self.zone_codes.values() for z in zs))

    def zone_nodes(self, domains: list[str], code: str) -> list[str]:
        """Graph nodes of a zone code in the given domains, one per column scheme."""
        return [
            f"zone:{scheme}:{code}"
            for dom in domains
            for scheme in self.zone_schemes.get((dom, code), ())
        ]

    # ------------------------------------------------------------------ documents and tables

    def documents_of(self, domain: str) -> list[str]:
        """Corpus documents whose own domain is `domain` (shared documents are not included)."""
        return [d.document_id for d in self.documents.values() if d.domain == domain]

    def regulations(self, domain: str | None = None) -> list[str]:
        """The technical regulations (QCVN) in the corpus, optionally of one domain."""
        return [
            d.document_id
            for d in self.documents.values()
            if d.document_type == REGULATION_TYPE and (domain is None or d.domain == domain)
        ]

    def document_domains(self) -> dict[str, str]:
        """Corpus documents that belong to exactly one regulated domain."""
        return {
            d.document_id: d.domain for d in self.documents.values() if d.domain in self.domains
        }

    def threshold_tables(self, domain: str | None = None) -> list[str]:
        """Tables that carry limit rules, of one regulated domain or of all of them."""
        if domain in self.domains:
            return list(self.limit_tables.get(domain, ()))  # type: ignore[arg-type]
        return [t for d in self.domains for t in self.limit_tables.get(d, ())]

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_dictionaries(cls, d: Dictionaries) -> Corpus:
        zone_codes: dict[str, list[str]] = {}
        zone_schemes: dict[tuple[str, str], list[str]] = {}
        for z in d.zones:
            if z.code not in zone_codes.setdefault(z.domain, []):
                zone_codes[z.domain].append(z.code)
            zone_schemes.setdefault((z.domain, z.code), []).append(z.scheme_id)
        return cls(
            domains={
                x.code: DomainSpec(
                    code=x.code,
                    name=x.name,
                    keywords=tuple(x.routing_keywords),
                    unit_hints=tuple(x.unit_hints),
                )
                for x in d.domains
            },
            out_of_scope_keywords=tuple(d.out_of_scope_keywords),
            equipment_cue_words=tuple(d.equipment_cue_words),
            zone_codes={k: tuple(v) for k, v in zone_codes.items()},
            zone_schemes={k: tuple(v) for k, v in zone_schemes.items()},
        )

    @classmethod
    def load(
        cls, session: Session, settings: Settings | None = None, d: Dictionaries | None = None
    ) -> Corpus:
        corpus = cls.from_dictionaries(d or load_dictionaries(settings=settings))
        for doc in session.execute(select(m.Document).order_by(m.Document.document_id)).scalars():
            if doc.document_type != STUB_TYPE:
                corpus.documents[doc.document_id] = DocumentSpec(
                    doc.document_id, doc.document_code, doc.document_type, doc.domain
                )
        tables: dict[str, list[str]] = {}
        rows = session.execute(
            select(m.LimitRule.domain, m.LimitRule.table_code)
            .distinct()
            .order_by(m.LimitRule.domain, m.LimitRule.table_code)
        )
        for domain, table_code in rows:
            tables.setdefault(domain, []).append(table_code)
        corpus.limit_tables = {k: tuple(v) for k, v in tables.items()}
        corpus.frequency_pointers = tuple(
            session.execute(
                select(m.MonitoringRequirement.evidence_chunk_id)
                .where(m.MonitoringRequirement.requirement_type == FREQUENCY_POINTER)
                .order_by(m.MonitoringRequirement.requirement_id)
            ).scalars()
        )
        return corpus


# ---------------------------------------------------------------- process-wide instance
_current: Corpus | None = None
_loaded = False


def set_current(corpus: Corpus, *, loaded: bool = True) -> Corpus:
    global _current, _loaded
    _current, _loaded = corpus, loaded
    return corpus


def current(session: Session | None = None) -> Corpus:
    """The corpus of this process. Without a database it knows the dictionaries only (domains,
    zones, routing keywords); given a `session` it is completed from the database once.
    `RetrievalService.ensure` refreshes it whenever the indexes are reloaded."""
    global _current
    if session is not None and not _loaded:
        return set_current(Corpus.load(session))
    if _current is None:
        _current = Corpus.from_dictionaries(load_dictionaries())
    return _current
