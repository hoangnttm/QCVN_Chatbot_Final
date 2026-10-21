"""Validator rule 8 (graph-grounded citations) with a fake graph runtime — no Neo4j needed."""

from typing import Any

from app.graph.runtime import GraphStatus
from app.qa.graph_verify import check, rule_matches
from app.qa.models import Answer, Basis
from app.retrieval.query import QueryUnderstanding
from app.retrieval.retriever import EvidenceItem

ROW = "chunk:QCVN40_2025.B1.r3"
DEF_B = "chunk:QCVN_40_2025/Section1/Section1.3/Definition1.3.3/Point1.3.3.2"
LE2000 = {"type": "flow_regime", "op": "<=", "value": 2000, "unit": "m3_per_day"}
GT2000 = {"type": "flow_regime", "op": ">", "value": 2000, "unit": "m3_per_day"}
# QCVN 40:2025 Bảng 1, TSS: F<=2000 A/B/C = 40/80/120, F>2000 A/B/C = 30/60/80
RULES = [
    {"zone": z, "value": v, "conds": [band], "parameter_id": "TSS"}
    for band, vals in ((LE2000, (40, 80, 120)), (GT2000, (30, 60, 80)))
    for z, v in zip("ABC", vals, strict=True)
]


class FakeRuntime:
    timeout = 1.0

    def __init__(self, paths: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.paths = paths or []
        self.fail = fail
        self.failed: Exception | None = None

    def status(self) -> GraphStatus:
        return GraphStatus(enabled=True, ok=True, projection_version="v", index_label="idx")

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if self.fail:
            raise RuntimeError("boom")
        if "DEFINED_BY_TABLE_ROW" in cypher:
            return [
                {"cid": ROW, "row_id": "QCVN40_2025.B1.r3", "rule": f"r{i}", **r}
                for i, r in enumerate(RULES)
            ]
        return self.paths

    def mark_failed(self, e: Exception) -> None:
        self.failed = e


def _ev(cid: str, ctype: str) -> EvidenceItem:
    return EvidenceItem(
        rank=1,
        chunk_id=cid,
        citation_label=cid,
        document_id="QCVN_40_2025",
        document_code="QCVN 40:2025/BTNMT",
        chunk_type=ctype,
        domain="wastewater",
        text_status="official",
        score=1.0,
        sources=[],
        text="Cột B: ..." if ctype == "point" else "TSS | 40 | 80 | 120 | 30 | 60 | 80",
    )


QU = QueryUnderstanding(
    question="q",
    domain="wastewater",
    intent="threshold",
    parameters=["TSS"],
    zone="B",
    flow_m3_day=3000,
)
EVIDENCE = [_ev(ROW, "table_row"), _ev(DEF_B, "point")]


def _ans(text: str, cites: list[str]) -> Answer:
    return Answer(conclusion=text, basis=[Basis(text=text, citations=cites)], citations=cites)


def _check(text: str, cites: list[str] = [ROW], rt: Any = None, qu=QU, mode="enforce"):  # noqa: B006
    return check(
        _ans(text, cites),
        EVIDENCE,
        qu,
        "TSS cột B 3000 m3/ngày?",
        rt or FakeRuntime(),
        mode=mode,
        active_label="idx",
    )


def test_rule_matches_zone_flow_and_unknown_anchors():
    ok, _ = rule_matches(RULES[4], QU)  # B, >2000
    assert ok
    ok, why = rule_matches(RULES[1], QU)  # B, <=2000
    assert not ok and "lưu lượng" in why
    ok, why = rule_matches(RULES[3], QU)  # A
    assert not ok and "Cột A" in why
    vague = QueryUnderstanding(question="q", domain="wastewater", intent="threshold")
    assert all(rule_matches(r, vague)[0] for r in RULES)  # nothing stated → nothing excluded


def test_right_value_passes():
    gc = _check("TSS tối đa 60 mg/L")
    assert gc.ok and gc.paths[0].status == "connected" and gc.paths[0].allowed_values == ["60"]


def test_wrong_column_and_wrong_band_are_contradicted():
    for text, hint in (("TSS tối đa 30 mg/L", "Cột A"), ("TSS tối đa 120 mg/L", "")):
        gc = _check(text)
        assert not gc.ok and gc.paths[0].status == "contradicted", text
        assert hint in gc.errors[0]


def test_sibling_next_to_the_right_value_only_warns():
    gc = _check("TSS tối đa 60 mg/L (Cột A: 30 mg/L)")
    assert gc.ok and gc.paths[0].status == "extra_sibling" and gc.warnings


def test_numbers_of_the_question_are_not_claims():
    gc = check(
        _ans("Với 2000 m3/ngày... TSS tối đa 60 mg/L", [ROW]),
        EVIDENCE,
        QU,
        "lưu lượng 2000 m3/ngày?",
        FakeRuntime(),
        mode="enforce",
        active_label="idx",
    )
    assert gc.ok


def test_non_row_chunk_needs_a_path():
    path = {
        "cid": DEF_B,
        "anchor": "zone:wastewater:B",
        "hops": 1,
        "nodes": [{"id": "zone:wastewater:B", "label": "Zone", "name": "B"}],
        "rels": ["HAS_EVIDENCE"],
    }
    gc = _check("TSS tối đa 60 mg/L", [ROW, DEF_B], FakeRuntime(paths=[path]))
    assert gc.ok and gc.paths[1].status == "connected" and gc.paths[1].hops == 1
    weak = _check("TSS tối đa 60 mg/L", [ROW, DEF_B], FakeRuntime(paths=[]))
    assert weak.ok and weak.paths[1].status == "weak" and weak.warnings  # weak never rejects


def test_off_and_failures_never_break_the_answer():
    assert _check("TSS tối đa 30", mode="off").skipped == "off"
    rt = FakeRuntime(fail=True)
    gc = _check("TSS tối đa 30 mg/L", rt=rt)
    assert gc.ok and gc.skipped and rt.failed is not None
    abstained = Answer(conclusion="Không đủ căn cứ", abstained=True)
    assert (
        check(
            abstained, EVIDENCE, QU, "q", FakeRuntime(), mode="enforce", active_label="idx"
        ).skipped
        == "no citations"
    )


def test_o2_reference_does_not_count_as_the_right_value():
    rules = [
        {"zone": z, "value": v, "ref_o2": 6.0, "conds": [], "parameter_id": "SO2"}
        for z, v in (("A", 250), ("B", 350), ("C", 400))
    ]

    class Rt(FakeRuntime):
        def run(self, cypher, **params):
            return [{"cid": ROW, "row_id": "r", "rule": z["zone"], **z} for z in rules]

    qu = QueryUnderstanding(
        question="q", domain="emission", intent="threshold", parameters=["SO2"], zone="B"
    )
    gc = check(
        _ans("SO2 tối đa 250 mg/Nm³, ôxy tham chiếu 6 %", [ROW]),
        [_ev(ROW, "table_row")],
        qu,
        "SO2 cột B?",
        Rt(),
        mode="enforce",
        active_label="idx",
    )
    assert not gc.ok and gc.paths[0].status == "contradicted"


def test_threshold_restated_in_the_question_does_not_filter():
    rule = {
        "zone": "B",
        "value": 1,
        "conds": [{"type": "capacity", "op": "<", "value": 2.0, "unit": "t_per_h"}],
    }
    qu = QueryUnderstanding(
        question="q",
        domain="emission",
        intent="threshold",
        zone="B",
        capacity_value=2.0,
        capacity_unit="t_per_h",
    )
    assert rule_matches(rule, qu)[0]  # "dưới 2 tấn/giờ" → capacity 2 restates the condition
    qu3 = qu.model_copy(update={"capacity_value": 3.0})
    assert not rule_matches(rule, qu3)[0]


def test_numbers_of_document_codes_are_not_claims():
    # "QCVN 40:2025" must not read as the Cột A value 40
    gc = _check("Theo QCVN 40:2025/BTNMT, TSS tối đa 60 mg/L")
    assert gc.ok and gc.paths[0].status == "connected"
