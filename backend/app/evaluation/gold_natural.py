"""Tier C — natural questions from the pilot/UAT query logs (plan §2.8).

`import_logs` reads the admin CSV export (`GET /api/v1/admin/query-logs/export.csv`), filters by
date, removes questions already in the gold set (or near-duplicates), masks obvious facility names
and writes candidates for the author to annotate with the v2 schema."""

from __future__ import annotations

import csv
import difflib
import json
import re
from pathlib import Path

from app.evaluation.gold_schema import GoldItem
from app.evaluation.gold_source import normalize_raw

_ORG_RE = re.compile(
    r"\b(Công ty|Cty|Nhà máy|Xí nghiệp|Doanh nghiệp|Tập đoàn|KCN|Khu công nghiệp|Cụm công nghiệp)"
    r"\s+((?:[A-ZĐ][^\s,.;?()]*\s*){1,5})"
)


def anonymize(question: str) -> tuple[str, bool]:
    out, n = _ORG_RE.subn(lambda m: f"{m.group(1)} [ẩn danh]", question)
    return out, n > 0


def _norm(q: str) -> str:
    t = normalize_raw(q)
    return re.sub(r"\d+([.,]\d+)?", "#", t)


def import_logs(
    csv_path: Path, out: Path, *, since: str | None, existing: list[GoldItem]
) -> tuple[int, Path]:
    known = [_norm(it.question) for it in existing]
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if since and (r.get("created_at") or "") < since:
                continue
            q = (r.get("question") or "").strip()
            if len(q) < 3:
                continue
            nq = _norm(q)
            dup = any(
                nq == k or difflib.SequenceMatcher(None, nq, k).ratio() >= 0.85 for k in known
            )
            if dup:
                continue
            known.append(nq)
            anon, masked = anonymize(q)
            rows.append(
                {
                    "origin_query_id": r.get("query_id"),
                    "created_at": r.get("created_at"),
                    "question": anon,
                    "question_original_masked": masked,
                    "status": r.get("status"),
                    "path": r.get("path"),
                    "domain_routed": r.get("domain"),
                    "intent_routed": r.get("intent"),
                    "feedback_min": r.get("feedback_min"),
                    "feedback_count": r.get("feedback_count"),
                    "tier": "C",
                    "source": "pilot_log",
                    "split": "test",
                }
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows), out
