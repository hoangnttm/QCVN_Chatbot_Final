"""Fact layer (plan 2026-10-06, G2): cell address, declared condition dimensions, trust, validity,
and the quality gate — on the real ingestion artefacts of the corpus."""

import datetime as dt
import json

import pytest

from app.db import facts as fact_layer
from app.db import models as m
from app.db.loader import load_document
from app.db.session import get_sessionmaker
from app.ingestion.address import cell_address, is_resolvable, node_address
from app.ingestion.models import LimitRule, Node, Span
from app.ingestion.pipeline import ingest_document
from app.ingestion.quality import document_quality
from app.ontology.loader import load_dictionaries
from app.registry.loader import load_registry
from app.registry.schema import Relation


@pytest.fixture(scope="module")
def qcvn40(settings):
    reg = load_registry(settings=settings)
    d = load_dictionaries(settings=settings)
    return ingest_document(reg.by_id("QCVN_40_2025"), d, reg, settings), reg, d


def _node(node_id, kind, number=None, parent=None):
    span = Span(file_hash="0" * 64, line_start=1, line_end=1)
    return Node(
        node_id=node_id,
        document_id="D",
        kind=kind,
        order=0,
        depth=0,
        parent_id=parent,
        number=number,
        raw="",
        span=span,
    )


def test_legal_address_is_the_structural_path():
    nodes = {
        n.node_id: n
        for n in [
            _node("d", "Document"),
            _node("s2", "Section", "2", "d"),
            _node("s21", "Section", "2.1", "s2"),
            _node("t1", "Table", "1", "s21"),
            _node("pre", "Preamble", None, "d"),
        ]
    }
    assert node_address("t1", nodes) == [
        {"level": "Section", "number": "2"},
        {"level": "Section", "number": "2.1"},
        {"level": "Table", "number": "1"},
    ]
    cell = cell_address("t1", nodes, "2.a", "F>2000.B", "F > 2000, Cột B")
    assert cell[-2:] == [
        {"level": "TableRow", "number": "2.a"},
        {"level": "Column", "key": "F>2000.B", "label": "F > 2000, Cột B"},
    ]
    assert is_resolvable(cell) and is_resolvable(node_address("pre", nodes))  # "Lời nói đầu"
    assert not is_resolvable([]) and not is_resolvable([{"level": "Paragraph"}])


def test_every_rule_carries_its_cell(qcvn40):
    art, _, _ = qcvn40
    rule = next(r for r in art.rules if r.rule_code == "QCVN40_2025.B1.r1.F>2000.B")
    assert rule.column_key == "F>2000.B" and rule.cell_raw == "≤ 50"
    assert rule.zone == "B" and rule.zone_scheme == "QCVN_40_2025"  # the zone is scoped
    assert [p["level"] for p in rule.address] == [
        "Section",
        "Section",
        "Table",
        "TableRow",
        "Column",
    ]
    assert rule.address[2] == {"level": "Table", "number": "1"}
    assert all(r.cell_raw and r.column_key and is_resolvable(r.address) for r in art.rules)
    # the address identifies the cell: no two rules share one
    keys = {json.dumps(r.address, sort_keys=True, ensure_ascii=False) for r in art.rules}
    assert len(keys) == len(art.rules)


def test_conditions_are_rows_on_declared_dimensions(qcvn40):
    art, _, d = qcvn40
    rows = fact_layer.rule_condition_rows(art.rules, d)
    by_dim: dict[str, list] = {}
    for row in rows:
        by_dim.setdefault(row["dimension"], []).append(row)
    assert len(by_dim["zone"]) == len(art.rules) and len(by_dim["flow_regime"]) == 24
    flow = next(r for r in by_dim["flow_regime"] if r["rule_code"].endswith("r1.F>2000.B"))
    assert (flow["op"], flow["value_num"], flow["unit"]) == (">", 2000.0, "m3_per_day")
    zone = next(r for r in by_dim["zone"] if r["rule_code"].endswith("r1.F>2000.B"))
    assert zone["value_text"] == "B" and zone["scope"] == "QCVN_40_2025"
    assert flow["scope"] is None  # only scoped dimensions carry a scope

    odd = art.rules[0].model_copy(deep=True)
    odd.conditions[0].type = "moon_phase"
    with pytest.raises(ValueError, match="condition_dimensions.yaml"):
        fact_layer.rule_condition_rows([odd], d)


def test_trust_levels():
    assert fact_layer.trust_of("approved", verified=False) == "approved"
    assert fact_layer.trust_of("pending", verified=True) == "auto_verified"
    assert fact_layer.trust_of("pending", verified=False) == "pending"


def test_validity_comes_from_the_registry(qcvn40):
    _, reg, _ = qcvn40
    own = fact_layer.document_validity("QCVN_40_2025", reg)
    assert own["valid_from"] == dt.date(2025, 9, 1) and own["valid_to"] is None
    via = fact_layer.document_validity("QCVN_19_2024", reg)  # its file prints no date
    assert via["valid_from"] == dt.date(2025, 7, 1) and "PROMULGATES" in via["basis"][0]
    assert fact_layer.document_validity("NOT_A_DOCUMENT", reg)["valid_from"] is None

    # a later document that fully replaces an earlier one closes its interval the day before
    changed = reg.model_copy(deep=True)
    changed.by_id("QCVN_40_2025").relations.append(Relation(type="REPLACES", target="TT_10_2021"))
    closed = fact_layer.document_validity("TT_10_2021", changed)
    assert closed["valid_to"] == dt.date(2025, 8, 31)
    assert any("REPLACES" in b for b in closed["basis"])


def test_quality_gate_on_the_corpus(settings, qcvn40):
    _, reg, d = qcvn40
    q = document_quality("QCVN_40_2025", reg, d, settings)
    assert q["passed"] and all(q["criteria"].values())
    assert q["value_cells"] == q["limit_rules"] == q["rules_with_cell_address"] == 213
    assert q["parameter_resolved"] == 1.0 and q["unit_resolved"] == 1.0
    assert q["facts_by_type"]["limit"] == 213 and q["facts_by_type"]["definition"] == 4
    assert q["valid_from"] == "2025-09-01"
    tt10 = document_quality("TT_10_2021", reg, d, settings)
    assert tt10["passed"] and tt10["facts_by_type"]["monitoring"] == 29


def test_fact_layer_is_loaded_with_the_document(db_settings):
    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker.begin() as session:
        counts = load_document("QCVN_40_2025", reg, db_settings, session)
    assert counts["limit_rules"] == 213 and counts["rule_conditions"] >= 213
    with maker() as session:
        rule = session.get(m.LimitRule, "QCVN40_2025.B1.r1.F>2000.B")
        assert rule.column_key == "F>2000.B" and rule.cell_raw == "≤ 50"
        assert rule.valid_from == dt.date(2025, 9, 1) and rule.trust in ("auto_verified", "pending")
        fact = session.get(m.Fact, "fact:limit:QCVN40_2025.B1.r1.F>2000.B")
        assert fact.chunk_id == "chunk:QCVN40_2025.B1.r1" and fact.quote == "≤ 50"
        assert fact.locator == "F>2000.B" and fact.address[-1]["level"] == "Column"
        assert fact.address_label.endswith("Bảng 1, TT 1, F > 2000, Cột B")
        n_limit = session.query(m.Fact).filter_by(document_id="QCVN_40_2025", fact_type="limit")
        assert n_limit.count() == 213
        flow = (
            session.query(m.RuleCondition)
            .filter_by(dimension="flow_regime", rule_code="QCVN40_2025.B1.r1.F>2000.B")
            .one()
        )
        assert flow.value_num == 2000.0 and flow.op == ">"
    # reloading replaces the fact layer instead of piling it up
    with maker.begin() as session:
        again = load_document("QCVN_40_2025", reg, db_settings, session)
    assert again["facts"] == counts["facts"]
    with maker() as session:
        assert (
            session.query(m.Fact).filter_by(document_id="QCVN_40_2025").count() == counts["facts"]
        )


def test_rule_without_a_zone_has_no_scheme():
    rule = LimitRule(
        rule_code="X.B2.r1.value",
        document_id="X",
        domain="air",
        table_code="X.B2",
        row_id="X.B2.r1",
        parameter_raw="Nitrit",
        unit_raw="mg/L",
        zone=None,
        op="<=",
        value=0.05,
        citation_label="QCVN X, Bảng 2, TT 1",
        span=Span(file_hash="0" * 64, line_start=1, line_end=1),
    )
    assert (
        rule.zone_scheme is None
        and fact_layer.rule_condition_rows([rule], load_dictionaries()) == []
    )
