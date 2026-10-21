"""In-process operational state (M15): last Gemini success/error, consecutive errors, uptime.

A module-level singleton because the SSE worker threads do not go through FastAPI dependency
injection. Thread-safe; reset in tests with `STATE.reset()`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class OpsState:
    started_at: datetime = field(default_factory=_now)
    llm_calls: int = 0
    llm_errors: int = 0
    llm_consecutive_errors: int = 0
    llm_last_success_at: datetime | None = None
    llm_last_error_at: datetime | None = None
    llm_last_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_llm(self, error: str | None) -> None:
        with self._lock:
            self.llm_calls += 1
            if error:
                self.llm_errors += 1
                self.llm_consecutive_errors += 1
                self.llm_last_error_at = _now()
                self.llm_last_error = error[:500]
            else:
                self.llm_consecutive_errors = 0
                self.llm_last_success_at = _now()

    def reset(self) -> None:
        with self._lock:
            self.started_at = _now()
            self.llm_calls = 0
            self.llm_errors = 0
            self.llm_consecutive_errors = 0
            self.llm_last_success_at = None
            self.llm_last_error_at = None
            self.llm_last_error = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_s": int((_now() - self.started_at).total_seconds()),
                "llm_calls": self.llm_calls,
                "llm_errors": self.llm_errors,
                "llm_consecutive_errors": self.llm_consecutive_errors,
                "llm_last_success_at": self.llm_last_success_at,
                "llm_last_error_at": self.llm_last_error_at,
                "llm_last_error": self.llm_last_error,
            }


STATE = OpsState()
