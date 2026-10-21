"""End-to-end Phase 1 gate on the Markdown documents: rules, entity resolution and chunks."""

import pytest

from app.ingestion.pipeline import ingest_document
from app.ontology.loader import load_dictionaries
from app.registry.loader import load_registry


@pytest.fixture(scope="module")
def artifacts(settings):
    reg = load_registry(settings=settings)
    d = load_dictionaries(settings=settings)
    return {
        doc_id: ingest_document(reg.by_id(doc_id), d, reg, settings)
        for doc_id in ("TT_06_2025", "QCVN_40_2025", "QCVN_19_2024")
    }


def test_relations_match_registry(artifacts):
    tt06 = artifacts["TT_06_2025"].relations
    assert tt06.effective_date_from_text == "2025-09-01"
    repeals = {(r.type, r.target_document_id) for r in tt06.relations}
    assert ("REPEALS", "TT_47_2011") in repeals and ("REPEALS", "QCVN_52_2017") in repeals
    assert len([r for r in tt06.relations if r.type == "REPEALS"]) == 11
    assert tt06.mismatches == [], tt06.mismatches
    kinds = [t.kind for t in tt06.transition_rules]
    assert (
        kinds.count("transition") == 2 and kinds.count("roadmap") == 3 and "default_zone" in kinds
    )
    roadmap = [t for t in tt06.transition_rules if t.kind == "roadmap"]
    assert any("2032-01-01" in t.dates for t in roadmap)
    assert any(t.relative_dates for t in roadmap)

    q40 = artifacts["QCVN_40_2025"].relations
    assert len([r for r in q40.relations if r.type == "REPLACES"]) == 11 and q40.mismatches == []
    q19 = artifacts["QCVN_19_2024"].relations
    full = [r for r in q19.relations if r.type == "REPLACES" and r.scope == "full"]
    partial = [r for r in q19.relations if r.type == "REPLACES" and r.scope == "partial"]
    assert len(full) == 7 and len(partial) == 5, (len(full), len(partial))
    assert {r.target_part for r in partial} >= {"Bảng 1 và Bảng 4", "Bảng 2", "Bảng 4"}
    assert q19.mismatches == [], q19.mismatches


def test_rule_counts(artifacts):
    assert (
        len(artifacts["QCVN_40_2025"].rules) == 4 * 6 + 63 * 3
    )  # Bảng 1 (2 tiers × 3 zones) + Bảng 2
    assert len(artifacts["QCVN_19_2024"].rules) == (215 + 89) * 3
    assert artifacts["TT_06_2025"].rules == []


def test_rules_carry_qualifiers(artifacts):
    rules = {r.rule_code: r for r in artifacts["QCVN_19_2024"].rules}
    co = rules["QCVN19_2024.B1.r2.1.2.1.B"]
    assert co.value == 350 and co.ref_o2 == 6 and co.zone == "B"
    assert co.parameter_id == "CO" and co.unit_id == "mg_Nm3"
    assert {c.type for c in co.conditions} >= {"equipment_path", "fuel_type", "capacity", "zone"}
    assert co.citation_label.endswith("Thiết bị 1.2.1, Cột B")
    w = {r.rule_code: r for r in artifacts["QCVN_40_2025"].rules}
    bod = w["QCVN40_2025.B1.r1.F>2000.B"]
    assert bod.value == 50 and bod.parameter_id == "BOD5" and bod.unit_id == "mg_L"
    assert any(c.type == "flow_regime" and c.op == ">" and c.value == 2000 for c in bod.conditions)
    assert bod.citation_label == "QCVN 40:2025/BTNMT, Mục 2.1, Bảng 1, TT 1, F > 2000, Cột B"
    tp = w["QCVN40_2025.B2.r4.2.A"]
    assert tp.parameter_id == "T_P" and tp.value == 8.0


def test_entities_resolved(artifacts):
    for doc_id in ("QCVN_40_2025", "QCVN_19_2024"):
        rep = artifacts[doc_id].report
        assert rep.unresolved_entities == [], rep.unresolved_entities[:15]
    rules = artifacts["QCVN_19_2024"].rules
    assert all(r.parameter_id and r.unit_id for r in rules)
    assert sum(1 for r in rules if r.equipment_type_id) == len(rules)


def test_chunks(artifacts):
    ch = artifacts["QCVN_40_2025"].chunks
    types = {c.chunk_type for c in ch}
    assert {"clause", "point", "definition", "footnote", "preamble", "table_row"} <= types
    row = next(c for c in ch if c.source_id == "QCVN40_2025.B2.r4.2")
    assert "sông" in row.context_text and "Tổng Phốt pho" in row.context_text
    # the cell as printed, under the name the regulation gives the column (G2 step 2.7)
    assert "Cột A: ≤ 8,0; Cột B: ≤ 14" in row.text and "8.0" not in row.context_text
    banded = next(c for c in ch if c.source_id == "QCVN40_2025.B1.r1")
    assert "F > 2000, Cột B: ≤ 50" in banded.text and "F>2000.B" not in banded.context_text
    # no LaTeX or Markdown markup is left in what gets indexed and embedded
    assert not [c.chunk_id for c in ch if "$" in c.context_text or "**" in c.context_text]
    assert row.citation_label == "QCVN 40:2025/BTNMT, Mục 2.2, Bảng 2, TT 4.2"
    defs = [c for c in ch if c.chunk_type == "definition"]
    assert len(defs) == 4 and defs[0].text.startswith("Nước thải sinh hoạt:")
    q19 = artifacts["QCVN_19_2024"].chunks
    co = next(c for c in q19 if c.source_id == "QCVN19_2024.B1.r2.1.2.1")
    assert "nhiên liệu rắn" in co.context_text.lower() and "O2 tham chiếu 6" in co.context_text
    assert all(c.chunk_id.startswith("chunk:") for c in q19)
    ids = [c.chunk_id for c in q19]
    assert len(ids) == len(set(ids))


def test_registry_fact_chunks_only_for_admin_declared(artifacts):
    assert not [c for c in artifacts["TT_06_2025"].chunks if c.chunk_type == "registry_fact"]
