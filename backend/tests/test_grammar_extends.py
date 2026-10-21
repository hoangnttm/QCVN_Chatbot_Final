"""Grammar inheritance (plan 2026-10-06 G1, step 0.8): a document of a known shape states only
what differs from its base profile."""

import pytest

from app.ingestion.grammar import load_grammar, merge_grammar

BASE = r"""
document_id: BASE
source_format: pdf
preprocess: [pdf_extract]
structure:
  - { kind: Article, match: '^Điều (?P<number>\d+)\.' }
tables:
  T1: { header_rows: 1, columns: [tt, parameter, A, B], zone_axis: [A, B] }
citation: { Article: "Điều {number}", Table: "Bảng {number}" }
"""

CHILD = """
extends: base.yaml
document_id: CHILD
tables:
  T1: { zone_axis: [A, B, C], zone_label: "Mức {zone}" }
  T2: { header_rows: 1, columns: [tt, parameter, value], value_columns: [value] }
citation: { document_label: "QCVN X" }
"""


def test_child_overrides_and_extends_its_base(tmp_path):
    (tmp_path / "base.yaml").write_text(BASE, "utf-8")
    (tmp_path / "child.yaml").write_text(CHILD, "utf-8")
    g = load_grammar(tmp_path / "child.yaml")
    assert g.document_id == "CHILD" and g.source_format == "pdf" and g.preprocess == ["pdf_extract"]
    assert [r.kind for r in g.structure] == ["Article"]  # inherited as a whole
    t1 = g.tables["T1"]
    assert t1["columns"] == ["tt", "parameter", "A", "B"]  # kept from the base
    assert t1["zone_axis"] == ["A", "B", "C"] and t1["zone_label"] == "Mức {zone}"  # overridden
    assert "T2" in g.tables
    assert g.citation == {
        "Article": "Điều {number}",
        "Table": "Bảng {number}",
        "document_label": "QCVN X",
    }
    assert load_grammar(tmp_path / "base.yaml").document_id == "BASE"  # a base is usable alone


def test_lists_are_replaced_not_concatenated():
    merged = merge_grammar({"structure": [1, 2], "preprocess": ["a"]}, {"structure": [3]})
    assert merged == {"structure": [3], "preprocess": ["a"]}


def test_a_cycle_is_an_error(tmp_path):
    (tmp_path / "a.yaml").write_text("extends: b.yaml\ndocument_id: A\n", "utf-8")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\ndocument_id: B\n", "utf-8")
    with pytest.raises(ValueError, match="extends itself"):
        load_grammar(tmp_path / "a.yaml")
