"""Phase 1 gate for parse_tables on the two QCVN: snapshot counts, zero unparsed cells, and a set of
hand-verified values (PLAN.md §1.3, docs/table-row-json-spec.md)."""

import pytest

from app.ingestion.pipeline import parse_structure_for, parse_tables_for
from app.registry.loader import load_registry


@pytest.fixture(scope="module")
def tables(settings):
    reg = load_registry(settings=settings)
    out = {}
    for doc_id in ("QCVN_40_2025", "QCVN_19_2024"):
        doc = reg.by_id(doc_id)
        st = parse_structure_for(doc, settings)
        pt = parse_tables_for(doc, st, settings)
        assert pt.warnings == [], pt.warnings
        for t in pt.tables:
            out[t.table_code] = t
    return out


def _row(t, row_id):
    m = {r.row_id: r for r in t.rows}
    assert row_id in m, sorted(m)[:20]
    return m[row_id]


def test_every_table_configured(tables):
    assert set(tables) == {
        "QCVN40_2025.B1",
        "QCVN40_2025.B2",
        "QCVN40_2025.PL1",
        "QCVN40_2025.PL2",
        "QCVN19_2024.B1",
        "QCVN19_2024.B2",
        "QCVN19_2024.PL1",
        "QCVN19_2024.PL2",
        "QCVN19_2024.PL3",
    }


def test_no_parse_warnings(tables):
    bad = [
        (t.table_code, r.row_id, r.parse_warnings)
        for t in tables.values()
        for r in t.rows
        if r.parse_warnings
    ]
    assert bad == [], bad[:10]


def test_snapshot_row_counts(tables):
    b1, b2 = tables["QCVN40_2025.B1"], tables["QCVN40_2025.B2"]
    assert [r.row_no for r in b1.rows] == ["1", "2.a", "2.b", "3"]
    assert len(b2.rows) == 64 and sum(r.is_group_header for r in b2.rows) == 1
    assert len(tables["QCVN40_2025.PL1"].rows) == 67
    pl2 = tables["QCVN40_2025.PL2"]
    assert len(pl2.data_rows()) == 54 and sum(r.is_group_header for r in pl2.rows) == 10

    q1, q2 = tables["QCVN19_2024.B1"], tables["QCVN19_2024.B2"]
    assert len(q1.data_rows()) == 215, len(q1.data_rows())
    assert len(q2.data_rows()) == 89, len(q2.data_rows())
    assert len({r.row_path[0] for r in q1.rows}) == 54
    assert len({r.row_path[0] for r in q2.rows}) == 12
    assert len(tables["QCVN19_2024.PL2"].rows) == 17
    assert len(tables["QCVN19_2024.PL3"].rows) == 16


def test_hand_verified_values(tables):
    b1 = tables["QCVN40_2025.B1"]
    bod = _row(b1, "QCVN40_2025.B1.r1")
    assert bod.values["F>2000.B"].value == 50 and bod.values["F<=2000.A"].value == 40
    cod, toc = _row(b1, "QCVN40_2025.B1.r2.a"), _row(b1, "QCVN40_2025.B1.r2.b")
    assert "COD" in cod.cells["parameter"] and cod.values["F<=2000.A"].value == 65
    assert "TOC" in toc.cells["parameter"] and toc.values["F>2000.C"].value == 50

    b2 = tables["QCVN40_2025.B2"]
    ph = _row(b2, "QCVN40_2025.B2.r1")
    assert ph.values["A"].op == "between" and (ph.values["A"].min, ph.values["A"].max) == (6, 9)
    tp = _row(b2, "QCVN40_2025.B2.r4")
    assert tp.is_group_header and not tp.values
    tp42 = _row(b2, "QCVN40_2025.B2.r4.2")
    assert tp42.values["A"].value == 8.0 and tp42.inherits_from == "QCVN40_2025.B2.r4"
    assert "Tổng Phốt pho" in tp42.cells["parameter"]
    assert any(c.type == "receiving_body" for c in tp42.conditions)
    assert any(c.type == "sector_scope" for c in tp42.conditions)
    cl = _row(b2, "QCVN40_2025.B2.r30")
    assert cl.exceptions and "mặn" in cl.exceptions[0]
    assert cl.values["B"].value == 1000
    hg = _row(b2, "QCVN40_2025.B2.r8")
    assert hg.values["A"].value == 0.001  # '0,001' Vietnamese decimal comma

    q1 = tables["QCVN19_2024.B1"]
    co = _row(q1, "QCVN19_2024.B1.r2.1.2.1")
    assert co.values["B"].value == 350 and co.values["B"].ref_o2 == 6
    assert "CO" in co.cells["parameter"] and co.inherits_from is not None
    kinds = {c.type: c for c in co.conditions}
    assert kinds["fuel_type"].value == "solid" and kinds["fuel_type"].inherited_from is not None
    assert kinds["capacity"].op == ">=" and kinds["capacity"].value == 20
    assert kinds["capacity"].unit == "t_steam_per_h" and kinds["capacity"].alt["value"] == 12380000
    assert kinds["equipment_path"].value == ["1", "1.2", "1.2.1"]
    assert (
        co.citation_label
        == "QCVN 19:2024/BTNMT, Mục 2.1, Bảng 1, TT 2 (Cacbon monoxit), Thiết bị 1.2.1"
    )
    grp = _row(q1, "QCVN19_2024.B1.r2.1")
    assert grp.is_group_header

    q2 = tables["QCVN19_2024.B2"]
    pm = _row(q2, "QCVN19_2024.B2.r1.1.3")
    assert pm.values["C"].value == 60 and pm.values["C"].ref_o2 == 6
    assert {c.type: c.value for c in pm.conditions}["fuel_type"] == "biomass_solid"
    smoke = _row(q2, "QCVN19_2024.B2.r12")
    assert smoke.values["A"].value == 2 and smoke.row_path == ["12"]


def test_methods_and_sectors(tables):
    pl1 = tables["QCVN40_2025.PL1"]
    cod = _row(pl1, "QCVN40_2025.PL1.r4")
    assert cod.methods[0].code == "TCVN 6491:1999" and cod.methods[0].order == 1
    assert _row(pl1, "QCVN40_2025.PL1.r1").methods[0].stage == "sampling"

    pl2 = tables["QCVN40_2025.PL2"]
    paper = _row(pl2, "QCVN40_2025.PL2.r13")
    assert paper.vsic_codes == ["C 17"]
    assert any("Clo" in (c["condition_text"] or "") for c in paper.conditional_parameters)
    auto = _row(pl2, "QCVN40_2025.PL2.r41")
    assert auto.vsic_codes == ["C 29", "C 30"]

    q_pl1 = tables["QCVN19_2024.PL1"]
    assert any(m.stage == "field" for r in q_pl1.rows for m in r.methods)
    tef = tables["QCVN19_2024.PL2"]
    assert _row(tef, "QCVN19_2024.PL2.r1").coefficient["tef"] == 1
    assert _row(tef, "QCVN19_2024.PL2.r7").coefficient["tef"] == 0.0003
