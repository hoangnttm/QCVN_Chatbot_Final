"""Evaluation of the constrained LLM path of query understanding v2 (plan G3 step 3.5).

Every question of the measurement sets goes through `LLMAssist`; the model is only called when
the rules are unsure or leave several answers (D57). The run stops calling once the budget is
spent and reports on the questions done so far. Calls are cached by prompt, so a second run pays
nothing for the same questions; the report gives both the money of this run and the cost of the
tokens used, cached or not.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.core.pricing import cost_of_usage
from app.evaluation import gold_frame
from app.evaluation import gold_reasoning as gr
from app.evaluation.cache import JsonCache
from app.evaluation.gold_schema import GoldItem
from app.llm.client import LLMClient, LLMResult
from app.retrieval.frame_llm import BudgetExceeded, LLMAssist

NAMESPACE = "frame_llm"


def cached_call(llm: LLMClient, cache: JsonCache | None, token_cost: list[float]) -> Any:
    """`generate_json` behind the evaluation cache. A hit costs nothing now; what its tokens cost
    when they were bought is added to `token_cost[0]`."""

    def call(system: str, prompt: str, schema: type[BaseModel], **kw: Any) -> LLMResult:
        key = JsonCache.key(system=system, prompt=prompt, schema=schema.__name__, **kw)
        hit = cache.get(NAMESPACE, key) if cache else None
        if hit is not None:
            token_cost[0] += cost_of_usage(hit["model"], hit["usage"])
            try:
                parsed = schema.model_validate_json(hit["text"])
            except ValueError:
                parsed = None
            return LLMResult(
                text=hit["text"],
                parsed=parsed,
                model="cache",  # not in the price list: this run pays nothing for it
                thinking_level=None,
                usage=hit["usage"],
                latency_s=hit["latency_s"],
                error=hit.get("error"),
            )
        r = llm.generate_json(system, prompt, schema, **kw)
        token_cost[0] += cost_of_usage(r.model, r.usage)
        if cache and r.error is None:
            cache.put(
                NAMESPACE,
                key,
                {"text": r.text, "model": r.model, "usage": r.usage, "latency_s": r.latency_s},
            )
        return r

    return call


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def run(
    assist: LLMAssist,
    codes: dict[str, str],
    items: list[GoldItem],
    partial: list[dict[str, Any]],
    composite: list[dict[str, Any]],
    token_cost: list[float],
) -> tuple[str, dict[str, list[dict[str, Any]]]]:
    """(report in Markdown, rows per set). Order: hand-written items first, then the generated
    sets, then the template items — so a budget stop still covers the questions that matter."""
    order = sorted(items, key=lambda it: ({"B": 0, "M": 2, "A": 3}.get(it.tier, 4), it.id))
    hand = [it for it in order if it.tier == "B"]
    rest = [it for it in order if it.tier != "B"]
    rows: dict[str, list[dict[str, Any]]] = {k: [] for k in ("items", "partial", "composite")}
    stopped: str | None = None
    planned = len(order) + len(partial) + len(composite)
    t0 = time.perf_counter()

    def understand(question: str) -> tuple[Any, dict[str, Any]]:
        frame, info = assist.understand(question)
        return frame, info

    try:
        for it in hand:
            frame, info = understand(it.question)
            rows["items"].append(_item_row(it, frame, info, codes))
        for r in partial:
            frame, info = understand(r["question"])
            rows["partial"].append(gr.score_partial(r, frame) | {"llm": info})
        for r in composite:
            frame, info = understand(r["question"])
            rows["composite"].append(gr.score_composite(r, frame, codes) | {"llm": info})
        for it in rest:
            frame, info = understand(it.question)
            rows["items"].append(_item_row(it, frame, info, codes))
    except BudgetExceeded as e:
        stopped = str(e)
    done = sum(len(v) for v in rows.values())
    wall = time.perf_counter() - t0

    every = rows["items"] + rows["partial"] + rows["composite"]
    calls = [c for r in every for c in r["llm"]["calls"]]
    asked = [r for r in every if r["llm"]["calls"]]
    lat = [r["llm"]["latency_s"] for r in asked]
    lines = [
        "# Bước 3.5 — đường AI ràng buộc của bản hiểu câu hỏi v2",
        "",
        f"Số câu đã chạy: {done}/{planned}."
        + (f" **Dừng vì hết ngân sách** ({stopped})." if stopped else " Không chạm ngân sách."),
        "",
        "## Lời gọi AI",
        "",
        "| Chỉ số | Giá trị |",
        "|---|---|",
        f"| Câu có gọi AI | {len(asked)}/{done} ({len(asked) / max(1, done):.3f}) |",
        f"| Số lời gọi | {len(calls)} (sửa lĩnh vực / ý định: "
        f"{sum(1 for c in calls if c['kind'] == 'repair')}; chọn dòng ứng viên: "
        f"{sum(1 for c in calls if c['kind'] == 'choice')}) |",
        f"| Lời gọi lỗi | {sum(1 for c in calls if c.get('error'))} |",
        f"| Lựa chọn bị loại vì không có đoạn chữ trong câu hỏi | "
        f"{sum(1 for c in calls if c.get('rejected'))} |",
        f"| Độ trễ AI của câu có gọi (p50 / p95) | {_pct(lat, 0.5):.1f} s / {_pct(lat, 0.95):.1f} s |",
        f"| Tiền trả trong lần chạy này | {assist.spent:.4f} USD |",
        f"| Chi phí theo token đã dùng (kể cả phần lấy từ cache) | {token_cost[0]:.4f} USD |",
        f"| Chi phí trung bình mỗi câu (theo token, trên mọi câu đã chạy) | "
        f"{token_cost[0] / max(1, done):.5f} USD |",
        f"| Thời gian chạy | {wall:.0f} s |",
        "",
    ]
    lines += _items_section(rows["items"])
    lines += _partial_section(rows["partial"])
    lines += _composite_section(rows["composite"])
    return "\n".join(lines) + "\n", rows


def _item_row(
    it: GoldItem, frame: Any, info: dict[str, Any], codes: dict[str, str]
) -> dict[str, Any]:
    row = gold_frame.score_item(it, gold_frame.to_predicted(frame, codes))
    clar = frame.clarification or {}
    row |= gr.score_hops(it, frame)
    row |= {
        "tier": it.tier,
        "llm": info,
        "mode": clar.get("mode"),
        "narrowed": clar.get("narrowed_by") == "llm",
        "expected_mode": it.expected_behaviour.mode,
        "unsure": list(frame.unsure),
    }
    if it.target_rule_codes:
        inside = it.target_rule_codes[0] in clar.get("rule_codes", [])
        row["cell"] = (
            "pinned"
            if clar.get("mode") == "answer" and inside
            else "wrong"
            if clar.get("mode") == "answer"
            else "lost"
            if not inside
            else "open"
        )
    return row


def _rate(rows: list[dict[str, Any]], key: str) -> str:
    m = gr.mean(rows, key)
    return "–" if not m.get("n") else f"{m['mean']:.3f} (n={m['n']})"


def _items_section(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    out = [
        "## Câu của bộ gold",
        "",
        "| Nhóm | Số câu | Đúng lĩnh vực + ý định chính | Còn báo không chắc | Chốt đúng một ô"
        " | Chốt sai ô | Còn liệt kê / hỏi lại | Ô đúng bị loại |",
        "|---|---|---|---|---|---|---|---|",
    ]
    groups = [("câu soạn tay", [r for r in rows if r["tier"] == "B"])]
    groups += [("T2 nhiều bước", [r for r in rows if r["tier"] == "M"])]
    groups += [("T1 một ô", [r for r in rows if r["tier"] == "A"])]
    for name, rs in groups:
        if not rs:
            continue
        cells = [r for r in rs if "cell" in r]

        def n(kind: str, cells: list[dict[str, Any]] = cells) -> str:
            return f"{sum(1 for r in cells if r['cell'] == kind)}/{len(cells)}"

        out.append(
            f"| {name} | {len(rs)} | {_rate(rs, 'both')}"
            f" | {sum(1 for r in rs if r['unsure'])} | {n('pinned')} | {n('wrong')}"
            f" | {n('open')} | {n('lost')} |"
        )
    multi = [r for r in rows if "expected_type" in r]
    if multi:
        out += ["", f"Câu nhiều bước xác định đủ cả hai căn cứ: {_rate(multi, 'both_bases')}."]
    hand = [r for r in rows if r["tier"] == "B"]
    if hand:
        out += [
            "",
            "### Từng câu soạn tay có gọi AI",
            "",
            "| Mã | Lời gọi | Kết quả sau AI | Ô đúng |",
            "|---|---|---|---|",
        ]
        for r in hand:
            if not r["llm"]["calls"]:
                continue
            kinds = ", ".join(
                c["kind"]
                + (f" (giữ {c['kept']}/{c['n_options']})" if c["kind"] == "choice" else "")
                for c in r["llm"]["calls"]
            )
            out.append(
                f"| {r['item_id']} | {kinds} | {r['pred']['domain']} · "
                f"{', '.join(r['pred']['intents'])} · {r['mode'] or '–'} | {r.get('cell', '–')} |"
            )
    return out + [""]


def _partial_section(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    narrowed = [
        r
        for r in rows
        if any(0 < c.get("kept", 0) < c.get("n_options", 0) for c in r["llm"]["calls"])
    ]
    return [
        f"## Câu thiếu dữ kiện ({len(rows)} câu; câu hỏi cố ý thiếu điều kiện, AI không được tự điền)",
        "",
        "| Chỉ số | Kết quả |",
        "|---|---|",
        f"| Đúng chế độ (trả lời / liệt kê / hỏi lại) | {_rate(rows, 'mode_ok')} |",
        f"| Trả lời một giá trị khi còn nhiều đáp án | {int(sum(r['unsafe_answer'] for r in rows))} câu |",
        f"| Tập phương án khớp nguyên vẹn | {_rate(rows, 'options_ok')} |",
        f"| Câu AI đã thu hẹp tập ứng viên (dù câu hỏi không nêu thêm gì) | {len(narrowed)} |",
        "",
    ]


def _composite_section(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    return [
        f"## Câu ghép hai lĩnh vực ({len(rows)} câu)",
        "",
        f"Đúng số câu con và lĩnh vực: {_rate(rows, 'split_ok')}; F1 slot theo câu con: "
        f"{gr.micro_f1(rows) or 0:.3f}.",
        "",
    ]


def latency_summary(rows: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
    lat = [r["llm"]["latency_s"] for v in rows.values() for r in v if r["llm"]["calls"]]
    return {"n": len(lat), "median": statistics.median(lat) if lat else 0.0}


def write(out: Path, text: str, rows: dict[str, list[dict[str, Any]]]) -> None:
    from app.evaluation import runner

    out.mkdir(parents=True, exist_ok=True)
    for name, rs in rows.items():
        runner.write_jsonl(out / f"{name}.jsonl", rs)
    (out / "report.md").write_text(text, "utf-8")
