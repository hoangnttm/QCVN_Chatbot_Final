"""Load ingestion artefacts (data/derived/<doc>/*.json) into PostgreSQL — idempotent per document.

Reloading a document replaces all of its rows inside one transaction. Embeddings already computed
for chunks whose `context_text` did not change are carried over, so re-running ingest does not
re-spend Gemini calls (ADR-0005).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db import facts as fact_layer
from app.db import models as m
from app.ingestion.models import (
    EvidenceChunk,
    ExtractedRelations,
    IngestionReport,
    LimitRule,
    MonitoringRequirement,
    ParsedStructure,
    ParsedTables,
)
from app.ontology.loader import load_dictionaries
from app.registry.schema import Document, Registry


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ctx_hash(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _span(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_hash": d["file_hash"],
        "line_start": d["line_start"],
        "line_end": d["line_end"],
        "pdf_page_start": d.get("pdf_page_start"),
        "pdf_page_end": d.get("pdf_page_end"),
        "printed_page": d.get("printed_page"),
    }


# ----------------------------------------------------------------------------- registry


def ensure_registry_documents(session: Session, reg: Registry) -> int:
    """Upsert every registry document (incl. external stubs) so relation targets resolve."""
    rows = []
    for d in reg.documents + reg.external_stubs:
        rows.append(
            {
                "document_id": d.document_id,
                "document_code": d.document_code,
                "document_type": d.document_type.value,
                "title": d.title,
                "issuer": d.issuer,
                "domain": d.domain,
                "text_status": d.text_status.value,
                "ingest_scope": d.ingest_scope,
                "grammar": d.grammar,
                "note": d.note,
            }
        )
    stmt = pg_insert(m.Document).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[m.Document.document_id],
        set_={c: getattr(stmt.excluded, c) for c in rows[0] if c != "document_id"},
    )
    session.execute(stmt)
    return len(rows)


def _load_registry_side(session: Session, doc: Document) -> None:
    session.execute(delete(m.RegistryFact).where(m.RegistryFact.document_id == doc.document_id))
    session.execute(
        delete(m.DocumentRelation).where(m.DocumentRelation.source_document_id == doc.document_id)
    )
    if doc.facts:
        session.execute(
            insert(m.RegistryFact),
            [
                {
                    "document_id": doc.document_id,
                    "fact_type": f.fact_type,
                    "value": f.value,
                    "source": f.source.value,
                    "evidence": f.evidence,
                    "scope": f.scope,
                    "declared_by": f.declared_by,
                    "declared_at": datetime.combine(f.declared_at, datetime.min.time())
                    if f.declared_at
                    else None,
                    "note": f.note,
                }
                for f in doc.facts
            ],
        )
    if doc.relations:
        session.execute(
            insert(m.DocumentRelation),
            [
                {
                    "source_document_id": doc.document_id,
                    "type": r.type,
                    "target_document_id": r.target,
                    "target_code": None,
                    "scope": r.scope,
                    "target_part": r.target_part,
                    "condition": r.condition,
                    "evidence": r.evidence,
                    "origin": "registry",
                }
                for r in doc.relations
            ],
        )
    if doc.source is not None:
        stmt = pg_insert(m.DocumentVersion).values(
            document_id=doc.document_id,
            file_name=doc.source.file,
            format=doc.source.format,
            sha256=doc.source.sha256,
            size_bytes=doc.source.size_bytes,
            page_count=doc.source.page_count,
            line_start=doc.source.line_range[0] if doc.source.line_range else None,
            line_end=doc.source.line_range[1] if doc.source.line_range else None,
            is_current=True,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[m.DocumentVersion.document_id, m.DocumentVersion.sha256],
            set_={"is_current": True, "file_name": doc.source.file},
        )
        session.execute(stmt)
        session.execute(
            update(m.DocumentVersion)
            .where(
                m.DocumentVersion.document_id == doc.document_id,
                m.DocumentVersion.sha256 != doc.source.sha256,
            )
            .values(is_current=False)
        )


# ----------------------------------------------------------------------------- artefacts


def load_document(
    doc_id: str, reg: Registry, settings: Settings, session: Session
) -> dict[str, int]:
    """Replace every artefact row of `doc_id` with the JSON under data/derived/<doc_id>/."""
    doc = reg.by_id(doc_id)
    base = settings.data_dir / "derived" / doc_id
    structure = ParsedStructure.model_validate(_read(base / "structure.json"))
    tables = ParsedTables.model_validate(_read(base / "tables.json"))
    rules = [LimitRule.model_validate(r) for r in _read(base / "rules.json")]
    relations = ExtractedRelations.model_validate(_read(base / "relations.json"))
    chunks = [EvidenceChunk.model_validate(c) for c in _read(base / "chunks.json")]
    monitoring = (
        [MonitoringRequirement.model_validate(q) for q in _read(base / "monitoring.json")]
        if (base / "monitoring.json").exists()
        else []
    )
    report = IngestionReport.model_validate(_read(base / "report.json"))

    ensure_registry_documents(session, reg)
    _load_registry_side(session, doc)

    # keep embeddings of unchanged chunks (chunk_id + context_text hash)
    kept = {
        row.chunk_id: (row.h, row.embedding, row.embedding_model, row.embedded_at)
        for row in session.execute(
            select(
                m.EvidenceChunk.chunk_id,
                func.md5(m.EvidenceChunk.context_text).label("h"),
                m.EvidenceChunk.embedding,
                m.EvidenceChunk.embedding_model,
                m.EvidenceChunk.embedded_at,
            ).where(m.EvidenceChunk.document_id == doc_id, m.EvidenceChunk.embedding.is_not(None))
        )
    }

    for model in (
        m.Fact,
        m.RuleCondition,
        m.EvidenceChunk,
        m.MonitoringRequirement,
        m.LimitRule,
        m.TransitionRule,
        m.TableRow,
        m.TableDef,
        m.StructureNode,
    ):
        session.execute(delete(model).where(model.document_id == doc_id))

    counts: dict[str, int] = {}

    node_rows = [
        {
            "node_id": n.node_id,
            "document_id": doc_id,
            "kind": n.kind,
            "order": n.order,
            "depth": n.depth,
            "parent_id": n.parent_id,
            "number": n.number,
            "label": n.label,
            "title": n.title,
            "term": n.term,
            "text": n.text,
            "raw": n.raw,
            "citation_label": n.citation_label,
            "scope": n.attrs.get("scope", "in_scope"),
            "domain": n.attrs.get("domain"),
            "attrs": n.attrs,
            **_span(n.span.model_dump()),
        }
        for n in structure.nodes
    ]
    _bulk(session, m.StructureNode, node_rows)
    counts["structure_nodes"] = len(node_rows)

    table_rows: list[dict[str, Any]] = []
    row_rows: list[dict[str, Any]] = []
    for t in tables.tables:
        table_rows.append(
            {
                "table_code": t.table_code,
                "document_id": doc_id,
                "node_id": t.node_id,
                "title": t.title,
                "columns": t.columns,
                "header_rows": t.header_rows,
                "config": t.config,
                "warnings": t.warnings,
            }
        )
        for r in t.rows:
            d = r.model_dump()
            span = d.pop("span")
            row_rows.append(
                {
                    **{k: v for k, v in d.items() if k in _ROW_COLUMNS},
                    "document_id": doc_id,
                    **_span(span),
                }
            )
    _bulk(session, m.TableDef, table_rows)
    _bulk(session, m.TableRow, row_rows)
    counts["tables"], counts["table_rows"] = len(table_rows), len(row_rows)

    # fact layer (plan G2): validity of the document, cells confirmed by an independent reader
    validity = fact_layer.document_validity(doc_id, reg)
    verified = read_verified(base)
    rule_rows = []
    for r in rules:
        d = r.model_dump()
        span = d.pop("span")
        rule_rows.append(
            {
                **{k: v for k, v in d.items() if k in _RULE_COLUMNS},
                **_span(span),
                "trust": fact_layer.trust_of(r.review_status, r.rule_code in verified),
                "valid_from": validity["valid_from"],
                "valid_to": validity["valid_to"],
            }
        )
    _bulk(session, m.LimitRule, rule_rows)
    counts["limit_rules"] = len(rule_rows)
    condition_rows = fact_layer.rule_condition_rows(rules, load_dictionaries(settings=settings))
    _bulk(session, m.RuleCondition, condition_rows)
    counts["rule_conditions"] = len(condition_rows)

    tr_rows = []
    for t in relations.transition_rules:
        d = t.model_dump()
        span = d.pop("span")
        tr_rows.append({**d, **_span(span)})
    _bulk(session, m.TransitionRule, tr_rows)
    counts["transition_rules"] = len(tr_rows)

    mr_rows = []
    for q in monitoring:
        d = q.model_dump()
        span = d.pop("span")
        mr_rows.append({**d, **_span(span)})
    _bulk(session, m.MonitoringRequirement, mr_rows)
    counts["monitoring_requirements"] = len(mr_rows)

    if relations.relations:
        session.execute(
            insert(m.DocumentRelation),
            [
                {
                    "source_document_id": doc_id,
                    "type": r.type,
                    "target_document_id": r.target_document_id,
                    "target_code": r.target_code,
                    "scope": r.scope,
                    "target_part": r.target_part,
                    "condition": r.condition,
                    "evidence": None,
                    "origin": "text",
                    "source_node_id": r.source_node_id,
                    "citation_label": r.citation_label,
                }
                for r in relations.relations
            ],
        )
    counts["text_relations"] = len(relations.relations)

    chunk_rows = []
    reused = 0
    for c in chunks:
        d = c.model_dump()
        span = d.pop("span")
        row = {**d, **_span(span), "embedding": None, "embedding_model": None, "embedded_at": None}
        prev = kept.get(c.chunk_id)
        if prev and prev[0] == _ctx_hash(c.context_text):
            row["embedding"], row["embedding_model"], row["embedded_at"] = prev[1], prev[2], prev[3]
            reused += 1
        chunk_rows.append(row)
    _bulk(session, m.EvidenceChunk, chunk_rows)
    counts["evidence_chunks"], counts["embeddings_reused"] = len(chunk_rows), reused

    facts = fact_layer.fact_rows(
        doc, structure, tables, rules, relations, monitoring, chunks, validity, verified
    )
    _bulk(session, m.Fact, facts)
    counts["facts"] = len(facts)

    session.execute(
        insert(m.IngestionRun).values(
            document_id=doc_id,
            file_hash=structure.file_hash,
            report=report.model_dump(),
        )
    )
    return counts


VERIFICATION_FILE = "verification.json"


def read_verified(base: Path) -> set[str]:
    """Rule codes an independent reader of the source agreed with (`ingest.py verify`)."""
    path = base / VERIFICATION_FILE
    if not path.exists():
        return set()
    return set(_read(path).get("verified", []))


_ROW_COLUMNS = {c.name for c in m.TableRow.__table__.columns}
_RULE_COLUMNS = {c.name for c in m.LimitRule.__table__.columns}


def _bulk(session: Session, model: type, rows: list[dict[str, Any]], batch: int = 500) -> None:
    for i in range(0, len(rows), batch):
        session.execute(insert(model), rows[i : i + batch])


def db_stats(session: Session) -> dict[str, int]:
    out: dict[str, int] = {}
    for model in (
        m.Document,
        m.DocumentVersion,
        m.RegistryFact,
        m.DocumentRelation,
        m.StructureNode,
        m.TableDef,
        m.TableRow,
        m.LimitRule,
        m.TransitionRule,
        m.MonitoringRequirement,
        m.EvidenceChunk,
        m.IngestionRun,
    ):
        out[model.__tablename__] = session.execute(
            select(func.count()).select_from(model.__table__)
        ).scalar_one()
    out["evidence_chunks_embedded"] = session.execute(
        select(func.count())
        .select_from(m.EvidenceChunk.__table__)
        .where(m.EvidenceChunk.embedding.is_not(None))
    ).scalar_one()
    return out


def load_documents(
    doc_ids: list[str] | None, settings: Settings | None = None
) -> dict[str, dict[str, int]]:
    from app.db.session import session_scope
    from app.registry.loader import load_registry

    settings = settings or get_settings()
    reg = load_registry(settings=settings)
    results: dict[str, dict[str, int]] = {}
    for d in reg.documents:
        if d.source is None or (doc_ids and d.document_id not in doc_ids):
            continue
        if not (settings.data_dir / "derived" / d.document_id / "report.json").exists():
            print(f"SKIP {d.document_id}: no artefacts — run `ingest.py all --doc {d.document_id}`")
            continue
        with session_scope(settings) as session:
            results[d.document_id] = load_document(d.document_id, reg, settings, session)
    return results
