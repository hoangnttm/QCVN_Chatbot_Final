"""A network timeout is not an APIError: it must come back as an LLM error (so the QA service can
fall back), never escape and abort the request or an evaluation run."""

import httpx
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.client import GeminiClient


class _Out(BaseModel):
    ok: bool


class _Models:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def generate_content(self, **_kw):
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ReadTimeout("The read operation timed out")

        class _Resp:
            text = '{"ok": true}'
            usage_metadata = None

        return _Resp()


def _client(failures: int, max_retries: int, monkeypatch) -> tuple[GeminiClient, _Models]:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    client = GeminiClient(Settings(_env_file=None, gemini_api_key="test"), max_retries=max_retries)
    models = _Models(failures)
    client._client = type("C", (), {"models": models})()
    return client, models


def test_timeout_is_retried_then_succeeds(monkeypatch):
    client, models = _client(failures=2, max_retries=3, monkeypatch=monkeypatch)
    r = client.generate_json("sys", "prompt", _Out, model="gemini-test")
    assert r.error is None and r.parsed.ok and models.calls == 3


def test_timeout_after_the_last_retry_is_reported_not_raised(monkeypatch):
    client, models = _client(failures=5, max_retries=1, monkeypatch=monkeypatch)
    r = client.generate_json("sys", "prompt", _Out, model="gemini-test")
    assert r.parsed is None and r.error == "transport: ReadTimeout" and models.calls == 2
