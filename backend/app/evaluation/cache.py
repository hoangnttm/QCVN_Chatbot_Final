"""Content-addressed JSON cache for evaluation runs (answers, judge verdicts): re-runs with the
same inputs cost nothing. Root defaults to `data/derived/eval_cache/` (git-ignored)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(**parts: Any) -> str:
        blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / f"{key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        p = self._path(namespace, key)
        if not p.exists():
            self.misses += 1
            return None
        self.hits += 1
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def put(self, namespace: str, key: str, value: Any) -> None:
        p = self._path(namespace, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, default=str)
