"""Value columns of threshold tables (plan 2026-10-06 G1, step 0.5): a table may have a zone axis,
other condition axes, both, or a single value column. Synthetic tables shaped like QCVN 05:2023
(limits by averaging period, no zone) and QCVN 08:2023 (one value per parameter; quality levels)."""

from app.ingestion.axes import ColumnSchema
from app.ingestion.extract_rules import extract_limit_rules
from app.ingestion.grammar import Grammar
from app.ingestion.sanitize import SanitizedText
from app.ingestion.structure_parser import StructureParser
from app.ingestion.table_parser import TableParser, parse_value

TEXT = """# QCVN X

### 2. QUY ĐỊNH KỸ THUẬT

**Bảng 1. Giá trị giới hạn theo thời gian trung bình**

| TT | Thông số | Trung bình 1 giờ | Trung bình 24 giờ | Trung bình năm |
|---|---|---|---|---|
| 1 | SO2 | 350 | 125 | 50 |
| 2 | CO | 30.000 | - | - |

**Bảng 2. Giá trị giới hạn tối đa**

| TT | Thông số | Đơn vị | Giá trị giới hạn |
|---|---|---|---|
| 1 | Nitrit | mg/L | 0,05 |
| 2 | pH | - | 6,0 - 8,5 |

**Bảng 3. Giá trị giới hạn theo mức**

| TT | Thông số | Đơn vị | A | B | C | D |
|---|---|---|---|---|---|---|
| 1 | DO | mg/L | ≥ 6,0 | ≥ 5,0 | ≥ 4,0 | ≥ 2,0 |
"""

GRAMMAR = {
    "document_id": "QCVN_X",
    "source_format": "md",
    "structure": [
        {"kind": "Title", "match": r"^# (?P<code>QCVN X)", "block": "until_blank"},
        {
            "kind": "Section",
            "level": 1,
            "match": r"^### (?P<number>\d+)\.\s+(?P<title>.+)$",
            "parent_kinds": ["Document"],
        },
        {
            "kind": "Table",
            "match": r"^\*\*Bảng (?P<number>\d+)\.\s*(?P<title>.+?)\*\*\s*$",
            "block": "table",
            "parent_kinds": ["Section"],
        },
    ],
    "tables": {
        "X.B1": {
            "title_match": r"Bảng 1\.",
            "header_rows": 1,
            "columns": ["tt", "parameter", "1h", "24h", "year"],
            "condition_axes": [
                {
                    "name": "averaging_period",
                    "values": {
                        "1h": {"value": 1, "unit": "hour", "label": "Trung bình 1 giờ"},
                        "24h": {"value": 24, "unit": "hour", "label": "Trung bình 24 giờ"},
                        "year": {"value": 1, "unit": "year", "label": "Trung bình năm"},
                    },
                }
            ],
            "default_op": "<=",
            "default_unit": "mg/Nm3",
            "empty_markers": ["-"],
        },
        "X.B2": {
            "title_match": r"Bảng 2\.",
            "header_rows": 1,
            "columns": ["tt", "parameter", "unit", "value"],
            "value_columns": ["value"],
            "default_op": "<=",
        },
        "X.B3": {
            "title_match": r"Bảng 3\.",
            "header_rows": 1,
            "columns": ["tt", "parameter", "unit", "A", "B", "C", "D"],
            "zone_axis": ["A", "B", "C", "D"],
            "zone_label": "Mức {zone}",
        },
    },
    "citation": {
        "document_label": "QCVN X",
        "Section": "Mục {number}",
        "Table": "Bảng {number}",
        "TableRow": "TT {row_no}",
    },
}


def _rules():
    grammar = Grammar.model_validate(GRAMMAR)
    lines = TEXT.split("\n")
    structure = StructureParser(grammar, "QCVN_X", "0" * 64, "test").parse(
        SanitizedText(lines=lines), (1, len(lines))
    )
    tables = TableParser(grammar, structure).parse_all()
    assert not [w for t in tables.tables for r in t.rows for w in r.parse_warnings]
    return {r.rule_code: r for r in extract_limit_rules(tables, "air")}


def test_condition_axis_without_zone():
    rules = _rules()
    so2 = rules["X.B1.r1.24h"]
    assert (so2.op, so2.value, so2.zone) == ("<=", 125.0, None)  # a bare number is a maximum
    cond = {c.type: c for c in so2.conditions}
    assert cond["averaging_period"].value == 24 and cond["averaging_period"].unit == "hour"
    assert "zone" not in cond
    assert so2.citation_label.endswith("Bảng 1, TT 1, Trung bình 24 giờ")
    assert so2.unit_raw == "mg/Nm3"  # the table states its unit once, above the header
    # "-" means no limit for that period: no rule, and no "unparsed" warning
    assert rules["X.B1.r2.1h"].value == 30000.0
    assert "X.B1.r2.24h" not in rules and "X.B1.r2.year" not in rules


def test_single_value_table():
    rules = _rules()
    nitrit = rules["X.B2.r1.value"]
    assert (nitrit.op, nitrit.value, nitrit.zone) == ("<=", 0.05, None)
    assert not nitrit.conditions and nitrit.citation_label.endswith("Bảng 2, TT 1")
    ph = rules["X.B2.r2.value"]
    assert (ph.op, ph.min, ph.max) == ("between", 6.0, 8.5)  # a range keeps its own operator


def test_zone_axis_with_other_codes_and_label():
    rules = _rules()
    d = rules["X.B3.r1.D"]
    assert (d.op, d.value, d.zone) == (">=", 2.0, "D")  # an explicit operator wins over default_op
    assert d.citation_label.endswith("Bảng 3, TT 1, Mức D")
    assert [c.text for c in d.conditions if c.type == "zone"] == ["Mức D"]
    assert len([r for r in rules.values() if r.table_code == "X.B3"]) == 4


def test_column_schema_keeps_the_existing_tables_as_they_were():
    flow = {
        "zone_axis": ["A", "B", "C"],
        "header_condition_axis": {
            "name": "flow_regime",
            "values": {
                "F<=2000": {"op": "<=", "value": 2000},
                "F>2000": {"op": ">", "value": 2000},
            },
        },
    }
    schema = ColumnSchema.from_config(flow)
    assert schema.is_value_column("F>2000.B") and schema.is_value_column("A")
    assert not schema.is_value_column("parameter") and not schema.is_value_column("unit")
    m = schema.meaning("F>2000.B")
    assert m.zone == "B" and [(a.name, k) for a, k in m.tiers] == [("flow_regime", "F>2000")]
    assert schema.zone_text("B") == "Cột B"
    assert not ColumnSchema.from_config({"columns": ["tt", "item", "methods"]}).has_values


def test_parse_value_options():
    assert parse_value("350").op == "="  # unchanged default
    assert parse_value("350", default_op="<=").op == "<="
    assert parse_value("≥ 6,0", default_op="<=").op == ">="
    assert parse_value("-", empty_markers=("-",)) is None
    assert parse_value("-").op == "unparsed"  # without the option a dash is still reported


def test_dictionary_stubs_for_a_new_document():
    from app.ontology.loader import load_dictionaries
    from app.ontology.stubs import parameter_id_for, propose

    grammar = Grammar.model_validate(GRAMMAR)
    lines = TEXT.split("\n")
    structure = StructureParser(grammar, "QCVN_X", "0" * 64, "test").parse(
        SanitizedText(lines=lines), (1, len(lines))
    )
    tables = TableParser(grammar, structure).parse_all()
    d = load_dictionaries()
    out = propose(tables, d, "ambient_air")
    known = {p.parameter_id for p in d.parameters}
    # SO2, CO, pH … are known parameters of other domains: reported, not duplicated
    extended = {e["parameter_id"]: e for e in out["extend_parameter_domains"]}
    assert "SO2" in extended and extended["SO2"]["add_domain"] == "ambient_air"
    assert all(e["parameter_id"] in known for e in out["extend_parameter_domains"])
    # what the dictionaries do not know becomes a stub with a fresh id and its source rows
    for stub in out["new_parameters"]:
        assert stub["parameter_id"] not in known and stub["domains"] == ["ambient_air"]
        assert stub["sources"]
    assert parameter_id_for("Chì (Pb)", "Pb", {"Pb"}) == "Pb_2"
    assert parameter_id_for("Độ đục", None, set()) == "Do_duc"
