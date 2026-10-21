"""Phase 0 acceptance: the Document Registry loads, hashes match sources/, provenance rules hold."""

from app.registry.loader import load_registry, verify_referenced_files, verify_sources
from app.registry.schema import FactSource, TextStatus


def test_registry_loads_and_sources_match(settings):
    reg = load_registry(settings=settings)
    assert reg.version == 1
    assert verify_sources(reg, settings) == []
    assert verify_referenced_files(reg, settings) == []


def test_expected_documents_present(settings):
    reg = load_registry(settings=settings)
    ids = {d.document_id for d in reg.documents}
    assert {"TT_06_2025", "QCVN_40_2025", "QCVN_19_2024", "TT_45_2024", "TT_10_2021"} <= ids


def test_all_texts_official_and_admin_declared_facts_audited(settings):
    """PO confirmed on 2026-09-24 that TT 45/2024, TT 10/2021 and QCVN 19:2024 are the official
    texts checked against the originals; their files still leave number/dates blank, so those
    values remain admin-declared registry facts with an audit trail."""
    reg = load_registry(settings=settings)
    ingested = [d for d in reg.documents if d.source is not None]
    assert {d.text_status for d in ingested} == {TextStatus.official}
    for doc in reg.documents:
        for f in doc.facts:
            if f.source == FactSource.admin_declared:
                assert f.declared_by and f.declared_at, (doc.document_id, f.fact_type)
            if doc.text_status == TextStatus.draft and f.fact_type.endswith("_date"):
                assert f.scope or f.source == FactSource.admin_declared
    for did in ("TT_45_2024", "TT_10_2021"):
        d = reg.by_id(did)
        assert d.fact("document_number").source == FactSource.admin_declared
        assert d.fact("effective_date").source == FactSource.admin_declared


def test_po_declared_dates(settings):
    reg = load_registry(settings=settings)
    tt45 = reg.by_id("TT_45_2024")
    assert tt45.fact("issue_date").value == "2024-12-30"
    assert tt45.fact("effective_date").value == "2025-07-01"
    tt10 = reg.by_id("TT_10_2021")
    assert tt10.fact("issue_date").value == "2021-06-30"
    assert tt10.fact("effective_date").value == "2021-08-16"
    tt06 = reg.by_id("TT_06_2025")
    assert tt06.fact("effective_date").source == FactSource.from_text
    assert tt06.fact("effective_date").value == "2025-09-01"


def test_promulgation_links(settings):
    reg = load_registry(settings=settings)
    assert any(
        r.type == "PROMULGATES" and r.target == "QCVN_40_2025"
        for r in reg.by_id("TT_06_2025").relations
    )
    assert any(
        r.type == "PROMULGATES" and r.target == "QCVN_19_2024"
        for r in reg.by_id("TT_45_2024").relations
    )
    q19 = reg.by_id("QCVN_19_2024")
    partial = [r for r in q19.relations if r.type == "REPLACES" and r.scope == "partial"]
    assert len(partial) == 5
