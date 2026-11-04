"""Usage ledger + monthly budget."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from job_pipeline.config import Secrets
from job_pipeline.llm import LLMError, budget, usage
from job_pipeline.llm.prices import cost_usd, price_for
from job_pipeline.ui import create_app
from tests.test_ui_server import _write_isolated_config


@pytest.fixture(autouse=True)
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "usage.jsonl"
    monkeypatch.setenv("JOB_PIPELINE_USAGE_FILE", str(p))
    return p


def test_prices_and_cost() -> None:
    assert price_for("claude-haiku-5-5") == (0.10, 0.50)
    assert price_for("claude-sonnet-5-5") == (2.00, 10.00)
    assert price_for("claude-opus-5-5") == (4.00, 20.00)
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 100_000, web_searches=2) == 3.02


def test_api_response_is_recorded_with_feature(ledger: Path) -> None:
    resp = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=10_000, output_tokens=1_000, cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=1)))
    with usage.usage_feature("cover-letter"):
        usage.record_api_response("claude-sonnet-5-5", resp)
    (row,) = usage.rows()
    assert row["feature"] == "cover-letter" and row["web_searches"] == 1
    assert row["cost_usd"] == pytest.approx(0.02 + 0.01 + 0.01)
    assert usage.month_spend() == pytest.approx(0.04)


def test_cli_cost_is_recorded(ledger: Path) -> None:
    msg = SimpleNamespace(total_cost_usd=0.123, usage={"input_tokens": 5, "output_tokens": 7})

    @usage.tagged("premium-search")
    def run() -> None:
        usage.record_cli_result("sonnet", msg)

    run()
    assert usage.by_feature() == {"premium-search": 0.123}


def _secrets(**kw: Any) -> Secrets:
    return Secrets(JOB_PIPELINE_AUTH_MODE="api_key", **kw)


def test_budget_stop_blocks_and_warn_does_not(ledger: Path) -> None:
    usage.record(model="m", cost_usd=5.0, source="api")
    with pytest.raises(LLMError, match="Monthly budget reached"):
        budget.check(_secrets(JOB_PIPELINE_MONTHLY_BUDGET=5, JOB_PIPELINE_BUDGET_MODE="stop"))
    budget.check(_secrets(JOB_PIPELINE_MONTHLY_BUDGET=5, JOB_PIPELINE_BUDGET_MODE="warn"))
    budget.check(_secrets(JOB_PIPELINE_MONTHLY_BUDGET=0, JOB_PIPELINE_BUDGET_MODE="stop"))
    st = budget.status(_secrets(JOB_PIPELINE_MONTHLY_BUDGET=10, JOB_PIPELINE_BUDGET_MODE="warn"))
    assert st["spent_usd"] == 5.0 and st["remaining_usd"] == 5.0 and not st["over"]


def test_budget_routes_write_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_pipeline import paths

    env = tmp_path / ".env"
    monkeypatch.setattr(paths, "ENV_FILE", env)
    c = TestClient(create_app(config_path=str(_write_isolated_config(tmp_path))))
    r = c.put("/api/budget", json={"monthly_usd": 12.5, "mode": "stop"}).json()
    assert r["budget_usd"] == 12.5 and r["mode"] == "stop"
    assert "JOB_PIPELINE_MONTHLY_BUDGET=12.5" in env.read_text()
    assert c.get("/api/today").json()["budget"]["budget_usd"] == 12.5
