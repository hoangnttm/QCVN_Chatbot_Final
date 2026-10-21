"""Shared helpers for API tests: a TestClient wired to the throwaway database, a loaded
RetrievalService and an optional fake LLM; admin token headers; scripted LLM answers."""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.loader import load_document
from app.db.session import get_sessionmaker
from app.llm.client import FakeLLM
from app.qa import prompts as P
from app.registry.loader import load_registry
from app.retrieval.retriever import RetrievalService

DOCS = ("TT_06_2025", "QCVN_40_2025", "TT_45_2024", "QCVN_19_2024", "TT_10_2021")
ADMIN = {"X-Admin-Token": "t"}
CLIENT_A = {"X-Client-Id": "11111111-1111-4111-8111-111111111111"}
CLIENT_B = {"X-Client-Id": "22222222-2222-4222-8222-222222222222"}
Q_BOD = "Giới hạn BOD5 trong nước thải công nghiệp cột B, lưu lượng 3000 m3/ngày là bao nhiêu?"
Q_FREQ = "Tần suất quan trắc nước thải theo QCVN 40:2025 là bao lâu một lần?"
CHUNK_RE = re.compile(
    r'<chunk id="([^"]+)" label="([^"]+)" document="([^"]+)"( \[BẢN DỰ THẢO\])?>\n(.*?)\n</chunk>',
    re.S,
)


def load_service(db_settings: Settings, cache_dir) -> RetrievalService:
    reg = load_registry(settings=db_settings)
    maker = get_sessionmaker(db_settings)
    with maker.begin() as session:
        for doc_id in DOCS:
            load_document(doc_id, reg, db_settings, session)
    svc = RetrievalService(db_settings)
    svc.cache_dir_override = cache_dir
    with maker() as session:
        svc.ensure(session)
    return svc


def chunks_in(prompt: str) -> list[dict[str, Any]]:
    return [
        {"id": i, "label": lb, "document": d, "draft": bool(f), "text": t}
        for i, lb, d, f, t in CHUNK_RE.findall(prompt)
    ]


def good_answer(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    row = next(c for c in chunks if c["id"] == "chunk:QCVN40_2025.B1.r1")
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


def bad_answer(chunks: list[dict[str, Any]]) -> dict[str, Any]:
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


def scripted(*answers) -> FakeLLM:
    calls = {"n": 0}

    def respond(system: str, prompt: str) -> dict[str, Any]:
        if system == P.UNDERSTANDING_SYSTEM:
            return {"domain": "wastewater", "intent": "threshold", "needs_clarification": False}
        fn = answers[min(calls["n"], len(answers) - 1)]
        calls["n"] += 1
        return fn(chunks_in(prompt))

    return FakeLLM([respond])


class ExplodingLLM:
    """Simulates a Gemini outage: every call raises."""

    def __init__(self, message: str = "GEMINI 503 unavailable") -> None:
        self.message = message
        self.calls = 0

    def generate_json(self, *args: Any, **kwargs: Any):
        self.calls += 1
        raise RuntimeError(self.message)


def build_client(db_settings: Settings, service, llm, monkeypatch) -> TestClient:
    """TestClient without lifespan (app.state stays as set here); admin token = "t"."""
    from app.api.deps import get_db
    from app.main import app
    from app.ops.state import STATE

    monkeypatch.setenv("ADMIN_TOKEN", "t")
    monkeypatch.setenv("ALERTS_ENABLED", "false")
    monkeypatch.setenv("DATABASE_URL", db_settings.database_url)
    get_settings.cache_clear()
    STATE.reset()
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
    app.state.llm = llm
    return TestClient(app)


def teardown_client() -> None:
    from app.main import app

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def parse_sse(body: str) -> list[tuple[str, str]]:
    out = []
    for frame in body.strip().split("\n\n"):
        ev_line, data = frame.split("\n", 1)
        out.append((ev_line.removeprefix("event: "), data.removeprefix("data: ")))
    return out
