# ruff: noqa: E501  (Vietnamese prompt and report lines are intentionally long)
"""Mini-benchmark of Gemini models for the QA step (PLAN.md §8) on the draft gold set.

    python backend/scripts/llm_bench.py --models gemini-2.5-flash,gemini-3.5-flash [--limit 8] [--top-k 8]

For every gold question: embed the question (Gemini RETRIEVAL_QUERY), take the top-k evidence chunks
by cosine distance (filtered by domain), and ask each model for the PLAN §8 JSON answer with the
structured-output API. Measured per model: JSON validity, citations ⊆ evidence pack, expected
citation label cited, every number in the answer present in a cited chunk, abstention correctness,
latency, tokens (incl. thinking) and estimated cost. Writes docs/reports/llm-bench-<date>.md + .json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, Field  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.pricing import PRICES, est_cost  # noqa: E402, F401
from app.db import models as m  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.retrieval.embedder import GeminiEmbedder  # noqa: E402

SYSTEM = """Bạn là trợ lý tra cứu quy chuẩn kỹ thuật quốc gia về môi trường (QCVN 40:2025, QCVN 19:2024, TT 06/2025, TT 45/2024, TT 10/2021) của Việt Nam.
Quy tắc bắt buộc:
1. Chỉ dùng thông tin trong EVIDENCE bên dưới. Không dùng kiến thức ngoài, không suy diễn ngưỡng, không kết luận "đạt/không đạt".
2. Mọi câu khẳng định phải kèm chunk_id từ EVIDENCE trong "citations". Mọi con số trong câu trả lời phải xuất hiện trong chunk được trích.
3. Nếu EVIDENCE không đủ để trả lời, đặt abstained=true và nêu abstain_reason; với câu hỏi tần suất quan trắc: các văn bản trong hệ thống không quy định tần suất (TT 10/2021 Điều 16.3/17.5 dẫn chiếu pháp luật hiện hành) → abstained=true.
4. Nếu chunk có ghi "(dự thảo)" hoặc "do quản trị viên khai báo", thêm cảnh báo tương ứng vào warnings.
5. Luôn nêu điều kiện áp dụng (cột A/B/C, lưu lượng, nhiên liệu, công suất, O2 tham chiếu) trong conditions.
Trả lời bằng tiếng Việt, ngắn gọn, đúng schema JSON."""


class Basis(BaseModel):
    text: str
    citations: list[str]


class Answer(BaseModel):
    conclusion: str
    basis: list[Basis] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    abstained: bool = False
    abstain_reason: str | None = None


def _num_set(text: str) -> set[str]:
    out = set()
    for n in re.findall(r"\d+(?:[.,]\d+)?", text or ""):
        out.add(n.replace(",", "."))
    return out


def load_gold(path: Path, limit: int | None) -> list[dict[str, Any]]:
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return rows[:limit] if limit else rows


def retrieve(session, emb: GeminiEmbedder, q: dict[str, Any], k: int) -> list[m.EvidenceChunk]:
    vec = emb.embed_query(q["question"])
    stmt = select(m.EvidenceChunk).where(m.EvidenceChunk.embedding.is_not(None))
    if q.get("domain") in ("wastewater", "emission"):
        stmt = stmt.where(m.EvidenceChunk.domain.in_([q["domain"], "both"]))
    stmt = stmt.order_by(m.EvidenceChunk.embedding.cosine_distance(vec)).limit(k)
    return list(session.execute(stmt).scalars())


def pack_text(chunks: list[m.EvidenceChunk]) -> str:
    parts = []
    for c in chunks:
        flag = " [BẢN DỰ THẢO]" if c.text_status == "draft" else ""
        parts.append(
            f'<chunk id="{c.chunk_id}" label="{c.citation_label}"{flag}>\n{c.text}\n</chunk>'
        )
    return "\n".join(parts)


def ask(
    client, spec: str, question: str, evidence: str, as_of: str | None
) -> tuple[Answer | None, dict[str, Any]]:
    """`spec` = model id, optionally `@low|@medium|@high` thinking level (Gemini 3.x)."""
    from google.genai import errors, types

    model, _, level = spec.partition("@")
    prompt = (
        f"CÂU HỎI: {question}\nTHỜI ĐIỂM ÁP DỤNG: {as_of or 'hiện tại'}\n\nEVIDENCE:\n{evidence}"
    )
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        temperature=0,
        response_mime_type="application/json",
        response_schema=Answer,
        thinking_config=types.ThinkingConfig(thinking_level=level) if level else None,
    )
    t0 = time.perf_counter()
    try:
        resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
    except errors.APIError as e:
        return None, {
            "error": f"{e.code} {str(e)[:120]}",
            "latency_s": round(time.perf_counter() - t0, 2),
        }
    latency = time.perf_counter() - t0
    u = resp.usage_metadata
    meta = {
        "latency_s": round(latency, 2),
        "prompt_tokens": getattr(u, "prompt_token_count", None),
        "output_tokens": getattr(u, "candidates_token_count", None),
        "thinking_tokens": getattr(u, "thoughts_token_count", None),
        "total_tokens": getattr(u, "total_token_count", None),
    }
    try:
        parsed = resp.parsed
        if parsed is None:
            parsed = Answer.model_validate_json(resp.text)
        return parsed, meta
    except Exception as e:  # noqa: BLE001
        meta["error"] = f"invalid JSON: {str(e)[:80]}"
        meta["raw"] = (resp.text or "")[:300]
        return None, meta


def score(q: dict[str, Any], ans: Answer | None, chunks: list[m.EvidenceChunk]) -> dict[str, Any]:
    if ans is None:
        return {"json_ok": False}
    by_id = {c.chunk_id: c for c in chunks}
    cited = list(dict.fromkeys(ans.citations + [c for b in ans.basis for c in b.citations]))
    in_pack = [c for c in cited if c in by_id]
    expected = q.get("expected_citation_labels") or []
    labels_cited = {by_id[c].citation_label for c in in_pack}
    labels_in_pack = {c.citation_label for c in chunks}

    def _match(lbl: str, e: str) -> bool:
        # gold labels may be rule-level ("…, TT 1, F > 2000, Cột B") while chunks are row-level
        return lbl == e or e.startswith(lbl) or lbl.startswith(e)

    exp_hit = (
        any(any(_match(lbl, e) for lbl in labels_cited) for e in expected) if expected else None
    )
    exp_retrievable = (
        any(any(_match(lbl, e) for lbl in labels_in_pack) for e in expected) if expected else None
    )
    nums = _num_set(ans.conclusion) | {n for b in ans.basis for n in _num_set(b.text)}
    cited_text = " ".join(by_id[c].context_text for c in in_pack)
    cited_nums = _num_set(cited_text)
    # numbers echoed from the question / as-of date are not hallucinations
    echo = _num_set(q["question"]) | _num_set(q.get("as_of_date") or "")
    bad_nums = sorted(
        n for n in nums if n not in cited_nums and n not in echo and n not in {"1", "2", "3"}
    )
    must_abstain = bool(q.get("must_abstain"))
    return {
        "json_ok": True,
        "abstained": ans.abstained,
        "abstain_ok": ans.abstained == must_abstain,
        "citations": len(cited),
        "citations_in_pack": len(in_pack) == len(cited) and (len(cited) > 0 or ans.abstained),
        "expected_hit": exp_hit,
        "expected_retrievable": exp_retrievable,
        "numbers_ok": not bad_nums if not ans.abstained else True,
        "bad_numbers": bad_nums,
        "draft_warning": any("dự thảo" in w.lower() for w in ans.warnings)
        if any(c.text_status == "draft" for c in (by_id[c] for c in in_pack))
        else None,
        "cited_labels": sorted(labels_cited),
        "conclusion": ans.conclusion[:160],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="gemini-2.5-flash,gemini-3.5-flash")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--tag", default="", help="suffix for the report file name")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings()
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    emb = GeminiEmbedder(settings)
    gold = load_gold(settings.data_dir / "gold" / "gold_qa_v1.jsonl", args.limit)
    models = [x.strip() for x in args.models.split(",") if x.strip()]
    results: dict[str, list[dict[str, Any]]] = {mdl: [] for mdl in models}
    with get_sessionmaker(settings)() as session:
        packs = {q["id"]: retrieve(session, emb, q, args.top_k) for q in gold}
        for q in gold:
            chunks = packs[q["id"]]
            evidence = pack_text(chunks)
            for mdl in models:
                ans, meta = ask(client, mdl, q["question"], evidence, q.get("as_of_date"))
                sc = score(q, ans, chunks)
                meta["cost_usd"] = est_cost(
                    mdl.partition("@")[0],
                    meta.get("prompt_tokens"),
                    meta.get("output_tokens"),
                    meta.get("thinking_tokens"),
                )
                results[mdl].append({"id": q["id"], "intent": q.get("intent"), **meta, **sc})
                print(
                    f"{mdl:26} {q['id']:10} {meta.get('latency_s', '?'):>6}s json={sc.get('json_ok')} "
                    f"cit_ok={sc.get('citations_in_pack')} exp={sc.get('expected_hit')} num={sc.get('numbers_ok')} "
                    f"abst={sc.get('abstain_ok')} {meta.get('error', '')}"
                )
                time.sleep(args.sleep)

    today = dt.date.today().isoformat()
    lines = [
        f"# LLM bench (Gemini) — {today}",
        "",
        f"Gold: {len(gold)} câu (bản nháp), top-k = {args.top_k}, temperature 0, structured output.",
        "",
        "| Model | JSON hợp lệ | Citation ⊆ pack | Trích đúng nhãn kỳ vọng (khi pack có) | Số liệu khớp chunk | Abstain đúng | Cảnh báo dự thảo | Độ trễ TB (s) | Token vào/ra/thinking TB | Chi phí TB/câu (USD) | Lỗi |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for mdl, rows in results.items():
        n = len(rows)
        ok = [r for r in rows if r.get("json_ok")]

        def frac(key, pool):
            vals = [r[key] for r in pool if r.get(key) is not None]
            return f"{sum(1 for v in vals if v)}/{len(vals)}" if vals else "—"

        lat = [r["latency_s"] for r in rows if "latency_s" in r and r.get("json_ok")]

        def tok(k: str, pool: list[dict[str, Any]] = ok) -> float:
            return (sum((r.get(k) or 0) for r in pool) / len(pool)) if pool else 0.0

        exp_pool = [r for r in ok if r.get("expected_retrievable")]
        lines.append(
            f"| {mdl} | {len(ok)}/{n} | {frac('citations_in_pack', ok)} | {frac('expected_hit', exp_pool)} | "
            f"{frac('numbers_ok', ok)} | {frac('abstain_ok', ok)} | {frac('draft_warning', ok)} | "
            f"{(sum(lat) / len(lat)) if lat else 0:.1f} | {tok('prompt_tokens'):.0f}/{tok('output_tokens'):.0f}/{tok('thinking_tokens'):.0f} | "
            f"{tok('cost_usd'):.4f} | {sum(1 for r in rows if r.get('error'))} |"
        )
    lines += ["", "## Chi tiết", ""]
    for mdl, rows in results.items():
        lines.append(f"### {mdl}")
        for r in rows:
            lines.append(
                f"- {r['id']} ({r.get('intent')}): {r.get('latency_s')}s, cit={r.get('citations')}, exp={r.get('expected_hit')}, "
                f"num={r.get('numbers_ok')} {r.get('bad_numbers') or ''}, abst={r.get('abstained')} → {r.get('conclusion') or r.get('error')}"
            )
        lines.append("")
    out = settings.data_dir.parent / "docs" / "reports" / f"llm-bench-{today}{args.tag}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
