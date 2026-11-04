"""Monthly Claude budget: warn, or stop new Claude work, at the limit.

Set in Setup (or .env): JOB_PIPELINE_MONTHLY_BUDGET (USD, 0 = no budget)
and JOB_PIPELINE_BUDGET_MODE ("warn" or "stop"). Spend comes from the usage
ledger (llm/usage.py). `check()` runs before every Claude client is built.
"""

from __future__ import annotations

from typing import Any

from ..config import Secrets, load_secrets
from .base import LLMError
from .usage import by_feature, month_spend, this_month


def status(secrets: Secrets | None = None) -> dict[str, Any]:
    s = secrets or load_secrets()
    spent = month_spend()
    budget = max(0.0, float(s.monthly_budget))
    return {
        "month": this_month(), "spent_usd": spent, "budget_usd": budget,
        "mode": s.budget_mode, "subscription": s.auth_mode == "claude_code",
        "over": budget > 0 and spent >= budget,
        "remaining_usd": round(max(0.0, budget - spent), 4) if budget > 0 else None,
        "by_feature": by_feature(),
    }


def check(secrets: Secrets | None = None) -> None:
    """Raise in "stop" mode once this month's spend reached the budget."""
    s = secrets or load_secrets()
    if s.monthly_budget <= 0 or s.budget_mode != "stop":
        return
    spent = month_spend()
    if spent >= s.monthly_budget:
        raise LLMError(
            f"Monthly budget reached (${spent:.2f} of ${s.monthly_budget:.2f}). "
            "Raise it on the Setup page, switch it to 'warn', or wait until next month."
        )
