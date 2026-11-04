"""Premium advisor: when to recommend a Chrome search, and how it's priced."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_pipeline import chrome_queue, plan_usage, premium_advisor
from job_pipeline.config import NoApiEntry, Secrets, load_config
from job_pipeline.models import Job, Source, Status, Verdict
from tests.conftest import EXAMPLE_CONFIG


@pytest.fixture(autouse=True)
def queue_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chrome_queue, "_DEFAULT_BASE", tmp_path / "runs")
    plan_usage._cache.update(at=0.0, value=None)


def _job(company: str, *, score: float = 8.5, desc: str = "x" * 2000, n: int = 1,
         visa: str = "sponsors", **kw: Any) -> Job:
    return Job(company=company, job_id=str(n), title=f"Engineer {n}", source=Source.GREENHOUSE,
               url=f"https://ex.example/{n}", location="SF", description=desc,
               discovered_at=datetime.now(UTC).isoformat(), fit_score=score,
               fit_verdict=Verdict.APPLY, extracted={"visa_stance": visa}, **kw)


def _cfg() -> Any:
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.profile.dream_companies = ["Stripe", "Databricks"]
    return cfg


def test_no_board_is_high_priority_and_dry_dream_board_medium() -> None:
    cfg = _cfg()  # example: uber in no_api; stripe/databricks dream with boards
    jobs = [_job("databricks", n=1, status=Status.APPLIED)]  # databricks board works
    recs = premium_advisor.recommend(cfg, jobs, profile="p")
    by = {(r["company"], r["signal"]): r["priority"] for r in recs}
    assert by[("Uber", "no_board")] == "high"
    assert by[("Stripe", "dry_board")] == "medium"  # dream, nothing came through
    assert ("Databricks", "dry_board") not in by     # had jobs (even applied ones)
    assert recs[0]["priority"] == "high"


def test_no_dry_board_signal_before_any_scrape() -> None:
    recs = premium_advisor.recommend(_cfg(), [], profile="p")
    assert all(r["signal"] != "dry_board" for r in recs)


def test_job_signals_thin_description_and_unknown_visa() -> None:
    cfg = _cfg()
    cfg.profile.needs_sponsorship = True
    jobs = [
        _job("datadog", n=1, desc="Short JD. Description truncated."),
        _job("datadog", n=2, visa="unknown"),
        _job("datadog", n=3, score=5.0, desc="short"),          # weak → not suggested
        _job("datadog", n=4, desc="short", status=Status.DISMISSED),
    ]
    sig = {r.get("job_key"): r["signal"] for r in premium_advisor.recommend(cfg, jobs, profile="p")
           if r["kind"] == "job"}
    assert sig == {"1": "thin_jd", "2": "visa_unknown"}


def test_recently_queued_companies_are_not_suggested_again() -> None:
    chrome_queue.ChromeQueue().enqueue(scope="mix", ad_hoc_companies=["Uber"],
                                       profile="p", premium=True)
    recs = premium_advisor.recommend(_cfg(), [], profile="p")
    assert "Uber" not in {r["company"] for r in recs}
    # Another profile still gets it.
    assert "Uber" in {r["company"] for r in premium_advisor.recommend(_cfg(), [], profile="q")}


def test_uses_users_own_spelling() -> None:
    cfg = _cfg()
    cfg.scrapers.no_api = [NoApiEntry(name="nvidia", careers_url="https://n.example",
                                      platform="custom")]
    cfg.profile.dream_companies = ["NVIDIA"]
    assert premium_advisor.recommend(cfg, [], profile="p")[0]["company"] == "NVIDIA"


def test_estimate_learns_from_past_runs() -> None:
    recs = [{"kind": "company", "company": "A"}, {"kind": "company", "company": "B"}]
    default = premium_advisor.estimate(recs, "p")
    assert default["learned_from_runs"] == 0 and default["usd_low"] == 1.0
    q = chrome_queue.ChromeQueue()
    rec = q.enqueue(scope="mix", ad_hoc_companies=["X", "Y"], profile="p", premium=True)
    q.claim(rec["id"])
    q.finish(rec["id"], summary={"cost_usd": 4.0, "plan_pct_used": 6.0})
    learned = premium_advisor.estimate(recs, "p")
    assert learned["learned_from_runs"] == 1
    assert learned["usd_low"] == 2.8 and learned["usd_high"] == 5.6  # 2/company ×0.7/×1.4
    assert learned["plan_pct"] == 6.0  # 3% per company × 2


def test_usage_message_api_key_mode() -> None:
    est = {"usd_low": 1.0, "usd_high": 3.0, "learned_from_runs": 0, "plan_pct": None}
    msg = premium_advisor.usage_message(est, Secrets(JOB_PIPELINE_AUTH_MODE="api_key"))
    assert msg["mode"] == "api_key" and "$1.00–$3.00" in msg["text"]


def test_usage_message_subscription_with_live_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_usage, "get_plan_usage", lambda s=None, **k: {
        "available": True, "five_hour": {"used_pct": 85.0, "left_pct": 15.0, "resets_at": None},
        "seven_day": {"used_pct": 20.0, "left_pct": 80.0, "resets_at": None}})
    est = {"usd_low": 1.0, "usd_high": 3.0, "learned_from_runs": 2, "plan_pct": 7.5}
    msg = premium_advisor.usage_message(est, Secrets(JOB_PIPELINE_AUTH_MODE="claude_code"))
    assert msg["mode"] == "claude_code" and msg["warn"] is True
    assert "85% of your 5-hour session" in msg["text"] and "~7.5%" in msg["text"]


def test_usage_message_subscription_without_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_usage, "get_plan_usage",
                        lambda s=None, **k: {"available": False, "detail": "nope"})
    est = {"usd_low": 1.0, "usd_high": 3.0, "learned_from_runs": 0, "plan_pct": None}
    msg = premium_advisor.usage_message(est, Secrets(JOB_PIPELINE_AUTH_MODE="claude_code"))
    assert msg["warn"] is False and "/usage" in msg["text"] and "measures" in msg["text"]


def test_plan_usage_api_key_mode_needs_no_network() -> None:
    assert plan_usage.get_plan_usage(Secrets(JOB_PIPELINE_AUTH_MODE="api_key"))["available"] \
        is False


def test_plan_usage_parses_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"five_hour": {"utilization": 34, "resets_at": "2026-11-04T10:00:00Z"},
                    "seven_day": {"utilization": 12.5}}

    seen: dict[str, Any] = {}
    monkeypatch.setattr(plan_usage.httpx, "get",
                        lambda url, **kw: seen.update(kw) or Resp())
    monkeypatch.setattr(plan_usage, "_cli_version", lambda p: "2.1.270")
    out = plan_usage.get_plan_usage(Secrets(JOB_PIPELINE_AUTH_MODE="claude_code",
                                            CLAUDE_CODE_OAUTH_TOKEN="tok"), fresh=True)
    assert out["available"] and out["five_hour"]["left_pct"] == 66.0
    assert seen["headers"]["User-Agent"] == "claude-code/2.1.270"


def test_queue_endpoint_queues_jobs_and_companies(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from job_pipeline.store import JobStore
    from job_pipeline.ui import create_app

    cfg_text = EXAMPLE_CONFIG.read_text().replace(
        'jobs_json:    "data/jobs.json"', f'jobs_json:    "{tmp_path / "jobs.json"}"')
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg_text)
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_job("stripe", n=7))
    store.save()
    client = TestClient(create_app(config_path=cfg_path))
    recs = client.get("/api/premium/recommendations").json()
    assert {"recommendations", "estimate", "usage", "pending"} <= set(recs)
    assert client.post("/api/premium/queue", json={}).status_code == 400
    assert client.post("/api/premium/queue", json={"job_keys": ["nope"]}).status_code == 404
    body = client.post("/api/premium/queue",
                       json={"companies": ["Uber"], "job_keys": ["7"]}).json()
    rec = body["queued"]
    assert rec["ad_hoc_companies"] == ["Uber"] and rec["job_urls"] == ["https://ex.example/7"]
    assert body["usage"]["text"]
