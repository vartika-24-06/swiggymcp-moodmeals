"""Price table for cost estimates (R14.3). Prices are config, not facts.

USD per million tokens. VERIFY against each vendor's pricing page before relying on them:
the numbers below were entered from the owner's notes and memory, not fetched.
The UI must call every figure an estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

LAST_CHECKED = "2026-10-05 (unverified)"


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float


# Free tiers are priced at 0 but have rate limits.
PRICES: dict[tuple[str, str], Price] = {
    ("openai", "gpt-5-mini"): Price(0.25, 2.00),
    ("anthropic", "claude-haiku-4-5-20251001"): Price(1.00, 5.00),
    ("anthropic", "claude-sonnet-5-5"): Price(3.00, 15.00),
    ("groq", "*"): Price(0.0, 0.0),
    ("gemini", "*"): Price(0.0, 0.0),
}


def price_for(provider: str, model: str) -> Price | None:
    return PRICES.get((provider, model)) or PRICES.get((provider, "*"))


def estimate_cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> float | None:
    """USD estimate, or None when the model is not in the table (the UI says 'unknown')."""
    p = price_for(provider, model)
    if p is None:
        return None
    return (tokens_in * p.input_per_m + tokens_out * p.output_per_m) / 1_000_000


def estimate_run_cost(provider: str, model: str, turns: int = 8, tokens_per_turn: int = 2500):
    """Rough pre-run estimate: about 8 turns of about 2.5k input and 200 output tokens."""
    return estimate_cost(provider, model, turns * tokens_per_turn, turns * 200)
