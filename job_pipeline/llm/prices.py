"""Claude prices used to estimate what each call cost (USD).

Anthropic first-party API list prices per million tokens, checked
2026-11-04. Edit freely — they only feed the usage ledger and the budget
meter; nothing is billed from here. Unknown models fall back to the most
expensive family in their name, so estimates err on the high side.
"""

from __future__ import annotations

# model-name substring → (input $/1M tokens, output $/1M tokens). First match wins.
PRICES: list[tuple[str, float, float]] = [
    ("haiku-5", 0.10, 0.50),
    ("haiku-4", 1.00, 5.00),
    ("haiku", 1.00, 5.00),
    ("sonnet-5", 2.00, 10.00),
    ("sonnet-4", 3.00, 15.00),
    ("sonnet", 3.00, 15.00),
    ("opus-5-5", 4.00, 20.00),
    ("opus", 5.00, 25.00),
    ("fable", 10.00, 50.00),
]
DEFAULT_PRICE = (5.00, 25.00)

# Server-side web search: per search request (not per token).
WEB_SEARCH_USD = 0.01


def price_for(model: str) -> tuple[float, float]:
    m = model.lower()
    for key, inp, out in PRICES:
        if key in m:
            return inp, out
    return DEFAULT_PRICE


def cost_usd(model: str, input_tokens: int, output_tokens: int, *,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0,
             web_searches: int = 0) -> float:
    inp, out = price_for(model)
    total = (
        input_tokens * inp
        + cache_write_tokens * inp * 1.25
        + cache_read_tokens * inp * 0.1
        + output_tokens * out
    ) / 1_000_000
    return round(total + web_searches * WEB_SEARCH_USD, 6)
