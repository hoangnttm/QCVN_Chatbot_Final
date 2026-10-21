"""List prices for cost estimates (USD per 1M tokens: input, output incl. thinking).

Source: ai.google.dev/gemini-api/docs/pricing, read 2026-09-23 (3.6/3.7/3.8 Flash: promotional
price until 2026-12-31, then $1.50 / $7.50). Used by the QA audit (`query_logs.cost_usd`,
`usage_daily`), the offline benchmark (`scripts/llm_bench.py`) and `scripts/qa_eval.py`.
Unknown models cost None (reported as 0 in aggregates) — extend the table when a model is added.
"""

from __future__ import annotations

from typing import Any

PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-embedding-001": (0.15, 0.0),
}


def est_cost(
    model: str | None,
    prompt_tokens: int | None,
    output_tokens: int | None,
    thinking: int | None,
) -> float | None:
    if not model or model not in PRICES:
        return None
    pi, po = PRICES[model]
    return ((prompt_tokens or 0) * pi + ((output_tokens or 0) + (thinking or 0)) * po) / 1e6


def cost_of_usage(model: str | None, usage: dict[str, Any] | None) -> float:
    """Cost of one LLM call from its usage dict (`prompt_tokens`, `output_tokens`, `thinking_tokens`)."""
    u = usage or {}
    return (
        est_cost(model, u.get("prompt_tokens"), u.get("output_tokens"), u.get("thinking_tokens"))
        or 0.0
    )
