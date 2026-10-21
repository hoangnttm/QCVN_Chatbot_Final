"""Embedding providers (ADR-0005).

Default: Gemini API `gemini-embedding-001` with `output_dimensionality=1536` (asymmetric task
types RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY). Fallback: local sentence-transformers (bge-m3).
Vectors are L2-normalised so cosine distance in pgvector is well defined for truncated (MRL) output.
"""

from __future__ import annotations

import math
import time
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings

# gemini-embedding-001 accepts <= 2,048 tokens; Vietnamese legal text is ~3 chars/token, so this
# cap only trips on pathological chunks. Truncation is counted, never silent (ADR-0005).
RETRYABLE_HTTP = (429, 500, 502, 503, 504)


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class GeminiEmbedder:
    def __init__(self, settings: Settings | None = None, max_retries: int = 6) -> None:
        settings = settings or get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (backend/.env)")
        from google import genai

        self._client = genai.Client(api_key=settings.gemini_api_key)
        self.model_name = settings.embedding_model
        self.dim = settings.embedding_dim
        self.task_document = settings.embedding_task_document
        self.task_query = settings.embedding_task_query
        self.max_chars = settings.embedding_max_chars
        self.max_retries = max_retries
        self.truncated = 0
        self.requests = 0
        self.retries = 0

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import errors, types

        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim)
        delay = 5.0
        attempt = 0
        while True:
            try:
                self.requests += 1
                resp = self._client.models.embed_content(
                    model=self.model_name, contents=texts, config=config
                )
                break
            except errors.APIError as exc:
                if exc.code in RETRYABLE_HTTP and attempt < self.max_retries:
                    attempt += 1
                    self.retries += 1
                    time.sleep(delay)
                    delay = min(delay * 2, 60.0)
                    continue
                raise
            except httpx.TransportError:  # read timeout / dropped connection
                if attempt < self.max_retries:
                    attempt += 1
                    self.retries += 1
                    time.sleep(delay)
                    delay = min(delay * 2, 60.0)
                    continue
                raise
        vectors = [l2_normalize(list(e.values or [])) for e in (resp.embeddings or [])]
        if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
            raise RuntimeError(
                f"Gemini returned {len(vectors)} vectors for {len(texts)} texts "
                f"(dims {sorted({len(v) for v in vectors})}); expected {self.dim}"
            )
        return vectors

    def _clip(self, text: str) -> str:
        if len(text) > self.max_chars:
            self.truncated += 1
            return text[: self.max_chars]
        return text

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed([self._clip(t) for t in texts], self.task_document)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._clip(text)], self.task_query)[0]


class LocalEmbedder:
    """Offline fallback (bge-m3, 1024 dims). Needs a matching `evidence_chunks.embedding` width."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(settings.local_embedding_model)
        self.model_name = settings.local_embedding_model
        self.dim = int(self._model.get_sentence_embedding_dimension() or 0)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def get_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    if settings.embedding_provider == "gemini":
        return GeminiEmbedder(settings)
    if settings.embedding_provider == "local":
        return LocalEmbedder(settings)
    raise ValueError(f"unknown EMBEDDING_PROVIDER={settings.embedding_provider!r}")
