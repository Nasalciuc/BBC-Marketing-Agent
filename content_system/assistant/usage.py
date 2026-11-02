"""Model calls and their cost, for the daily limit (CONTENT_ASSISTANT_DAILY_USD, content.usage)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# USD per million tokens (input, output) — platform.claude.com/docs/en/about-claude/pricing, 2 Nov 2026.
# Haiku 5.5: prompts up to 100k tokens (ours are far below). Cached input is counted at full price.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}
UNKNOWN = (2.00, 10.00)          # a model not listed is counted at the higher price, never for free


def price(model: str) -> tuple[float, float]:
    for name, p in PRICES.items():
        if model == name or model.startswith(name + "-"):
            return p
    return UNKNOWN


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = price(model)
    return (input_tokens * p_in + output_tokens * p_out) / 1_000_000


@dataclass
class Meter:
    """What one message to the assistant used, by model; saved once the reply is out."""
    calls: dict[str, list[int]] = field(default_factory=dict)      # model → [calls, input, output]

    def add(self, model: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        row = self.calls.setdefault(model or "unknown", [0, 0, 0])
        row[0] += 1
        row[1] += int(input_tokens or 0)
        row[2] += int(output_tokens or 0)

    def add_message(self, message: Any, default_model: str) -> None:
        usage = getattr(message, "usage_metadata", None) or {}
        model = (getattr(message, "response_metadata", None) or {}).get("model_name") or default_model
        self.add(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))

    @property
    def usd(self) -> float:
        return sum(cost(m, r[1], r[2]) for m, r in self.calls.items())

    async def save(self, store: Any) -> None:
        for model, (n, tokens_in, tokens_out) in self.calls.items():
            await store.add_usage(model, n, tokens_in, tokens_out, cost(model, tokens_in, tokens_out))
        self.calls.clear()
