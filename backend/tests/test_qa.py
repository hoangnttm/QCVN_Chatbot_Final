"""Phase 4 gate (offline): validator rules, deterministic policy paths, the LLM path with a
scripted FakeLLM (happy path, reject → regenerate, double reject → abstain), the audit log and
the SSE endpoint. No Gemini call is made here; the live check is `scripts/qa_eval.py`."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.db import models as m
from app.db.loader import load_document
from app.db.session import get_sessionmaker
from app.llm.client import FakeLLM
from app.qa import prompts as P
from app.qa.models import Answer, Basis, QARequest
from app.qa.service import QAService
from app.qa.validator import numbers, validate
from app.registry.loader import load_registry
from app.retrieval.query import QueryUnderstanding
from app.retrieval.retriever import EvidenceItem, RetrievalService

DOCS = ("TT_06_2025", "QCVN_40_2025", "TT_45_2024", "QCVN_19_2024", "TT_10_2021")
CHUNK_RE = re.compile(
    r'<chunk id="([^"]+)" label="([^"]+)" document="([^"]+)"( \[BẢN DỰ THẢO\])?>\n(.*?)\n</chunk>',
    re.S,
)


@pytest.fixture(scope="module")
def service(db_settings, tmp_path_factory):
    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker.begin() as session:
        for doc_id in DOCS:
            load_document(doc_id, reg, db_settings, session)
    svc = RetrievalService(db_settings)
    svc.cache_dir_override = tmp_path_factory.mktemp("bm25")
    with maker() as session:
        svc.ensure(session)
    return svc


@pytest.fixture()
def session(db_settings):
    s = get_sessionmaker(db_settings)()
    yield s
    s.close()


def chunks_in(prompt: str) -> list[dict]:
    return [
        {"id": i, "label": lb, "document": d, "draft": bool(f), "text": t}
        for i, lb, d, f, t in CHUNK_RE.findall(prompt)
    ]


def scripted(*answers):
    """FakeLLM responses: understanding calls get a neutral classification, answer calls are
    served by the given callables (prompt chunks → payload), the last one repeating."""
    calls = {"n": 0}

    def respond(system: str, prompt: str) -> dict:
        if system == P.UNDERSTANDING_SYSTEM:
            return {"domain": "wastewater", "intent": "threshold", "needs_clarification": False}
        fn = answers[min(calls["n"], len(answers) - 1)]
        calls["n"] += 1
        return fn(chunks_in(prompt))

    return FakeLLM([respond])


def ev(chunk_id: str, text: str, **kw) -> EvidenceItem:
    base = dict(
        rank=1,
        chunk_id=chunk_id,
        citation_label="QCVN 40:2025/BTNMT, Mục 2.1, Bảng 1, TT 1",
        document_id="QCVN_40_2025",
        document_code="QCVN 40:2025/BTNMT",
        chunk_type="table_row",
        domain="wastewater",
        text_status="official",
        score=1.0,
        sources=["bm25"],
        text=text,
    )
    base.update(kw)
    return EvidenceItem(**base)


QU = QueryUnderstanding(question="BOD5 cột B?", domain="wastewater", intent="threshold")


# ----------------------------------------------------------------------------- validator


def test_number_normalisation():
    got = numbers("BOD5 ≤ 50 mg/L, lưu lượng 3.000 m3/ngày, 0,05; pH 6,0 – 9; 1.234,5")
    assert {"5", "50", "3000", "0.05", "6", "9", "1234.5"} <= got


def test_validator_rules_1_to_4():
    pack = [ev("c1", "BOD5 … Cột B: 50 mg/L"), ev("c2", "CO 350 mg/Nm3", domain="emission")]
    r, _ = validate(Answer(conclusion="50 mg/L"), pack, QU, "BOD5?")
    assert not r.ok and any("không có citation" in e for e in r.errors)
    r, _ = validate(Answer(conclusion="50 mg/L", citations=["nope"]), pack, QU, "BOD5?")
    assert not r.ok and any("không có trong evidence" in e for e in r.errors)
    r, _ = validate(Answer(conclusion="≤ 60 mg/L", citations=["c1"]), pack, QU, "BOD5?")
    assert not r.ok and "60" in r.errors[0]
    ok = Answer(
        conclusion="BOD5 cột B tối đa 50 mg/L với lưu lượng 3.000 m3/ngày",
        basis=[Basis(text="Bảng 1 TT 1 cột B: 50 mg/L", citations=["c1"])],
    )
    r, _ = validate(ok, pack, QU, "BOD5 cột B lưu lượng 3000 m3/ngày?")
    assert r.ok, r.errors  # 3000 echoes the question, 50 is in the chunk
    r, _ = validate(Answer(conclusion="350 mg/Nm3", citations=["c2"]), pack, QU, "CO?")
    assert not r.ok and any("miền khác" in e for e in r.errors)
    r, a = validate(Answer(conclusion="50 và 350", citations=["c1", "c2"]), pack, QU, "?")
    assert r.ok and any("miền khác" in w for w in a.warnings)


def test_validator_rule_6_draft_and_registry_fact():
    draft = ev(
        "d1",
        "Thông tư này có hiệu lực từ ngày 01/03/2025",
        document_code="TT 45/2024/BTNMT",
        text_status="draft",
        chunk_type="clause",
        domain="emission",
    )
    fact = ev(
        "f1",
        "Ngày hiệu lực: 01/03/2025 (do quản trị viên khai báo)",
        citation_label="Sổ đăng ký văn bản TT 45/2024/BTNMT: ngày hiệu lực (do quản trị viên khai báo, chưa có bản ký)",
        document_code="TT 45/2024/BTNMT",
        text_status="draft",
        chunk_type="registry_fact",
        domain="emission",
    )
    qu = QueryUnderstanding(question="?", domain="emission", intent="effectivity")
    r, a = validate(
        Answer(conclusion="Hiệu lực từ 01/03/2025", citations=["d1"]), [draft, fact], qu, "?"
    )
    assert not r.ok and any("Sổ đăng ký" in e for e in r.errors)
    assert any("dự thảo TT 45/2024" in w for w in a.warnings)
    r, a = validate(
        Answer(conclusion="Hiệu lực từ 01/03/2025", citations=["f1"]), [draft, fact], qu, "?"
    )
    assert r.ok and any("quản trị viên" in w for w in a.warnings)


def test_validator_rule_6_official_text_with_admin_declared_date():
    """Official text whose file leaves the date blank (TT 45/2024 after 2026-09-24): no draft
    warning, but the date must still be cited from the registry-fact chunk."""
    body = ev(
        "b1",
        "Thông tư này có hiệu lực thi hành kể từ ngày tháng năm 2025",
        document_id="TT_45_2024",
        document_code="45/2024/TT-BTNMT",
        text_status="official",
        chunk_type="clause",
        domain="emission",
    )
    fact = ev(
        "f1",
        "45/2024/TT-BTNMT: ngày hiệu lực 2025-07-01.",
        citation_label=(
            "Sổ đăng ký văn bản: 45/2024/TT-BTNMT — ngày hiệu lực 2025-07-01 "
            "(do quản trị viên khai báo ngày 2026-09-23)"
        ),
        document_id="TT_45_2024",
        document_code="45/2024/TT-BTNMT",
        text_status="official",
        chunk_type="registry_fact",
        domain="emission",
    )
    qu = QueryUnderstanding(question="?", domain="emission", intent="effectivity")
    r, a = validate(
        Answer(conclusion="Hiệu lực từ năm 2025", citations=["b1"]), [body, fact], qu, "?"
    )
    assert not r.ok and any("Sổ đăng ký" in e for e in r.errors)
    assert not any("dự thảo" in w for w in a.warnings)
    r, a = validate(
        Answer(conclusion="Hiệu lực từ 2025-07-01", citations=["f1"]), [body, fact], qu, "?"
    )
    assert r.ok and any("quản trị viên" in w and "không in ngày" in w for w in a.warnings)
    assert not any("dự thảo" in w for w in a.warnings)


# ----------------------------------------------------------------------------- policy paths


def test_policy_m01_and_out_of_scope_never_call_llm(service, session):
    llm = FakeLLM([])
    qa = QAService(session, service, llm)
    resp = qa.answer(
        QARequest(question="Tần suất quan trắc nước thải theo QCVN 40:2025 là bao lâu một lần?")
    )
    assert resp.status == "abstained" and resp.path == "policy" and resp.answer.abstained
    assert any("Điều 16, Khoản 3" in c.citation_label for c in resp.citations)
    assert any("M-01" in w for w in resp.answer.warnings) and llm.calls == []
    log = session.get(m.QueryLog, resp.query_id)
    assert log is not None and log.status == "abstained" and log.path == "policy"

    resp2 = qa.answer(QARequest(question="Giới hạn tiếng ồn khu dân cư ban đêm là bao nhiêu?"))
    assert resp2.status == "abstained" and resp2.understanding.domain == "out_of_scope"
    assert "ngoài phạm vi" in resp2.answer.conclusion.lower() and llm.calls == []


# ----------------------------------------------------------------------------- LLM path

Q_BOD = "Giới hạn BOD5 trong nước thải công nghiệp cột B, lưu lượng 3000 m3/ngày là bao nhiêu?"


def good_answer(chunks: list[dict]) -> dict:
    row = next(c for c in chunks if c["id"] == "chunk:QCVN40_2025.B1.r1")
    assert "50" in row["text"]
    return {
        "conclusion": "BOD5 nước thải công nghiệp cột B (F > 2000 m³/ngày): tối đa 50 mg/L.",
        "basis": [{"text": "Bảng 1 TT 1, Cột B, F > 2000: 50 mg/L", "citations": [row["id"]]}],
        "conditions": ["Cột B", "Lưu lượng > 2000 m³/ngày"],
        "citations": [row["id"]],
        "missing_information": [],
        "warnings": [],
        "abstained": False,
        "abstain_reason": None,
    }


def bad_answer(chunks: list[dict]) -> dict:
    return {
        "conclusion": "BOD5 cột B tối đa 999 mg/L.",
        "basis": [],
        "conditions": [],
        "citations": ["chunk:does-not-exist"],
        "missing_information": [],
        "warnings": [],
        "abstained": False,
        "abstain_reason": None,
    }


def test_llm_happy_path_with_audit_and_events(service, session):
    llm = scripted(good_answer)
    events: list[str] = []
    resp = QAService(session, service, llm).answer(
        QARequest(question=Q_BOD), on_event=lambda e, _d: events.append(e)
    )
    assert resp.status == "answered" and resp.path == "llm" and resp.attempts == 1
    assert resp.validation.ok and resp.citations[0].chunk_id == "chunk:QCVN40_2025.B1.r1"
    assert resp.citations[0].citation_label.startswith("QCVN 40:2025/BTNMT, Mục 2.1, Bảng 1, TT 1")
    assert events[:3] == ["stage", "understanding", "evidence"]
    assert "delta" in events and events[-1] == "answer"
    assert len(llm.calls) == 1 and llm.calls[0]["schema"] == "Answer"
    assert "<question>" in llm.calls[0]["prompt"] and P.ANSWER_SYSTEM == llm.calls[0]["system"]
    ans = session.query(m.AnswerRecord).filter_by(query_id=resp.query_id).one()
    cits = session.query(m.AnswerCitation).filter_by(answer_id=ans.id).all()
    assert ans.abstained is False and [c.chunk_id for c in cits] == ["chunk:QCVN40_2025.B1.r1"]
    assert resp.disclaimer.startswith("Thông tin tra cứu")


def test_llm_reject_then_regenerate(service, session):
    llm = scripted(bad_answer, good_answer)
    resp = QAService(session, service, llm).answer(QARequest(question=Q_BOD))
    assert resp.status == "answered" and resp.attempts == 2 and len(llm.calls) == 2
    assert (
        "BỊ TỪ CHỐI" in llm.calls[1]["prompt"] and "chunk:does-not-exist" in llm.calls[1]["prompt"]
    )


def test_llm_double_reject_falls_back_to_abstention(service, session):
    llm = scripted(bad_answer)
    resp = QAService(session, service, llm).answer(QARequest(question=Q_BOD))
    assert resp.status == "failed" and resp.path == "fallback" and resp.answer.abstained
    assert resp.attempts == 2 and any("Validator từ chối" in w for w in resp.answer.warnings)
    assert resp.answer.citations == [] and resp.evidence, "evidence still shown to the user"
    log = session.get(m.QueryLog, resp.query_id)
    assert log.status == "failed" and log.validation["ok"] is False


def test_llm_effectivity_of_admin_declared_circular_needs_registry_fact(service, session):
    """TT 45/2024 is official but its file leaves the dates blank: an answer citing the body must
    be rejected and regenerated so that the date comes from the registry-fact chunk."""
    q = "Thông tư 45/2024/BTNMT có hiệu lực từ ngày nào?"

    def from_body(chunks):
        body = [x for x in chunks if "Sổ đăng ký" not in x["label"]]
        c = next((x for x in body if "45/2024" in x["document"]), body[0])
        return {
            "conclusion": "Có hiệu lực từ 01/01/2030.",
            "citations": [c["id"]],
            "abstained": False,
        }

    def from_registry(chunks):
        c = next(x for x in chunks if "Sổ đăng ký" in x["label"] and "hiệu lực" in x["label"])
        date = re.search(r"\d{4}-\d{2}-\d{2}", c["text"]).group(0)
        return {"conclusion": f"Có hiệu lực từ {date}.", "citations": [c["id"]], "abstained": False}

    llm = scripted(from_body, from_registry)
    resp = QAService(session, service, llm).answer(QARequest(question=q))
    assert resp.understanding.intent == "effectivity"
    assert resp.status == "answered" and resp.attempts == 2
    assert (
        resp.citations[0].text_status == "official"
        and "Sổ đăng ký" in resp.citations[0].citation_label
    )
    assert not any("dự thảo" in w for w in resp.answer.warnings)
    assert any("quản trị viên" in w for w in resp.answer.warnings)


# ----------------------------------------------------------------------------- API (SSE)


def parse_sse(body: str) -> list[tuple[str, str]]:
    out = []
    for frame in body.strip().split("\n\n"):
        ev_line, data = frame.split("\n", 1)
        out.append((ev_line.removeprefix("event: "), data.removeprefix("data: ")))
    return out


def test_ask_sse_feedback_and_logs(service, db_settings, monkeypatch):
    import json

    from app.api.deps import get_db
    from app.core.config import get_settings
    from app.main import app

    maker = get_sessionmaker(db_settings)

    def _db():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr("app.api.questions.get_sessionmaker", lambda: maker)
    app.state.retrieval = service
    app.state.llm = scripted(good_answer)
    try:
        client = TestClient(app)  # no lifespan → app.state stays as set above
        r = client.post(
            "/api/v1/questions/ask", json={"question": Q_BOD, "as_of_date": "2026-01-15"}
        )
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(r.text)
        names = [e for e, _ in events]
        assert names[:3] == ["stage", "understanding", "evidence"] and names[-2:] == [
            "answer",
            "done",
        ]
        assert "delta" in names
        answer = json.loads(dict(events)["answer"])
        assert (
            answer["status"] == "answered"
            and answer["citations"][0]["chunk_id"] == "chunk:QCVN40_2025.B1.r1"
        )

        fb = client.post(
            "/api/v1/feedback", json={"query_id": answer["query_id"], "rating": 1, "comment": "ok"}
        )
        assert fb.status_code == 200
        assert (
            client.post("/api/v1/feedback", json={"query_id": "missing", "rating": 1}).status_code
            == 404
        )
        got = client.get(f"/api/v1/questions/{answer['query_id']}")
        assert got.status_code == 200 and got.json()["answer"]["abstained"] is False
        assert client.get("/api/v1/admin/query-logs?limit=5").status_code in (401, 503)
        monkeypatch.setenv("ADMIN_TOKEN", "t")
        get_settings.cache_clear()
        logs = client.get("/api/v1/admin/query-logs?limit=5", headers={"X-Admin-Token": "t"}).json()
        assert logs["items"] and logs["items"][0]["query_id"] == answer["query_id"]

        plain = client.post("/api/v1/questions/answer", json={"question": Q_BOD})
        assert plain.status_code == 200 and plain.json()["status"] == "answered"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_eval_options_observe_mode_and_audit_off(service, session):
    """Evaluation knobs: observe mode keeps a rejected answer (no regeneration, enforced=False),
    audit=False writes nothing, ranking is returned, and API payloads cannot reach the options."""
    from app.qa.models import EvalOptions, RetrievalOptions

    llm = scripted(bad_answer)
    before = session.query(m.QueryLog).count()
    resp = QAService(session, service, llm).answer(
        QARequest(question=Q_BOD),
        options=EvalOptions(
            validator_mode="observe",
            audit=False,
            return_ranking=True,
            retrieval=RetrievalOptions(use_vector=False),
        ),
    )
    assert resp.attempts == 1 and len(llm.calls) == 1 and resp.path == "llm"
    assert resp.validation.ok is False and resp.validation.enforced is False
    assert any("EVAL-ONLY" in w for w in resp.answer.warnings)
    assert resp.ranking and resp.ranking[0].chunk_id == resp.evidence[0].chunk_id
    assert session.query(m.QueryLog).count() == before
    req = QARequest.model_validate({"question": Q_BOD, "options": {"validator_mode": "observe"}})
    assert not hasattr(req, "options")
    # production path unchanged: enforced validator regenerates once
    llm2 = scripted(bad_answer, good_answer)
    resp2 = QAService(session, service, llm2).answer(QARequest(question=Q_BOD))
    assert resp2.attempts == 2 and resp2.validation.enforced is True
