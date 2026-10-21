"""Structured (JSON) logging with request/query correlation (M15).

`configure_logging(settings)` installs one stdout handler on the root logger. When `log_json` is on
every record becomes one JSON line carrying `request_id` / `query_id` from context variables plus
any `extra=` fields, so a question can be traced across the API, the SSE worker thread and the
audit tables by its `query_id`.

`RequestIdMiddleware` is a pure ASGI middleware (not `BaseHTTPMiddleware`, which buffers streaming
responses such as SSE): it sets `X-Request-Id`, keeps it in a contextvar and writes one access
log line per request.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
query_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("query_id", default=None)
client_key_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "client_key", default=None
)

_STANDARD_ATTRS = set(
    logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None).__dict__.keys()
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        qid = query_id_var.get()
        ck = client_key_var.get()
        if rid:
            payload["request_id"] = rid
        if qid:
            payload["query_id"] = qid
        if ck:
            payload["client_key"] = ck
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_CONFIGURED = False


def configure_logging(settings: Settings) -> None:
    """Idempotent: safe to call from app startup and from scripts."""
    global _CONFIGURED
    root = logging.getLogger()
    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    root.setLevel(level)
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.handlers = [handler]
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # our RequestIdMiddleware writes the access line (with request_id); avoid duplicates
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    _CONFIGURED = True


access_logger = logging.getLogger("app.access")


class RequestIdMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        rid = (headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        ck_token = None
        cid = headers.get("x-client-id")
        if cid:
            ck_token = client_key_var.set(f"cid:{cid[:36]}")
        started = time.perf_counter()
        status = {"code": 0}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 0))
                hdrs = list(message.get("headers") or [])
                hdrs.append((b"x-request-id", rid.encode()))
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_logger.info(
                "http.access",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status["code"],
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            request_id_var.reset(token)
            if ck_token is not None:
                client_key_var.reset(ck_token)
