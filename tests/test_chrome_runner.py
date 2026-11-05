"""App-launched premium search (Claude Code + Chrome). The browser session is
mocked — these tests cover queueing, importing, the applied guard, failure
handling and the safety configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from job_pipeline import chrome_queue, chrome_runner, tracker_db
from job_pipeline.config import load_config
from job_pipeline.store import JobStore
from tests.conftest import EXAMPLE_CONFIG


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(chrome_queue, "_DEFAULT_BASE", tmp_path / "runs")
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.paths.jobs_json = str(tmp_path / "data" / "jobs.json")
    cfg.tracker.db_path = str(tmp_path / "data" / "tracker.db")
    store = JobStore(cfg.paths.jobs_json)
    store.load()
    return cfg, store


def _queue(profile: str | None = "angel", **kw: Any) -> dict[str, Any]:
    return chrome_queue.ChromeQueue().enqueue(
        scope="mix", ad_hoc_companies=["NVIDIA"], score_after=True,
        profile=profile, premium=True, **kw)


JOB = {"company": "NVIDIA", "job_id": "JR1", "title": "Data Engineer",
       "url": "https://nvidia.example/jr1", "location": "Santa Clara, CA",
       "description": "Build pipelines."}


def test_safety_config_is_browse_only() -> None:
    assert "Bash" in chrome_runner.DENIED_TOOLS and "Write" in chrome_runner.DENIED_TOOLS
    assert not any("form_input" in t or "file_upload" in t for t in chrome_runner.ALLOWED_TOOLS)
    assert "NEVER log in" in chrome_runner.SEARCH_PROMPT


def test_describe_tool_calls() -> None:
    assert chrome_runner._describe("mcp__claude-in-chrome__navigate",
                                   {"url": "https://x.example/jobs"}) == \
        "Visiting https://x.example/jobs"
    assert chrome_runner._describe("mcp__claude-in-chrome__computer", {}) is None


def test_run_pending_imports_scores_and_finishes(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store = env
    rec = _queue()
    _queue(profile="someone-else")  # not ours — must stay pending
    monkeypatch.setattr(chrome_runner, "run_premium", lambda r, c, **kw: {
        "jobs": [JOB, {**JOB, "job_id": "JR2", "url": "https://nvidia.example/jr2"}],
        "notes": "searched careers site", "cost": 0.4, "pages_visited": 7})
    lines: list[str] = []
    scored: list[bool] = []
    counts = chrome_runner.run_pending_premium(
        cfg, store, profile="angel", progress=lines.append,
        score=lambda: scored.append(True) or {"ok": 1})
    assert counts["requests"] == 1 and counts["imported"] == 2 and scored == [True]
    assert {j.job_id for j in store.jobs} == {"JR1", "JR2"}
    q = chrome_queue.ChromeQueue()
    done = {r["id"]: r for r in q.list_status()}
    assert done[rec["id"]]["status"] == "done"
    assert done[rec["id"]]["summary"]["pages_visited"] == 7
    assert len(q.pending_for("someone-else")) == 1
    assert any("Found 2 roles" in line for line in lines)


def test_run_pending_skips_tracked_jobs(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store = env
    tracker_db.insert_jobs(cfg, [{"company": "NVIDIA", "role": "Data Engineer", "job_id": "JR1"}])
    _queue()
    monkeypatch.setattr(chrome_runner, "run_premium", lambda r, c, **kw: {
        "jobs": [JOB], "notes": "", "cost": None, "pages_visited": 1})
    counts = chrome_runner.run_pending_premium(cfg, store, profile="angel")
    assert counts["imported"] == 0 and counts["already_tracked"] == 1


def test_run_pending_records_failure(env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store = env
    rec = _queue()

    def boom(*a: Any, **k: Any) -> Any:
        raise chrome_runner.ChromeRunError(chrome_runner.NOT_CONNECTED_HELP)

    monkeypatch.setattr(chrome_runner, "run_premium", boom)
    counts = chrome_runner.run_pending_premium(cfg, store, profile="angel")
    assert counts["failed"] == 1 and "Reconnect" in counts["error"]
    # Not connected → stays queued, so it runs after reconnecting.
    assert [r["id"] for r in chrome_queue.ChromeQueue().pending_for("angel")] == [rec["id"]]

    def other(*a: Any, **k: Any) -> Any:
        raise chrome_runner.ChromeRunError("site blocked everything")

    monkeypatch.setattr(chrome_runner, "run_premium", other)
    chrome_runner.run_pending_premium(cfg, store, profile="angel")
    status = {r["id"]: r for r in chrome_queue.ChromeQueue().list_status()}
    assert status[rec["id"]]["status"] == "error"


def test_nothing_queued(env: Any) -> None:
    cfg, store = env
    assert chrome_runner.run_pending_premium(cfg, store, profile="angel")["requests"] == 0


def test_check_chrome_reports_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chrome_runner, "_require_cli", lambda s: "/bin/claude")
    monkeypatch.setattr(chrome_runner, "_run", lambda coro: (coro.close(), (
        "NOT_CONNECTED Browser extension is not connected.",
        ["mcp__claude-in-chrome__tabs_context_mcp"], 0.01))[1])
    out = chrome_runner.check_chrome()
    assert out["connected"] is False and "/chrome" in out["detail"]
    monkeypatch.setattr(chrome_runner, "_run", lambda coro: (coro.close(), (
        "CONNECTED", ["mcp__claude-in-chrome__tabs_context_mcp"], 0.01))[1])
    assert chrome_runner.check_chrome()["connected"] is True


def test_premium_phase_is_allowed() -> None:
    from job_pipeline.ui.server import _ALLOWED_PHASES

    assert "premium" in _ALLOWED_PHASES


def test_premium_stops_early_when_chrome_not_connected(
        env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The quick connection test runs first, so a full browsing session is
    never started (or paid for) when Chrome isn't reachable."""
    cfg, store = env
    sessions: list[str] = []
    monkeypatch.setattr(chrome_runner, "_require_cli", lambda s: "/bin/claude")
    monkeypatch.setattr(chrome_runner, "check_chrome", lambda s=None: {
        "connected": False, "detail": chrome_runner.NOT_CONNECTED_HELP})
    monkeypatch.setattr(chrome_runner, "_session", lambda *a, **k: sessions.append("x"))
    rec = _queue()
    counts = chrome_runner.run_pending_premium(cfg, store, profile="angel")
    assert counts["failed"] == 1 and counts["error_code"] == "chrome_not_connected"
    assert sessions == []
    # Still queued, so it runs once Chrome is reconnected.
    assert [r["id"] for r in chrome_queue.ChromeQueue().pending_for("angel")] == [rec["id"]]
