"""Phase 1 gate for the PDF documents. These tests need the docling cache under
data/derived/<doc>/pdf_extract (run `python backend/scripts/ingest.py pdf --doc TT_45_2024`);
they are skipped, not failed, when it is absent so CI without docling models stays green."""

import pytest

from app.ingestion.pdf_extract import cached_batches_present
from app.ingestion.pipeline import derived_dir, ingest_document
from app.ontology.loader import load_dictionaries
from app.registry.loader import load_registry


def _needs_cache(settings, doc_id):
    reg = load_registry(settings=settings)
    doc = reg.by_id(doc_id)
    cache = derived_dir(doc_id, settings) / "pdf_extract"
    if not cached_batches_present(doc.source.sha256, cache, doc.source.page_count or 0):
        pytest.skip(f"docling cache missing for {doc_id}")
    return reg, doc


def test_tt45_structure_relations_and_chunks(settings):
    reg, doc = _needs_cache(settings, "TT_45_2024")
    art = ingest_document(doc, load_dictionaries(settings=settings), reg, settings)
    st = art.structure
    assert st.coverage.uncovered == []
    counts = st.kind_counts()
    assert counts["Article"] == 5 and counts["Clause"] == 11 and counts["Point"] == 7
    assert counts.get("Paragraph", 0) == 0, (
        "page-break fragments must merge into their clause/point"
    )
    # every content node knows its PDF page
    for n in st.nodes:
        if n.kind in ("Article", "Clause", "Point"):
            assert n.span.pdf_page_start is not None, n.node_id
    labels = {n.citation_label for n in st.nodes}
    assert "Thông tư 45/2024/TT-BTNMT, Điều 2, Khoản 4, tr. 2" in labels
    assert "Thông tư 45/2024/TT-BTNMT, Điều 2, Khoản 2, điểm a, tr. 1" in labels

    rel = art.relations
    full = [r for r in rel.relations if r.type == "REPEALS" and r.scope == "full"]
    partial = [r for r in rel.relations if r.type == "REPEALS" and r.scope == "partial"]
    assert len(full) == 7 and len(partial) == 5, (len(full), len(partial))
    assert rel.effective_date_from_text is None, "the file leaves the effective date blank"
    assert rel.mismatches == [], rel.mismatches
    kinds = [t.kind for t in rel.transition_rules]
    assert (
        kinds.count("transition") == 2 and kinds.count("roadmap") == 3 and "default_zone" in kinds
    )

    facts = [c for c in art.chunks if c.chunk_type == "registry_fact"]
    assert {c.entities["fact_type"] for c in facts} == {
        "document_number",
        "issue_date",
        "signing_place",
        "effective_date",
    }
    assert all(c.text_status == "official" for c in art.chunks), "PO confirmation 2026-09-24"
    assert any("01/7/2025" in c.citation_label or "2025-07-01" in c.citation_label for c in facts)
