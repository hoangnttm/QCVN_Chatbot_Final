"""Schema v1.3 helpers and the graph-first candidate set, without a graph server."""

from types import SimpleNamespace

from app.core.config import get_settings
from app.graph import enrich
from app.retrieval import graph_candidates as gc
from app.retrieval.query import QueryUnderstanding


def _node(node_id, kind, number=None, parent=None, title=None, doc="D"):
    return SimpleNamespace(
        node_id=node_id, kind=kind, number=number, parent_id=parent, title=title, document_id=doc
    )


NODES = {
    n.node_id: n
    for n in [
        _node("D/A2", "Article", "2", title="Hiệu lực thi hành"),
        _node("D/A2/K4", "Clause", "4", parent="D/A2"),
        _node("D/A3", "Article", "3", title="Điều khoản chuyển tiếp"),
        _node("D/A3/K1", "Clause", "1", parent="D/A3"),
        _node("D/S2.1", "Section", "2.1", title="Giá trị giới hạn"),
        _node("D/S2.1/T1", "Table", "1", parent="D/S2.1"),
        _node("D/PL1", "Appendix", "1", title="Phương pháp xác định"),
    ]
}


def test_one_word_vietnamese_aliases_are_not_mentions():
    assert enrich._ambiguous("hồ") and enrich._ambiguous("đồng") and enrich._ambiguous("bụi")
    assert not enrich._ambiguous("cod") and not enrich._ambiguous("so2")
    assert not enrich._ambiguous("bụi tổng") and not enrich._ambiguous("vùng hạn chế phát thải")


def test_references_resolve_inside_the_document_only():
    index = enrich.reference_index(NODES)
    text = (
        "Giá trị quy định tại Bảng 1 và Phụ lục 1 Quy chuẩn này; theo khoản 4 Điều 2 Thông tư này, "
        "Mục 2.1 và Điều 111 Luật Bảo vệ môi trường, Điều 3 Nghị định số 08/2022/NĐ-CP."
    )
    found = dict(enrich.reference_targets(text, "D", index))
    assert found == {
        "D/S2.1/T1": "Bảng 1",
        "D/PL1": "Phụ lục 1",
        "D/A2/K4": "khoản 4 Điều 2",  # the clause, not Article 2 a second time
        "D/S2.1": "Mục 2.1",
    }  # Điều 111 / Điều 3 belong to other legal texts
    assert enrich.reference_targets("xem Bảng 1", "OTHER", index) == []


def test_role_comes_from_the_nearest_heading():
    roles, inherited = enrich.load_vocabulary(get_settings().data_dir)
    assert enrich._role_of("D/A2/K4", NODES, roles) == "effectivity"
    assert enrich._role_of("D/A3/K1", NODES, roles) == "transition"
    assert enrich._role_of("D/PL1", NODES, roles) == "method"
    assert enrich._role_of("D/S2.1/T1", NODES, roles) is None
    assert {"effectivity", "transition", "roadmap"} <= inherited


def test_a_document_can_declare_its_own_role_headings(tmp_path):
    grammar = tmp_path / "decree.yaml"
    grammar.write_text(
        "provision_roles:\n  - { role: effectivity, heading: 'Điều khoản thi hành' }\n", "utf-8"
    )
    own = enrich.document_roles(grammar)
    assert own == [("effectivity", "điều khoản thi hành")]
    nodes = {"X/A9": _node("X/A9", "Article", "9", title="Điều khoản thi hành")}
    common, _ = enrich.load_vocabulary(get_settings().data_dir)
    assert enrich._role_of("X/A9", nodes, common) is None  # unknown to the shared vocabulary
    assert enrich._role_of("X/A9", nodes, own + common) == "effectivity"
    assert enrich.document_roles(None) == [] and enrich.document_roles(tmp_path / "none") == []


def _view():
    v = gc.GraphView(chunk_ids={"c:row", "c:fn", "c:trans", "c:eff", "c:scope", "c:freq", "c:pre"})
    v.documents = {"doc:Q": {"id": "doc:Q", "domain": "wastewater", "type": "qcvn"}}
    v.conditions = [
        {"id": "cond:lo", "type": "flow_regime", "op": "<=", "value": 2000},
        {"id": "cond:hi", "type": "flow_regime", "op": ">", "value": 2000},
        {"id": "cond:coal", "type": "fuel_type", "op": None, "value": "solid"},
    ]
    v.add("rule", "param:COD", "FOR_PARAMETER")
    v.add("rule", "c:row", "HAS_EVIDENCE")
    v.add("c:row", "row", "EXTRACTED_FROM")
    v.add("table", "row", "CONTAINS")
    v.add("table", "c:fn", "TABLE_CONTEXT", walk=False, kind="footnote")
    v.add("table", "c:trans", "EFFECTIVE_UNDER", walk=False, kind="transition")
    v.add("doc:Q", "c:eff", "HAS_PROVISION", walk=False, role="effectivity")
    v.add("doc:Q", "c:scope", "HAS_PROVISION", walk=False, role="scope")
    v.add("doc:OLD", "c:pre", "STATED_IN")
    v.add("doc:Q", "doc:OLD", "REPLACES")
    v.add("req", "topic:wastewater:frequency_pointer", "ABOUT_TOPIC", walk=False)
    v.add("req", "c:freq", "HAS_EVIDENCE")
    return v


def _qu(**kw):
    base = {"question": "?", "domain": "wastewater", "intent": "threshold"}
    return QueryUnderstanding(**(base | kw))


def test_threshold_question_gets_the_row_and_its_table_context():
    parts = gc.candidates(_view(), _qu(parameters=["COD"]))
    assert parts["core"] == {"c:row"}
    assert parts["context"] == {"c:fn", "c:trans"}  # footnote + transition clause of the table
    assert not parts["provisions"] and not parts["topics"]  # the intent does not ask for them
    assert gc.union(parts) == {"c:row", "c:fn", "c:trans"}


def test_provisions_and_topics_follow_the_intent():
    v = _view()
    eff = gc.candidates(v, _qu(intent="effectivity", document_ids=["OLD"]))
    # the named document was replaced: its successor's effectivity provisions, not its scope
    assert eff["provisions"] == {"c:eff"} and "c:pre" in eff["core"]
    scope = gc.candidates(v, _qu(intent="scope"))
    assert scope["provisions"] == {"c:scope"}  # the QCVN of the domain, nothing named
    freq = gc.candidates(v, _qu(intent="frequency"))
    assert freq["topics"] == {"c:freq"} and not freq["provisions"]
    assert not gc.candidates(v, _qu(domain="unknown", intent="scope"))["provisions"]


def test_condition_seeds_pick_the_flow_band_and_the_fuel():
    v = _view()
    assert gc.condition_seeds(v, _qu(flow_m3_day=3000.0)) == ["cond:hi"]
    assert gc.condition_seeds(v, _qu(flow_m3_day=2000.0)) == ["cond:lo"]
    assert gc.condition_seeds(v, _qu(fuel_type="solid")) == ["cond:coal"]
    assert gc.condition_seeds(v, _qu()) == []


def test_cypher_parameters_follow_the_question():
    v = _view()
    p = gc.lookup_params(v, _qu(parameters=["COD"], flow_m3_day=3000.0))
    assert p["seeds"] == ["param:COD", "cond:hi"] and p["roles"] == [] and p["docs"] == []
    eff = gc.lookup_params(v, _qu(intent="effectivity", document_ids=["OLD"]))
    assert eff["docs"] == ["doc:Q"] and "effectivity" in eff["roles"] and eff["topics"] == []
    freq = gc.lookup_params(v, _qu(domain="unknown", intent="frequency"))
    assert freq["topics"] == [
        "topic:wastewater:frequency_pointer",
        "topic:emission:frequency_pointer",
    ]


def test_lookup_only_edges_are_not_walked():
    v = _view()
    assert "c:fn" not in gc.neighbourhood(v.adj, "param:COD", 5)
    assert "c:row" in gc.neighbourhood(v.adj, "param:COD", 2)
