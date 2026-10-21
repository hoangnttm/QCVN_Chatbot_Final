"""Phase 1 gate for TT 10/2021 (docling output, 192 pages). Skipped when the docling cache is absent."""

from collections import Counter

from app.ingestion.pipeline import ingest_document
from app.ontology.loader import load_dictionaries
from tests.test_pdf_documents import _needs_cache


def test_tt10_end_to_end(settings):
    reg, doc = _needs_cache(settings, "TT_10_2021")
    art = ingest_document(doc, load_dictionaries(settings=settings), reg, settings)
    st = art.structure
    assert st.coverage.uncovered == []
    counts = st.kind_counts()
    assert counts["Chapter"] == 10 and counts["Article"] == 47
    assert (
        counts["Appendix"] >= 16 + 9
    )  # 16 top-level + 2.1–2.7, 3.1–3.2, 4.1–4.3 (some may be merged)

    by_id = st.by_id()
    a1 = next(n for n in st.nodes if n.kind == "Article" and n.number == "1")
    assert a1.title == "Phạm vi điều chỉnh" and by_id[a1.parent_id].kind == "Chapter"
    articles = [n for n in st.nodes if n.kind == "Article"]
    assert all(by_id[a.parent_id].kind == "Chapter" for a in articles), (
        "every Điều sits under a Chương"
    )
    ids = [n.node_id for n in st.nodes]
    assert len(ids) == len(set(ids))

    # scope partition (ADR-0008): ambient/POP chapters are parsed but never chunked
    scopes = Counter(n.attrs.get("scope") for n in st.nodes)
    assert scopes["in_scope"] > 300 and scopes["structure_only"] > 300
    chunk_ids = [c.chunk_id for c in art.chunks]
    assert len(chunk_ids) == len(set(chunk_ids))
    assert all(c.scope == "in_scope" for c in art.chunks)
    domains = Counter(c.domain for c in art.chunks)
    assert domains["wastewater"] > 50 and domains["emission"] > 50 and "ambient" not in domains

    # Điều 17.4 (sampling at >= 50 % capacity) is retrievable with page provenance
    d17 = next(c for c in art.chunks if c.source_id.endswith("Article17/Clause4"))
    assert "50%" in d17.text and d17.citation_label.endswith("Điều 17, Khoản 4, tr. 12")
    assert d17.domain == "emission" and d17.text_status == "official"
    # frequency clauses exist so rule M-01 can cite them
    assert any(c.source_id.endswith("Article16/Clause3") for c in art.chunks)
    assert any(c.source_id.endswith("Article17/Clause5") for c in art.chunks)

    # tables: unique codes, page-break continuation merged, methods split on ';'
    codes = [t.table_code for t in art.tables.tables]
    assert len(codes) == len(set(codes))
    b26 = next(t for t in art.tables.tables if t.table_code == "TT10_2021.B26")
    assert [r.row_no for r in b26.rows] == ["1", "2", "3", "4", "5", "6", "7"]
    assert (
        b26.rows[0].methods[0].code == "TCVN 4557:1988"
        and b26.rows[0].methods[1].code == "SMEWW 2550B:2017"
    )
    b27 = next(t for t in art.tables.tables if t.table_code == "TT10_2021.B27")
    assert len(b27.rows) == 16 and b27.rows[3].cells["thong_so"] == "COD"
    assert b27.rows[3].methods[0].code == "TCVN 6491:1999"

    rel = art.relations
    assert rel.mismatches == [], rel.mismatches
    assert any(r.target_document_id == "TT_43_2015" and r.scope == "partial" for r in rel.relations)
    assert art.report.unresolved_entities == []

    # MonitoringRequirement: curated map anchored to parsed nodes, every entry has its chunk
    mon = {q.requirement_id: q for q in art.monitoring}
    assert len(mon) == 29 and art.report.monitoring_requirements == 29
    assert not [w for w in art.report.warnings if w.startswith("monitoring")]
    types = Counter(q.requirement_type for q in art.monitoring)
    assert types["direct_measurement"] == 8 and types["frequency_pointer"] == 2
    q11 = mon["TT10.MR.11"]
    assert q11.requirement_type == "sampling_condition" and q11.domain == "emission"
    assert "50% công suất thiết kế" in q11.text and q11.text == d17.text
    assert any(c.type == "capacity_load" and c.value == 50 for c in q11.conditions)
    assert q11.evidence_chunk_id == d17.chunk_id and q11.citation_label == d17.citation_label
    q28 = mon["TT10.MR.28"]
    assert q28.span.printed_page == "PL-80" and q28.citation_label.endswith("tr. PL-80")
    assert {c.value for c in q28.conditions} == {3, 15}
    assert mon["TT10.MR.10"].parameters == ["SO2", "NOx", "CO"]
    assert all(q.review_status == "pending" for q in art.monitoring)
