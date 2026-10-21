"""LLM-as-judge for answer correctness/faithfulness (plan §3.6). Structured output through the
existing `LLMClient.generate_json`; the judge sees the question, the gold expectation, the system
answer and ONLY the cited chunks. Verdicts are cached by input hash."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.pricing import cost_of_usage
from app.evaluation.cache import JsonCache
from app.evaluation.gold_schema import GoldItem
from app.llm.client import LLMClient

JUDGE_PROMPT_VERSION = "judge-v1.0"

JUDGE_SYSTEM = """Bạn là giám khảo độc lập chấm câu trả lời của một trợ lý tra cứu quy chuẩn môi trường
Việt Nam. Bạn nhận: câu hỏi, đáp án kỳ vọng do người biên soạn đọc từ văn bản gốc, câu trả lời của
hệ thống (JSON) và CHỈ các đoạn văn bản mà hệ thống đã trích dẫn.
Nguyên tắc:
- Chỉ đánh giá dựa trên văn bản được cung cấp; không dùng kiến thức ngoài.
- "Đúng giá trị" nghĩa là số, đơn vị và (nếu có) khoảng/ôxy tham chiếu khớp đáp án kỳ vọng;
  0,05 và 0.05 là như nhau.
- "Đúng cột/điều kiện" nghĩa là hệ thống chọn đúng cột (A/B/C), dải lưu lượng, thiết bị/nhiên
  liệu/công suất, nguồn tiếp nhận như đáp án kỳ vọng.
- "Trung thực" (faithful) nghĩa là mọi khẳng định trong câu trả lời đều có trong đoạn trích;
  không suy diễn, không quy đổi đơn vị, không kết luận đạt/không đạt.
- Với câu phải từ chối (đáp án kỳ vọng ghi rõ), việc hệ thống từ chối kèm chỉ dẫn là
  "appropriate"; trả lời bịa số là "should_have_abstained".
- Trả về đúng schema JSON; rationale ngắn (≤ 400 ký tự) bằng tiếng Việt."""


class JudgeVerdict(BaseModel):
    value_correct: bool | None = Field(
        default=None, description="số/đơn vị/khoảng khớp đáp án kỳ vọng; null nếu không áp dụng"
    )
    column_or_condition_correct: bool | None = Field(
        default=None, description="đúng cột/điều kiện áp dụng"
    )
    conditions_complete: bool | None = Field(default=None, description="nêu đủ điều kiện kỳ vọng")
    basis_complete: Literal["complete", "partial", "missing"] = "missing"
    faithful: bool = Field(description="không suy diễn/quy đổi/kiến thức ngoài đoạn trích")
    unsupported_claims: list[str] = Field(
        default_factory=list, description="trích các khẳng định không có trong đoạn trích"
    )
    abstain_appropriate: Literal[
        "appropriate", "should_have_answered", "should_have_abstained", "not_applicable"
    ] = "not_applicable"
    warnings_ok: bool | None = Field(
        default=None, description="cảnh báo dự thảo/khai báo/ngoài phạm vi có mặt khi cần"
    )
    overall: int = Field(
        ge=0,
        le=3,
        description="0 sai hoặc không an toàn · 1 đúng một phần · 2 đúng, thiếu nhỏ · 3 đúng và đầy đủ",
    )
    rationale: str = Field(max_length=600)


def _cited_chunks(
    resp: dict[str, Any], limit_chars: int = 6000, limit_n: int = 6
) -> list[dict[str, str]]:
    a = resp["answer"]
    cited: list[str] = []
    for c in a.get("citations", []) + [
        c for b in a.get("basis", []) for c in b.get("citations", [])
    ]:
        if c not in cited:
            cited.append(c)
    by_id = {e["chunk_id"]: e for e in resp.get("evidence", [])}
    out = []
    used = 0
    for cid in cited[:limit_n]:
        e = by_id.get(cid)
        if not e:
            continue
        text = e.get("text", "")
        if used + len(text) > limit_chars:
            text = text[: max(0, limit_chars - used)]
        used += len(text)
        out.append(
            {
                "label": e.get("citation_label", cid),
                "text": text,
                "draft": e.get("text_status") == "draft",
            }
        )
        if used >= limit_chars:
            break
    return out


def judge_prompt(item: GoldItem, resp: dict[str, Any]) -> str:
    a = resp["answer"]
    gold = {
        "expected_answer": item.expected_answer,
        "expected_numbers": [
            f"{n.display or n.value} {n.unit or ''} ({n.role})".strip()
            for n in item.expected_numbers
        ],
        "expected_conditions": item.expected_conditions.axes(),
        "must_abstain": item.must_abstain,
        "expected_behaviour": item.expected_behaviour.mode,
        "expected_warnings": item.expected_warnings,
        "judge_notes": item.judge_notes or "",
    }
    answer = {
        k: a.get(k)
        for k in (
            "conclusion",
            "basis",
            "conditions",
            "warnings",
            "missing_information",
            "abstained",
            "abstain_reason",
        )
    }
    chunks = _cited_chunks(resp)
    chunk_text = (
        "\n\n".join(
            f'<chunk label="{c["label"]}"{' draft="true"' if c["draft"] else ""}>\n{c["text"]}\n</chunk>'
            for c in chunks
        )
        or "(hệ thống không trích dẫn đoạn nào)"
    )
    as_of = item.as_of_date.isoformat() if item.as_of_date else "không nêu"
    return (
        f'<question as_of="{as_of}">\n{item.question}\n</question>\n\n'
        f"<gold>\n{json.dumps(gold, ensure_ascii=False, indent=1)}\n</gold>\n\n"
        f"<system_answer>\n{json.dumps(answer, ensure_ascii=False, indent=1)}\n</system_answer>\n\n"
        f"<cited_chunks>\n{chunk_text}\n</cited_chunks>\n\n"
        "Hãy chấm theo schema."
    )


def judge_item(
    llm: LLMClient,
    item: GoldItem,
    resp: dict[str, Any],
    *,
    cache: JsonCache | None,
    model: str,
    thinking_level: str | None,
    tag: str = "J1",
) -> dict[str, Any]:
    prompt = judge_prompt(item, resp)
    key = JsonCache.key(
        v=JUDGE_PROMPT_VERSION,
        model=model,
        thinking=thinking_level,
        system=JUDGE_SYSTEM,
        prompt=prompt,
    )
    if cache is not None:
        hit = cache.get("judge", key)
        if hit is not None:
            return {**hit, "cached": True, "tag": tag}
    r = llm.generate_json(
        JUDGE_SYSTEM, prompt, JudgeVerdict, model=model, thinking_level=thinking_level
    )
    out: dict[str, Any] = {
        "item_id": item.id,
        "tag": tag,
        "model": r.model,
        "thinking_level": r.thinking_level,
        "prompt_version": JUDGE_PROMPT_VERSION,
        "usage": r.usage,
        "cost_usd": cost_of_usage(r.model, r.usage) if r.usage else 0.0,
        "latency_s": r.latency_s,
        "error": r.error,
        "cached": False,
    }
    if r.parsed is not None:
        out.update(r.parsed.model_dump())
        out["pass"] = r.parsed.overall >= 2
    else:
        out["judge_error"] = True
    if cache is not None and r.parsed is not None:
        cache.put("judge", key, out)
    return out
