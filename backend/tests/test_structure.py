"""Phase 1 gate for parse_structure on the Markdown sources: 100 % line coverage and snapshot counts
(PLAN.md §5 'Cổng chất lượng Phase 1')."""

import pytest

from app.ingestion.pipeline import parse_structure_for
from app.registry.loader import load_registry

EXPECTED = {
    # counts verified by hand against the source files on 2026-09-24
    "TT_06_2025": {"Article": 5, "Clause": 11, "Point": 11, "Preamble": 1},
    "QCVN_40_2025": {
        "Section": 11,
        "Clause": 22,
        "Point": 11,
        "Definition": 4,
        "Table": 2,
        "AppendixTable": 2,
        "Appendix": 2,
        "Footnote": 3,
    },
    "QCVN_19_2024": {
        "Section": 11,
        "Clause": 18,
        "Point": 6,
        "Definition": 13,
        "Table": 2,
        "AppendixTable": 3,
        "Appendix": 3,
        "Footnote": 3,
        "Formula": 2,
    },
}


@pytest.fixture(scope="module")
def parsed(settings):
    reg = load_registry(settings=settings)
    return {doc_id: parse_structure_for(reg.by_id(doc_id), settings) for doc_id in EXPECTED}


@pytest.mark.parametrize("doc_id", list(EXPECTED))
def test_full_line_coverage(parsed, doc_id):
    cov = parsed[doc_id].coverage
    assert cov.uncovered == [], cov.uncovered[:10]
    assert cov.covered == cov.total_nonblank


@pytest.mark.parametrize("doc_id", list(EXPECTED))
def test_snapshot_counts(parsed, doc_id):
    counts = parsed[doc_id].kind_counts()
    for kind, n in EXPECTED[doc_id].items():
        assert counts.get(kind, 0) == n, (kind, counts.get(kind), n)


def test_numbering_is_contiguous(parsed):
    """Article/Clause numbers under one parent must run 1..n without gaps."""
    for doc_id, ps in parsed.items():
        by_parent: dict[str, list[int]] = {}
        for n in ps.nodes:
            if n.kind in ("Article", "Clause") and n.number and n.number.isdigit():
                by_parent.setdefault(n.parent_id or "", []).append(int(n.number))
        for parent, nums in by_parent.items():
            assert nums == list(range(1, len(nums) + 1)), (doc_id, parent, nums)


def test_citation_labels(parsed):
    labels = {n.citation_label for ps in parsed.values() for n in ps.nodes if n.citation_label}
    for must in [
        "Thông tư 06/2025/TT-BTNMT, Điều 2, Khoản 2, điểm a",
        "Thông tư 06/2025/TT-BTNMT, Điều 3, Khoản 1",
        "QCVN 40:2025/BTNMT, Mục 1.1.2",
        "QCVN 40:2025/BTNMT, Mục 2.2, Bảng 2",
        "QCVN 40:2025/BTNMT, Mục 4.3.2",
        "QCVN 40:2025/BTNMT, Phụ lục 2",
        "QCVN 19:2024/BTNMT, Mục 1.3.13.2",
        "QCVN 19:2024/BTNMT, Mục 2.1, Bảng 1",
        "QCVN 19:2024/BTNMT, Mục 4.2.3",
    ]:
        assert must in labels, must
    # labels are unique within a document for the kinds that carry their own template
    labelled = {
        "Article",
        "Clause",
        "Point",
        "Section",
        "Definition",
        "Table",
        "Appendix",
        "Footnote",
    }
    for doc_id, ps in parsed.items():
        seen = [n.citation_label for n in ps.nodes if n.citation_label and n.kind in labelled]
        dupes = {x for x in seen if seen.count(x) > 1}
        assert not dupes, (doc_id, dupes)


def test_table_blocks_capture_grid(parsed):
    q19 = parsed["QCVN_19_2024"]
    tables = [n for n in q19.nodes if n.kind == "Table"]
    assert len(tables[0].table_lines) >= 250, "Bảng 1 QCVN 19 has 215 value rows + headers/groups"
    assert tables[0].table_lines[0].startswith("| TT")
