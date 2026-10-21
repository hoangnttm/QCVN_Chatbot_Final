"""LLM client abstraction (ADR-0011): Gemini structured output with optional streaming, plus a
deterministic fake for tests. The QA layer never depends on a vendor SDK directly."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from app.core.config import Settings, get_settings

RETRYABLE_HTTP = (429, 500, 502, 503, 504)


@dataclass
class LLMResult:
    text: str
    parsed: Any | None
    model: str
    thinking_level: str | None
    usage: dict[str, int | None] = field(default_factory=dict)
    latency_s: float = 0.0
    error: str | None = None


class LLMClient(Protocol):
    def generate_json(
        self,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        *,
        model: str | None = None,
        thinking_level: str | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult: ...


class GeminiClient:
    def __init__(self, settings: Settings | None = None, max_retries: int = 3) -> None:
        self.settings = settings or get_settings()
        if not self.settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (backend/.env)")
        from google import genai
        from google.genai import types

        self._client = genai.Client(
            api_key=self.settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=int(self.settings.gemini_timeout_s) * 1000),
        )
        self.max_retries = max_retries

    def generate_json(
        self,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        *,
        model: str | None = None,
        thinking_level: str | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        from google.genai import errors, types

        model = model or self.settings.gemini_answer_model
        level = thinking_level or self.settings.gemini_answer_thinking
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=level)
            if level and model.startswith("gemini-3")
            else None,
        )
        t0 = time.perf_counter()
        delay = 3.0
        attempt = 0
        while True:
            try:
                if on_delta is not None:
                    text_parts: list[str] = []
                    usage: dict[str, int | None] = {}
                    for chunk in self._client.models.generate_content_stream(
                        model=model, contents=prompt, config=cfg
                    ):
                        piece = chunk.text or ""
                        if piece:
                            text_parts.append(piece)
                            on_delta(piece)
                        if chunk.usage_metadata is not None:
                            usage = _usage(chunk.usage_metadata)
                    text = "".join(text_parts)
                else:
                    resp = self._client.models.generate_content(
                        model=model, contents=prompt, config=cfg
                    )
                    text = resp.text or ""
                    usage = _usage(resp.usage_metadata)
                break
            except errors.APIError as exc:
                if exc.code in RETRYABLE_HTTP and attempt < self.max_retries:
                    attempt += 1
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                return LLMResult(
                    text="",
                    parsed=None,
                    model=model,
                    thinking_level=level,
                    latency_s=time.perf_counter() - t0,
                    error=f"{exc.code}: {str(exc)[:200]}",
                )
            except httpx.TransportError as exc:
                # read timeout / dropped connection: not an APIError, so it used to escape and
                # abort the request (and a whole evaluation run). Same policy as a 5xx.
                if attempt < self.max_retries:
                    attempt += 1
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                return LLMResult(
                    text="",
                    parsed=None,
                    model=model,
                    thinking_level=level,
                    latency_s=time.perf_counter() - t0,
                    error=f"transport: {type(exc).__name__}",
                )
        latency = time.perf_counter() - t0
        try:
            parsed = schema.model_validate_json(text)
            err = None
        except Exception as e:  # noqa: BLE001
            parsed, err = None, f"invalid JSON: {str(e)[:200]}"
        return LLMResult(
            text=text,
            parsed=parsed,
            model=model,
            thinking_level=level,
            usage=usage,
            latency_s=latency,
            error=err,
        )


def _usage(u: Any) -> dict[str, int | None]:
    return {
        "prompt_tokens": getattr(u, "prompt_token_count", None),
        "output_tokens": getattr(u, "candidates_token_count", None),
        "thinking_tokens": getattr(u, "thoughts_token_count", None),
        "total_tokens": getattr(u, "total_token_count", None),
    }


class FakeLLM:
    """Scripted responses for tests: `responses` is a list of dicts (JSON payloads) or callables
    receiving (system, prompt) and returning a dict; consumed in order, last one repeats."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def generate_json(
        self,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        *,
        model: str | None = None,
        thinking_level: str | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResult:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema.__name__})
        spec = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        payload = spec(system, prompt) if callable(spec) else spec
        text = json.dumps(payload, ensure_ascii=False)
        if on_delta is not None:
            for i in range(0, len(text), 40):
                on_delta(text[i : i + 40])
        try:
            parsed = schema.model_validate(payload)
            err = None
        except Exception as e:  # noqa: BLE001
            parsed, err = None, f"invalid JSON: {str(e)[:200]}"
        return LLMResult(
            text=text,
            parsed=parsed,
            model="fake",
            thinking_level=thinking_level,
            usage={"prompt_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0},
            error=err,
        )


def get_llm(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    if settings.llm_provider == "gemini":
        return GeminiClient(settings)
    raise NotImplementedError(f"LLM_PROVIDER={settings.llm_provider!r} is not implemented yet")


def iter_deltas(text: str, size: int = 80) -> Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]
