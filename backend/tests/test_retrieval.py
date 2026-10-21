"""Phase 2 gate (offline part): tokenizer + alias expansion, rule-based routing on the gold set,
structured fast path, BM25 retrieval and graph expansion on the throwaway database. The vector
leg needs the Gemini key and stored embeddings → covered by the live test at the end."""

import json
import os

import pytest

from app.core.config import Settings
from app.db.loader import load_document
from app.db.session import get_sessionmaker
from app.registry.loader import load_registry
from app.retrieval.query import understand
from app.retrieval.retriever import RetrievalService, Retriever, SearchRequest
from app.retrieval.structured import lookup
from app.retrieval.tokenize import tokenize

DOCS = ("TT_06_2025", "QCVN_40_2025", "TT_45_2024", "QCVN_19_2024", "TT_10_2021")


@pytest.fixture(scope="module")
def service(db_settings, tmp_path_factory):
    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker.begin() as session:
        for doc_id in DOCS:
            load_document(doc_id, reg, db_settings, session)
    svc = RetrievalService(db_settings)
    svc.cache_dir_override = tmp_path_factory.mktemp("bm25")  # keep test caches out of data/
    with maker() as session:
        svc.ensure(session)
    return svc


@pytest.fixture()
def session(db_settings):
    s = get_sessionmaker(db_settings)()
    yield s
    s.close()


def gold(settings: Settings) -> list[dict]:
    path = settings.data_dir / "gold" / "gold_qa_v1.jsonl"
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_tokenizer_and_aliases(service):
    toks = tokenize("Giới hạn BOD5 trong nước thải công nghiệp cột B, lưu lượng 3000 m3/ngày?")
    assert "nước_thải" in toks and "nước" in toks and "bod5" in toks and "3000" in toks
    qu = understand(
        "Nhu cầu oxy sinh hóa cột B lưu lượng 3.000 m3/ngày là bao nhiêu?", service.aliases
    )
    assert qu.parameters == ["BOD5"] and qu.zone == "B" and qu.flow_m3_day == 3000
    assert qu.domain == "wastewater" and qu.intent == "threshold" and "param_bod5" in qu.expansions
    qu2 = understand(
        "CO của lò hơi đốt than 25 tấn hơi/giờ ở vùng hạn chế phát thải?", service.aliases
    )
    assert qu2.parameters == ["CO"] and qu2.fuel_type == "solid" and qu2.zone == "B"
    assert (
        qu2.capacity_value == 25
        and qu2.capacity_unit == "t_steam_per_h"
        and qu2.domain == "emission"
    )
    qu3 = understand("Tần suất quan trắc nước thải là bao lâu một lần?", service.aliases)
    assert qu3.intent == "frequency" and qu3.policy == "M-01"


def test_zone_negation(service):
    def zone(q):
        return understand(q, service.aliases).zone

    assert zone("Giới hạn COD cột B là bao nhiêu?") == "B"
    # two zones excluded -> the question describes the third
    assert (
        zone(
            "Nhà máy xả ra nguồn nước tiếp nhận không dùng cấp nước sinh hoạt và không thuộc "
            "Mức A, Mức B của QCVN 08:2023/BTNMT. Giới hạn Tổng Crom trong nước thải là bao nhiêu?"
        )
        == "C"
    )
    assert (
        zone(
            "Cơ sở có địa điểm hoạt động không thuộc vùng bảo vệ nghiêm ngặt hay vùng hạn chế "
            "phát thải. Giới hạn As trong khí thải là bao nhiêu?"
        )
        == "C"
    )
    # one zone excluded: not enough to tell; a contrast after the negation is the answer
    assert zone("Cơ sở không thuộc cột A thì giới hạn COD là bao nhiêu?") is None
    assert zone("Cơ sở không thuộc cột A mà thuộc cột B, giới hạn COD là bao nhiêu?") == "B"
    # the negation ends with its sentence
    assert zone("Cơ sở không thuộc khu công nghiệp. Giới hạn COD cột A là bao nhiêu?") == "A"


def test_routing_on_gold(service, db_settings):
    rows = gold(db_settings)
    wrong = []
    for q in rows:
        qu = understand(q["question"], service.aliases, doc_domains=service.doc_domains)
        ok = qu.domain == q["domain"] or (
            q["domain"] in ("wastewater", "emission") and qu.domain == "cross_domain"
        )
        if not ok:
            wrong.append((q["id"], q["domain"], qu.domain))
        if q.get("must_abstain") and q["intent"] == "frequency":
            assert qu.policy == "M-01", q["id"]
    assert wrong == [], wrong


def test_structured_fast_path(service, session):
    qu = understand("Giới hạn BOD5 cột B, lưu lượng 3000 m3/ngày?", service.aliases)
    hits, _ = lookup(session, qu)
    assert [h.rule_code for h in hits] == ["QCVN40_2025.B1.r1.F>2000.B"] and hits[0].value == 50
    qu = understand(
        "CO của lò hơi đốt than công suất 25 tấn hơi/giờ ở vùng hạn chế phát thải?", service.aliases
    )
    hits, warnings = lookup(session, qu)
    assert hits[0].rule_code == "QCVN19_2024.B1.r2.1.2.1.B" and hits[0].value == 350
    assert all(h.zone == "B" for h in hits)
    assert not any("không khớp" in w or "không thỏa" in w for w in warnings)
    qu = understand(
        "Tổng Phốt pho khi xả ra sông đối với nhà máy chế biến thủy sản, cột A?", service.aliases
    )
    hits, _ = lookup(session, qu)
    rows = {h.row_id for h in hits}
    assert "QCVN40_2025.B2.r4.2" in rows and "QCVN40_2025.B2.r4.1" not in rows


def test_bm25_hybrid_offline_recall(service, session, db_settings):
    r = Retriever(session, service)
    misses = []
    for q in gold(db_settings):
        if not q.get("expected_citation_labels"):
            continue
        as_of = q.get("as_of_date")
        res = r.search(
            SearchRequest(question=q["question"], k=10, use_vector=False, as_of_date=as_of)
        )
        labels = [e.citation_label for e in res.evidence]
        hit = any(
            lbl == e or e.startswith(lbl) or lbl.startswith(e)
            for e in q["expected_citation_labels"]
            for lbl in labels
        )
        if not hit:
            misses.append((q["id"], labels[:3]))
        assert res.latency_ms["total"] < 1500
    assert len(misses) <= 2, (
        misses
    )  # BM25 + structured + expansion alone: ≥ 9/11 gold labels in top-10


def test_expansion_and_policy(service, session):
    r = Retriever(session, service)
    res = r.search(
        SearchRequest(
            question="Tần suất quan trắc nước thải theo QCVN 40:2025 là bao lâu một lần?",
            use_vector=False,
        )
    )
    assert res.understanding.policy == "M-01"
    assert any("Điều 16, Khoản 3" in e.citation_label for e in res.evidence)
    assert any(w.startswith("Tần suất quan trắc") for w in res.warnings)
    res2 = r.search(
        SearchRequest(
            question="CO của lò hơi đốt than công suất 25 tấn hơi/giờ ở vùng hạn chế phát thải giới hạn bao nhiêu?",
            use_vector=False,
        )
    )
    assert res2.evidence[0].chunk_type == "table_row" and "structured" in res2.evidence[0].sources
    assert any(e.chunk_type == "footnote" for e in res2.evidence), "table footnote expanded"
    res3 = r.search(
        SearchRequest(question="QCVN 19:2024/BTNMT có hiệu lực từ ngày nào?", use_vector=False)
    )
    assert any(e.chunk_type == "registry_fact" for e in res3.evidence)
    assert not any("dự thảo" in w for w in res3.warnings), "all five texts official (2026-09-24)"


@pytest.mark.skipif(os.environ.get("QCVN_LIVE_TESTS") != "1", reason="set QCVN_LIVE_TESTS=1")
def test_hybrid_live_on_real_db():
    settings = Settings()
    svc = RetrievalService(settings)
    with get_sessionmaker(settings)() as session:
        r = Retriever(session, svc)
        res = r.search(SearchRequest(question="Giới hạn BOD5 cột B lưu lượng 3000 m3/ngày?", k=8))
    assert res.evidence[0].citation_label.startswith("QCVN 40:2025/BTNMT, Mục 2.1, Bảng 1, TT 1")
    assert "vector" in {s for e in res.evidence for s in e.sources}
    assert res.latency_ms["total"] < 1500


def test_retrieval_flags_for_ablation(service, session):
    """Evaluation switches change the pipeline; defaults leave the evidence untouched."""
    r = Retriever(session, service)
    q = "Giới hạn BOD5 trong nước thải công nghiệp cột B, lưu lượng 3000 m3/ngày là bao nhiêu?"
    base = r.search(SearchRequest(question=q, k=10, use_vector=False, return_ranking=True))
    default = r.search(SearchRequest(question=q, k=10, use_vector=False))
    assert [e.chunk_id for e in default.evidence] == [e.chunk_id for e in base.evidence]
    assert base.ranking and len(base.ranking) <= 50 and default.ranking == []
    assert base.ranking[0].chunk_id == base.evidence[0].chunk_id
    no_struct = r.search(SearchRequest(question=q, k=10, use_vector=False, use_structured=False))
    assert no_struct.structured_hits == []
    assert not any("structured" in e.sources for e in no_struct.evidence)
    no_exp = r.search(SearchRequest(question=q, k=10, use_vector=False, expand=False))
    assert not any(s.startswith("expansion:") for e in no_exp.evidence for s in e.sources)
    no_flt = r.search(
        SearchRequest(question=q, k=10, use_vector=False, domain_filter=False, rrf_k=10)
    )
    assert no_flt.evidence


def test_structured_cap_and_llm_rerank(service, session):
    """R0' boosts only the first N structured rows; R3 reorders the head of the fused list without
    adding or dropping candidates, and a failing reranker leaves the RRF ranking untouched."""
    import re

    from app.llm.client import FakeLLM

    r = Retriever(session, service)
    q = "CO của lò hơi đốt than công suất 25 tấn hơi/giờ ở vùng hạn chế phát thải?"
    kw = {"question": q, "k": 10, "use_vector": False, "expand": False, "return_ranking": True}
    base = r.search(SearchRequest(**kw))
    capped = r.search(SearchRequest(**kw, structured_cap=1))
    n_struct = lambda res: sum("structured" in c.sources for c in res.ranking)  # noqa: E731
    assert n_struct(capped) == 1 and n_struct(base) >= 1
    # the most specific row (lookup order) ranks first once the other rows lose their +1.0
    assert capped.ranking[0].chunk_id == capped.structured_hits[0].chunk_id

    def reverse(_s, prompt):  # grade the last candidate 2, the first 0, the rest neutral
        ids = re.findall(r'<c id="([^"]+)"', prompt)
        return {"grades": [{"id": ids[-1], "grade": 2}, {"id": ids[0], "grade": 0}]}

    try:
        service.rerank_llm_override, service._reranker = FakeLLM([reverse]), None
        rr = r.search(SearchRequest(**kw, rerank="llm", rerank_top_n=10))
        head = [c.chunk_id for c in base.ranking[:10]]
        assert [c.chunk_id for c in rr.ranking[:10]] == [head[-1], *head[1:-1], head[0]]
        assert [c.chunk_id for c in rr.ranking[10:]] == [c.chunk_id for c in base.ranking[10:]]
        assert rr.rerank and rr.rerank["candidates"] == 10 and "rerank" in rr.latency_ms
        assert rr.ranking[0].rerank_grade == 2 and rr.evidence[0].chunk_id == head[-1]

        service.rerank_llm_override, service._reranker = FakeLLM(["not json"]), None
        failed = r.search(SearchRequest(**kw, rerank="llm"))
        assert [c.chunk_id for c in failed.ranking] == [c.chunk_id for c in base.ranking]
        assert any(w.startswith("rerank unavailable") for w in failed.warnings)
    finally:
        service.rerank_llm_override, service._reranker = None, None
