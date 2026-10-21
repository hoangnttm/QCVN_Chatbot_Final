"""Plan step 3.4: sub-questions (rules only), candidate facts and hops (need the loaded fact
store; skipped when the working database is not reachable or holds no rules)."""

from pathlib import Path

import pytest
import yaml

from app.core.config import get_settings
from app.ontology.corpus import Corpus, DocumentSpec
from app.ontology.loader import load_dictionaries
from app.retrieval.frame import FrameBuilder, Slot
from app.retrieval.tokenize import AliasIndex

REGISTRY = Path(__file__).resolve().parents[2] / "data" / "registry" / "documents.yaml"


@pytest.fixture(scope="module")
def builder() -> FrameBuilder:
    d = load_dictionaries()
    corpus = Corpus.from_dictionaries(d)
    docs = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["documents"]
    for doc in docs:
        corpus.documents[doc["document_id"]] = DocumentSpec(
            doc["document_id"], doc["document_code"], doc["document_type"], doc["domain"]
        )
    codes = {doc["document_id"]: doc["document_code"] for doc in docs}
    return FrameBuilder(d, AliasIndex(d, codes), corpus, corpus.document_domains())


@pytest.fixture(scope="module")
def resolver():
    from sqlalchemy import func, select
    from sqlalchemy.exc import SQLAlchemyError

    from app.db import models as m
    from app.db.session import get_sessionmaker
    from app.retrieval.hops import FrameResolver
    from app.retrieval.retriever import RetrievalService

    settings = get_settings()
    try:
        session = get_sessionmaker(settings)()
        if not session.execute(select(func.count()).select_from(m.RuleCondition)).scalar():
            pytest.skip("fact store is empty")
        svc = RetrievalService(settings)
        svc.ensure(session)
    except SQLAlchemyError as e:
        pytest.skip(f"no database: {type(e).__name__}")
    d = load_dictionaries(settings=settings)
    builder = FrameBuilder(d, svc.aliases, svc.corpus, svc.doc_domains)
    return FrameResolver(session, d, builder, svc.corpus)


# ---------------------------------------------------------------- sub-questions (no database)


def test_split_by_domain_with_shared_context(builder):
    wastewater, emission = builder.corpus.domain_codes()[:2]
    f = builder.build(
        "Địa phương chưa xác định phân vùng thì nước thải công nghiệp và khí thải công nghiệp áp"
        " dụng cột nào của hai quy chuẩn?"
    )
    assert [s.domain for s in f.sub_questions] == [wastewater, emission]
    assert all("applicability" in s.intents for s in f.sub_questions)
    assert f.domain == "cross_domain" and f.relation == "sequence"


def test_split_two_parts_each_with_its_own_column(builder):
    wastewater, emission = builder.corpus.domain_codes()[:2]
    f = builder.build(
        "So sánh giúp tôi hai trường hợp. COD trong nước thải công nghiệp cột A, lưu lượng 1.500"
        " m3/ngày là bao nhiêu? Lò hỏa táng cột B thì bụi tối đa bao nhiêu?"
    )
    assert f.relation == "compare"
    subs = {s.domain: s for s in f.sub_questions}
    assert set(subs) == {wastewater, emission}

    def values(sub, dim):
        return [x.value for x in sub.slots if x.dimension == dim]

    assert values(subs[wastewater], "zone") == ["A"] and values(subs[emission], "zone") == ["B"]
    assert values(subs[wastewater], "parameter") == ["COD"]
    assert values(subs[emission], "equipment") == ["lo_hoa_tang"]
    assert values(subs[emission], "flow") == []  # the flow belongs to the wastewater part


def test_questions_that_stay_whole(builder):
    # two values of one slot are one question
    f = builder.build("COD và BOD5 trong nước thải công nghiệp cột A là bao nhiêu?")
    assert not f.sub_questions and len(f.values("parameter")) == 2
    # a second sentence about the same cell is a second intent, not a second question
    f = builder.build(
        "Giới hạn SO2 của lò hỏa táng (cột B) là bao nhiêu? Con số trong ngoặc ở cột này có nghĩa"
        " là gì?"
    )
    assert not f.sub_questions and "definition" in f.intents
    # a label that holds a semicolon is not a clause boundary
    f = builder.build(
        "Theo quy chuẩn khí thải, thiết bị sản xuất, sử dụng hóa chất chứa Brom; thiết bị xả thải"
        " khác thuộc cột B thì Brom không được vượt quá bao nhiêu?"
    )
    assert not f.sub_questions


# ---------------------------------------------------------------- candidates (fact store)


def S(dimension: str, value: str, unit: str | None = None) -> Slot:
    return Slot(dimension=dimension, value=value, unit=unit)


def test_missing_facts_follow_the_number_of_answers(resolver):
    store = resolver.store
    wastewater, emission = resolver.corpus.domain_codes()[:2]
    # three cells, one value: nothing is missing
    c = store.resolve([S("parameter", "pH")], wastewater)
    assert (c.mode, c.n_candidates, c.n_answers, c.missing) == ("answer", 3, 1, [])
    # a stated flow says nothing about a parameter that does not depend on the flow
    c = store.resolve([S("parameter", "pH"), S("flow", "2500", "m3_per_day")], wastewater)
    assert c.mode == "answer"
    # up to three answers are listed, each with what tells it apart and its legal address
    c = store.resolve([S("parameter", "BOD5"), S("flow", "1500", "m3_per_day")], wastewater)
    assert (c.mode, c.n_answers, c.missing) == ("list", 3, ["zone"])
    assert sorted(o.conditions["zone"] for o in c.options) == ["A", "B", "C"]
    assert all(o.legal_addresses for o in c.options)
    # more than three: ask, the dimension that separates best first
    c = store.resolve([S("parameter", "COD")], wastewater)
    assert (c.mode, c.missing, c.ask_choices) == ("ask", ["zone", "flow"], ["A", "B", "C"])
    c = store.resolve([S("parameter", "SO2"), S("zone", "A")], emission)
    assert c.mode == "ask" and c.missing == ["equipment"]
    # a parameter of two domains without a domain: the domain is asked first
    c = store.resolve([S("parameter", "Pb")], None)
    assert c.mode == "ask" and c.missing[0] == "domain"
    # fully stated; a value no row has; no parameter at all
    slots = [S("parameter", "COD"), S("zone", "A"), S("flow", "2500", "m3_per_day")]
    assert store.resolve(slots, wastewater).mode == "answer"
    c = store.resolve([S("parameter", "COD"), S("zone", "D")], wastewater)
    assert (c.mode, c.emptied_by) == ("none_found", "zone")
    assert store.resolve([S("zone", "A")], wastewater).mode == "not_applicable"


# ---------------------------------------------------------------- hops (fact store)


def test_default_column_is_filled_from_its_clause(resolver):
    f = resolver.understand(
        "Chưa xác định được phân vùng xả thải của cơ sở thì Asen (As) trong nước thải công nghiệp"
        " tối đa là bao nhiêu?"
    )
    (hop,) = [h for h in f.hops if h.type == "default_value"]
    assert hop.status == "resolved" and hop.effect == {"slot": "zone", "value": "B"}
    assert hop.legal_addresses[0].endswith("Điều 2, Khoản 4")
    assert f.clarification["mode"] == "answer" and not f.missing  # the column is not missing
    # the same question with a column named needs no default
    f = resolver.understand("Asen (As) trong nước thải công nghiệp cột A tối đa là bao nhiêu?")
    assert not [h for h in f.hops if h.type == "default_value"]


def test_replaced_document_roadmap_and_footnote(resolver):
    f = resolver.understand(
        "Tôi vẫn đang dùng QCVN 40:2011/BTNMT. Quy chuẩn nào thay thế nó và giới hạn Niken (cột A)"
        " mới là bao nhiêu?"
    )
    (hop,) = [h for h in f.hops if h.type == "replacement"]
    assert hop.status == "resolved" and hop.effect["scope"] == "full"
    assert hop.effect["to"] in resolver.store.documents
    assert f.clarification["mode"] == "answer"

    f = resolver.understand(
        "Cơ sở đã đi vào vận hành trước đó. Kể từ ngày 01/01/2032, Niken (Ni) trong nước thải công"
        " nghiệp ở Cột B phải đáp ứng mức nào?"
    )
    (hop,) = [h for h in f.hops if h.type == "roadmap"]
    assert hop.status == "resolved" and hop.legal_addresses[0].endswith("Điều 4, Khoản 2")
    assert hop.supporting_chunk_ids  # the clause "khoản 1 Điều 3" it refers to

    f = resolver.understand(
        "Giới hạn bụi của lò hỏa táng (cột B) là bao nhiêu? Con số trong ngoặc ở cột này có nghĩa"
        " là gì?"
    )
    (hop,) = [h for h in f.hops if h.type == "footnote"]
    assert hop.status == "resolved" and all("Footnote" in c for c in hop.chunk_ids)


def test_described_column_and_unresolved_hop(resolver):
    f = resolver.understand(
        "Cơ sở có địa điểm hoạt động trong vùng bảo vệ nghiêm ngặt. Giới hạn SO2 của lò hỏa táng"
        " là bao nhiêu?"
    )
    (hop,) = [h for h in f.hops if h.type == "zone_definition"]
    assert hop.status == "resolved" and f.values("zone") == ["A"]
    # the domain is not known: the roadmap cannot be chosen, and the frame says so
    f = resolver.understand("Nhà máy đang hoạt động từ trước năm 2025 thì từ 01/01/2032 As cột C?")
    assert [h.status for h in f.hops if h.type == "roadmap"] == ["unresolved"]
    assert "hop:unresolved" in f.unsure and f.missing[0] == "domain"


# ---------------------------------------------------------------- constrained LLM path (fake LLM)


def _assist(resolver, responses, budget=None):
    from app.llm.client import FakeLLM
    from app.retrieval.frame_llm import LLMAssist

    llm = FakeLLM(responses)
    return LLMAssist(
        resolver, llm, repair_model="fake", choice_model="fake", budget_usd=budget
    ), llm


QUESTION = (
    "CO của lò hơi đốt than công suất 25 tấn hơi/giờ ở vùng hạn chế phát thải giới hạn bao nhiêu?"
)


def test_llm_choice_only_narrows_and_must_quote_the_question(resolver):
    rules = resolver.understand(QUESTION)
    n = rules.clarification["n_candidates"]
    assert rules.clarification["mode"] == "ask" and n > 1  # the rules cannot read the equipment

    # a row of the list, justified by words of the question: the set is narrowed, not replaced
    assist, llm = _assist(resolver, [{"chosen": [1], "evidence": ["lò hơi đốt than"]}])
    frame, info = assist.understand(QUESTION)
    assert frame.clarification["mode"] == "answer" and frame.clarification["narrowed_by"] == "llm"
    assert frame.clarification["rule_codes"] == [rules.clarification["rule_codes"][0]]
    assert [c["kind"] for c in info["calls"]] == ["choice"] and len(llm.calls) == 1

    # a quote that is not in the question, a row outside the list, or every row: nothing changes
    for bad in (
        {"chosen": [1], "evidence": ["lò hơi tầng sôi tuần hoàn"]},
        {"chosen": [n + 5], "evidence": ["lò hơi đốt than"]},
        {"chosen": list(range(1, n + 1)), "evidence": ["lò hơi đốt than"]},
        {"chosen": [], "evidence": []},
    ):
        assist, _ = _assist(resolver, [bad])
        frame, _ = assist.understand(QUESTION)
        assert frame.clarification["mode"] == "ask" and "narrowed_by" not in frame.clarification


def test_llm_is_not_called_when_the_rules_are_sure_and_stops_at_the_budget(resolver):
    from app.retrieval.frame_llm import BudgetExceeded

    assist, llm = _assist(resolver, [{"chosen": [1], "evidence": ["x"]}])
    frame, info = assist.understand(
        "COD trong nước thải công nghiệp cột A, 2.500 m3/ngày là bao nhiêu?"
    )
    assert frame.clarification["mode"] == "answer" and not info["calls"] and not llm.calls
    assist, llm = _assist(resolver, [{"chosen": [], "evidence": []}], budget=0.0)
    with pytest.raises(BudgetExceeded):
        assist.understand(QUESTION)
    assert not llm.calls


def test_llm_repair_gives_a_domain_only_from_the_list(resolver):
    question = "Đất trong khuôn viên nhà máy được phép có hàm lượng chì tối đa bao nhiêu?"
    assert resolver.builder.build(question).domain == "unknown"
    assist, _ = _assist(
        resolver,
        [
            {
                "domain": "out_of_scope",
                "intents": ["threshold"],
                "evidence": ["Đất trong khuôn viên"],
            }
        ],
    )
    frame, info = assist.understand(question)
    assert frame.domain == "out_of_scope" and frame.clarification is None
    assert [c["kind"] for c in info["calls"]] == ["repair"]
    # a domain the ontology does not have is refused by the schema: the frame stays as it was
    assist, _ = _assist(
        resolver, [{"domain": "soil", "intents": ["threshold"], "evidence": ["Đất"]}]
    )
    frame, _ = assist.understand(question)
    assert frame.domain == "unknown"
