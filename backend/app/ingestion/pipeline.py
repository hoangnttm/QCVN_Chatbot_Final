"""Orchestrates the per-document ingestion stages and writes artefacts to data/derived/."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.ingestion.chunk import (
    chunks_from_registry_facts,
    chunks_from_structure,
    chunks_from_tables,
)
from app.ingestion.extract_monitoring import extract_monitoring, load_monitoring_map
from app.ingestion.extract_relations import extract_relations
from app.ingestion.extract_rules import extract_limit_rules
from app.ingestion.grammar import load_grammar
from app.ingestion.models import (
    EvidenceChunk,
    ExtractedRelations,
    IngestionReport,
    LimitRule,
    MonitoringRequirement,
    ParsedStructure,
    ParsedTables,
)
from app.ingestion.pdf_extract import cached_batches_present, cached_page_ranges, extract_pdf
from app.ingestion.resolve_entities import EntityResolver
from app.ingestion.sanitize import sanitize
from app.ingestion.scope import apply_scope, load_scope
from app.ingestion.structure_parser import StructureParser
from app.ingestion.table_parser import TableParser
from app.ontology.loader import load_dictionaries
from app.ontology.schema import Dictionaries
from app.registry.loader import load_registry, resolve_source_path
from app.registry.schema import Document, Registry


def derived_dir(document_id: str, settings: Settings) -> Path:
    d = settings.data_dir / "derived" / document_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_structure_for(
    doc: Document,
    settings: Settings | None = None,
    require_pdf_cache: bool = True,
    allow_partial: bool = False,
) -> ParsedStructure:
    settings = settings or get_settings()
    assert doc.source is not None and doc.grammar is not None
    path = resolve_source_path(doc.source.file, settings)
    if path is None:
        raise FileNotFoundError(doc.source.file)
    grammar_path = settings.data_dir / doc.grammar
    grammar = load_grammar(grammar_path)

    page_of_line: list[int] | None = None
    partial_note: str | None = None
    if doc.source.format == "pdf":
        cache_dir = derived_dir(doc.document_id, settings) / "pdf_extract"
        n_pages = doc.source.page_count or 0
        ranges = None
        if not cached_batches_present(doc.source.sha256, cache_dir, n_pages):
            ranges = cached_page_ranges(doc.source.sha256, cache_dir, n_pages)
            if not allow_partial or not ranges:
                if require_pdf_cache:
                    raise NotImplementedError(
                        f"{doc.document_id}: pdf_extract cache missing — "
                        f"run `ingest.py pdf --doc {doc.document_id}` first"
                    )
                ranges = None
            else:
                partial_note = f"PARTIAL: only cached page ranges {ranges} were parsed"
        extracted = extract_pdf(path, doc.source.sha256, cache_dir, n_pages, page_ranges=ranges)
        raw = extracted.text
        page_of_line = extracted.page_of_line
    else:
        raw = path.read_text(encoding="utf-8")

    text = sanitize(raw, doc.source, grammar.preprocess)
    line_range = (
        (1, len(text.lines))
        if doc.source.format == "pdf"
        else (doc.source.line_range or grammar.line_range or (1, len(text.lines)))
    )
    parser = StructureParser(grammar, doc.document_id, doc.source.sha256, str(doc.grammar))
    parsed = parser.parse(text, line_range)
    if text.blanked:
        parsed.warnings.insert(
            0, f"sanitize: blanked noise lines {text.blanked[0]}–{text.blanked[-1]}"
        )
    if partial_note:
        parsed.warnings.insert(0, partial_note)
    if text.lone_bullets:
        parsed.warnings.append(f"sanitize: dropped {text.lone_bullets} empty '-' list items")
    if page_of_line:
        numbering = (grammar.model_extra or {}).get("page_numbering") or {}
        suffix_tpl = grammar.citation.get("page_suffix")
        for n in parsed.nodes:
            pages = [
                page_of_line[i - 1]
                for i in range(n.span.line_start, n.span.line_end + 1)
                if 0 < i <= len(page_of_line) and page_of_line[i - 1]
            ]
            if not pages:
                continue
            n.span.pdf_page_start, n.span.pdf_page_end = min(pages), max(pages)
            n.span.printed_page = _printed_page(n.span.pdf_page_start, numbering)
            if suffix_tpl and n.citation_label and n.kind not in ("Document", "Boilerplate"):
                n.citation_label += ", " + suffix_tpl.format(
                    pdf_page=n.span.pdf_page_start, printed_page=n.span.printed_page
                )
    return parsed


def _printed_page(pdf_page: int, numbering: dict) -> str:
    """Map a PDF page to the printed page label using the grammar's page_numbering sections
    (TT 10/2021 restarts at 1 in the appendices: PDF 53 -> 'PL-1')."""
    for section in numbering.values():
        lo, hi = section.get("pdf_pages", (1, 10**6))
        if lo <= pdf_page <= hi:
            printed = pdf_page - int(section.get("printed_offset", 0))
            return f"{section.get('label_prefix', '')}{printed}"
    return str(pdf_page)


def write_structure(parsed: ParsedStructure, settings: Settings) -> Path:
    out = derived_dir(parsed.document_id, settings) / "structure.json"
    out.write_text(json.dumps(parsed.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def parse_tables_for(
    doc: Document, structure: ParsedStructure, settings: Settings | None = None
) -> ParsedTables:
    settings = settings or get_settings()
    assert doc.grammar is not None
    grammar = load_grammar(settings.data_dir / doc.grammar)
    return TableParser(grammar, structure).parse_all()


def write_tables(parsed: ParsedTables, settings: Settings) -> Path:
    out = derived_dir(parsed.document_id, settings) / "tables.json"
    out.write_text(json.dumps(parsed.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8")
    return out


class DocumentArtifacts(BaseModel):
    structure: ParsedStructure
    tables: ParsedTables
    rules: list[LimitRule]
    relations: ExtractedRelations
    chunks: list[EvidenceChunk]
    monitoring: list[MonitoringRequirement] = []
    report: IngestionReport


def ingest_document(
    doc: Document,
    dictionaries: Dictionaries,
    reg: Registry,
    settings: Settings,
    allow_partial: bool = False,
) -> DocumentArtifacts:
    """Steps 2–9 for one document (PDFs need their docling cache from step 1b first)."""
    structure = parse_structure_for(doc, settings, allow_partial=allow_partial)
    if doc.scope_file:
        counts = apply_scope(structure, load_scope(settings.data_dir / doc.scope_file))
        structure.warnings.append(f"scope: {counts}")
    tables = parse_tables_for(doc, structure, settings)
    unresolved = EntityResolver(dictionaries).resolve_tables(tables)
    domain = doc.domain
    rules = extract_limit_rules(tables, domain, structure.by_id())
    relations = extract_relations(structure, doc, reg)
    chunks = (
        chunks_from_structure(structure, doc, domain)
        + chunks_from_tables(tables, structure, doc, domain)
        + chunks_from_registry_facts(doc, domain)
    )
    monitoring: list[MonitoringRequirement] = []
    monitoring_warnings: list[str] = []
    grammar = load_grammar(settings.data_dir / doc.grammar)
    monitoring_file = (grammar.model_extra or {}).get("monitoring_file")
    if monitoring_file:
        monitoring, monitoring_warnings = extract_monitoring(
            structure, load_monitoring_map(settings.data_dir / monitoring_file), dictionaries
        )
        chunk_ids = {c.chunk_id for c in chunks}
        monitoring_warnings += [
            f"monitoring {q.requirement_id}: no evidence chunk {q.evidence_chunk_id}"
            for q in monitoring
            if q.evidence_chunk_id not in chunk_ids
        ]
    report = IngestionReport(
        document_id=doc.document_id,
        text_status=doc.text_status.value,
        coverage_ratio=structure.coverage.ratio,
        node_counts=structure.kind_counts(),
        table_row_counts={t.table_code: len(t.data_rows()) for t in tables.tables},
        rules=len(rules),
        chunks=len(chunks),
        relations=len(relations.relations),
        transition_rules=len(relations.transition_rules),
        monitoring_requirements=len(monitoring),
        unresolved_entities=unresolved,
        warnings=structure.warnings
        + tables.warnings
        + [
            f"{t.table_code} {r.row_id}: {w}"
            for t in tables.tables
            for r in t.rows
            for w in r.parse_warnings
        ]
        + [f"relations: {m}" for m in relations.mismatches]
        + monitoring_warnings,
    )
    return DocumentArtifacts(
        structure=structure,
        tables=tables,
        rules=rules,
        relations=relations,
        chunks=chunks,
        monitoring=monitoring,
        report=report,
    )


def _dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def write_artifacts(a: DocumentArtifacts, settings: Settings) -> Path:
    d = derived_dir(a.report.document_id, settings)
    write_structure(a.structure, settings)
    write_tables(a.tables, settings)
    _dump(d / "rules.json", [r.model_dump() for r in a.rules])
    _dump(d / "relations.json", a.relations.model_dump())
    _dump(d / "chunks.json", [c.model_dump() for c in a.chunks])
    _dump(d / "monitoring.json", [q.model_dump() for q in a.monitoring])
    _dump(d / "report.json", a.report.model_dump())
    return d


def run_all(
    document_ids: list[str] | None,
    settings: Settings | None = None,
    allow_partial: bool = False,
) -> dict[str, DocumentArtifacts]:
    settings = settings or get_settings()
    reg = load_registry(settings=settings)
    dictionaries = load_dictionaries(settings=settings)
    results: dict[str, DocumentArtifacts] = {}
    for doc in reg.documents:
        if doc.source is None or (document_ids and doc.document_id not in document_ids):
            continue
        try:
            art = ingest_document(doc, dictionaries, reg, settings, allow_partial=allow_partial)
        except NotImplementedError as e:
            print(f"SKIP {e}")
            continue
        write_artifacts(art, settings)
        results[doc.document_id] = art
    return results


def write_quality_report(settings: Settings | None = None) -> Path:
    """Phase 1 deliverable 'Báo cáo parser quality': one Markdown table from data/derived/*/report.json."""
    import datetime as _dt

    settings = settings or get_settings()
    rows: list[IngestionReport] = []
    for rep_file in sorted((settings.data_dir / "derived").glob("*/report.json")):
        rows.append(IngestionReport.model_validate_json(rep_file.read_text(encoding="utf-8")))
    today = _dt.date.today().isoformat()
    lines = [
        f"# Báo cáo chất lượng ingest — {today}",
        "",
        "Sinh tự động bằng `python backend/scripts/ingest.py report` "
        "từ `data/derived/<doc>/report.json`.",
        "Cổng chất lượng Phase 1 (PLAN.md §5): coverage 100 %, 0 ô không parse, "
        "0 entity chưa resolve, 0 lệch registry.",
        "",
        "| Văn bản | Trạng thái text | Coverage | Dòng bảng | LimitRule | Quan hệ | "
        "Chuyển tiếp | Chunk | Chưa resolve | Cảnh báo |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        n_warn = len([w for w in r.warnings if not w.startswith("sanitize")])
        lines.append(
            f"| {r.document_id} | {r.text_status} | {r.coverage_ratio:.1%} | "
            f"{sum(r.table_row_counts.values())} | {r.rules} | {r.relations} | "
            f"{r.transition_rules} | {r.chunks} | {len(r.unresolved_entities)} | {n_warn} |"
        )
    lines += ["", "## Chi tiết bảng", ""]
    for r in rows:
        if r.table_row_counts:
            detail = ", ".join(f"{k} = {v} dòng" for k, v in r.table_row_counts.items())
            lines.append(f"- **{r.document_id}**: {detail}")
    lines += ["", "## Node theo loại", ""]
    for r in rows:
        lines.append(
            f"- **{r.document_id}**: " + ", ".join(f"{k} {v}" for k, v in r.node_counts.items())
        )
    issues = [(r.document_id, u) for r in rows for u in r.unresolved_entities] + [
        (r.document_id, w) for r in rows for w in r.warnings if not w.startswith("sanitize")
    ]
    lines += ["", "## Vấn đề cần review", ""]
    lines += [f"- {d}: {i}" for d, i in issues] or ["- (không có)"]
    out = settings.data_dir.parent / "docs" / "reports" / f"ingestion-{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def run_pdf_extract(
    document_ids: list[str] | None, settings: Settings | None = None
) -> dict[str, dict]:
    """Slow step: convert PDFs with docling (cached per batch). Safe to re-run."""
    settings = settings or get_settings()
    reg = load_registry(settings=settings)
    out: dict[str, dict] = {}
    for doc in reg.documents:
        if (
            doc.source is None
            or doc.source.format != "pdf"
            or (document_ids and doc.document_id not in document_ids)
        ):
            continue
        path = resolve_source_path(doc.source.file, settings)
        assert path is not None, doc.source.file
        cache_dir = derived_dir(doc.document_id, settings) / "pdf_extract"
        ex = extract_pdf(path, doc.source.sha256, cache_dir, doc.source.page_count or 0)
        out[doc.document_id] = {
            "lines": len(ex.lines),
            "tables": ex.n_tables,
            "failed_pages": ex.failed_pages,
            "cache": ex.cache_path,
        }
    return out


def run_tables(
    document_ids: list[str] | None, settings: Settings | None = None
) -> dict[str, ParsedTables]:
    settings = settings or get_settings()
    reg = load_registry(settings=settings)
    results: dict[str, ParsedTables] = {}
    for doc in reg.documents:
        if doc.source is None or (document_ids and doc.document_id not in document_ids):
            continue
        try:
            structure = parse_structure_for(doc, settings)
        except NotImplementedError as e:
            print(f"SKIP {e}")
            continue
        write_structure(structure, settings)
        tables = parse_tables_for(doc, structure, settings)
        write_tables(tables, settings)
        results[doc.document_id] = tables
    return results


def run_structure(
    document_ids: list[str] | None,
    settings: Settings | None = None,
    allow_partial: bool = False,
) -> dict[str, ParsedStructure]:
    settings = settings or get_settings()
    reg = load_registry(settings=settings)
    docs = [
        d
        for d in reg.documents
        if d.source is not None and (not document_ids or d.document_id in document_ids)
    ]
    results: dict[str, ParsedStructure] = {}
    for doc in docs:
        try:
            parsed = parse_structure_for(doc, settings, allow_partial=allow_partial)
            if doc.scope_file:
                apply_scope(parsed, load_scope(settings.data_dir / doc.scope_file))
        except NotImplementedError as e:
            print(f"SKIP {e}")
            continue
        write_structure(parsed, settings)
        results[doc.document_id] = parsed
    return results
