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
        raise chrome_runner.ChromeRunError(chrome_runner.NOT_CONNECTED_HELP,
                                           chrome_runner.CODE_NOT_CONNECTED)

    monkeypatch.setattr(chrome_runner, "run_premium", boom)
    counts = chrome_runner.run_pending_premium(cfg, store, profile="angel")
    assert counts["failed"] == 1 and "claude.ai/chrome" in counts["error"]
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


def _full_login(monkeypatch: pytest.MonkeyPatch, logged_in: bool = True) -> None:
    monkeypatch.setattr("job_pipeline.llm.cli_client.cli_account", lambda *a, **k: {
        "logged_in": logged_in, "method": "claude.ai" if logged_in else "none",
        "email": "jordan@example.com" if logged_in else "", "plan": "pro"})


def test_check_chrome_reports_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chrome_runner, "_require_cli", lambda s: "/bin/claude")
    _full_login(monkeypatch)
    monkeypatch.setattr(chrome_runner, "_run", lambda coro: (coro.close(), (
        "NOT_CONNECTED Browser extension is not connected.",
        ["mcp__claude-in-chrome__tabs_context_mcp"], 0.01))[1])
    out = chrome_runner.check_chrome()
    assert out["connected"] is False and out["code"] == chrome_runner.CODE_NOT_CONNECTED
    assert "claude.ai/chrome" in out["detail"]
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
        "connected": False, "detail": chrome_runner.NOT_CONNECTED_HELP,
        "code": chrome_runner.CODE_NOT_CONNECTED})
    monkeypatch.setattr(chrome_runner, "_session", lambda *a, **k: sessions.append("x"))
    rec = _queue()
    counts = chrome_runner.run_pending_premium(cfg, store, profile="angel")
    assert counts["failed"] == 1 and counts["error_code"] == "chrome_not_connected"
    assert sessions == []
    # Still queued, so it runs once Chrome is reconnected.
    assert [r["id"] for r in chrome_queue.ChromeQueue().pending_for("angel")] == [rec["id"]]


# The real cause found on 2026-11-04: Claude Code reaches the extension via
# Anthropic's browser bridge, which rejects the setup-token with a 403.
BRIDGE_403 = ('[Claude in Chrome] Bridge received: {"type":"error","error":"OAuth token '
              'forbidden (403) — missing scope, org access, or network not allowed by org"}')
BRIDGE_OK = ("[Claude in Chrome] Paired with Chrome extension (duration: 869ms)\n"
             "[Claude in Chrome] Selected Chrome extension: 59301c7b...\n"
             "[Claude in Chrome] Tool call completed: tabs_context_mcp (9a73d4a1) in 234ms")


def test_debug_log_diagnosis() -> None:
    assert chrome_runner.diagnose_debug_log(BRIDGE_403) == chrome_runner.CODE_NEEDS_LOGIN
    assert chrome_runner.diagnose_debug_log(BRIDGE_OK) == ""
    assert chrome_runner.diagnose_debug_log("Connection timeout") == \
        chrome_runner.CODE_NOT_CONNECTED


def test_check_chrome_names_the_missing_full_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chrome_runner, "_require_cli", lambda s: "/bin/claude")
    _full_login(monkeypatch, logged_in=False)

    def fake_run(coro: Any) -> Any:
        log = coro.cr_frame.f_locals["debug_file"]
        log.write_text(BRIDGE_403)
        coro.close()
        return ("NOT_CONNECTED Browser extension is not connected.",
                ["mcp__claude-in-chrome__tabs_context_mcp"], 0.0)

    monkeypatch.setattr(chrome_runner, "_run", fake_run)
    out = chrome_runner.check_chrome()
    assert out["code"] == chrome_runner.CODE_NEEDS_LOGIN
    assert "Sign in for Chrome" in out["detail"]


def test_chrome_sessions_skip_the_setup_token_when_fully_signed_in(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from job_pipeline.config import Secrets

    s = Secrets(CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat-setup-token")  # type: ignore[call-arg]
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-from-shell")
    _full_login(monkeypatch)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in chrome_runner._child_env("/bin/claude", s)
    _full_login(monkeypatch, logged_in=False)
    env = chrome_runner._child_env("/bin/claude", s)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-setup-token"


def test_readiness_finds_the_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Default" / "Extensions" / chrome_runner.EXTENSION_ID).mkdir(parents=True)
    monkeypatch.setattr(chrome_runner, "_chrome_profile_roots", lambda: [tmp_path])
    assert chrome_runner.extension_installed() is True
    monkeypatch.setattr(chrome_runner, "_chrome_profile_roots", lambda: [tmp_path / "nope"])
    assert chrome_runner.extension_installed() is None


def test_check_trusts_the_log_over_the_models_reading(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Haiku once answered NOT_CONNECTED for "This session has no tab group of
    its own" — a connected extension. The debug log decides."""
    monkeypatch.setattr(chrome_runner, "_require_cli", lambda s: "/bin/claude")
    _full_login(monkeypatch)

    def fake_run(coro: Any) -> Any:
        coro.cr_frame.f_locals["debug_file"].write_text(BRIDGE_OK)
        coro.close()
        return ("NOT_CONNECTED: This session has no tab group of its own.",
                ["mcp__claude-in-chrome__tabs_context_mcp"], 0.0)

    monkeypatch.setattr(chrome_runner, "_run", fake_run)
    out = chrome_runner.check_chrome()
    assert out["connected"] is True and "jordan@example.com" in out["detail"]


def test_out_of_steps_search_is_salvaged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A search that hits the step limit is resumed once, without browser
    tools, to return the roles it already verified."""
    import asyncio

    import claude_agent_sdk
    from claude_agent_sdk import ResultMessage, SystemMessage

    from job_pipeline.config import Secrets

    calls: list[Any] = []

    async def fake_query(*, prompt: str, options: Any) -> Any:
        calls.append(options)
        if options.resume is None:
            yield SystemMessage(subtype="init", data={"session_id": "sess-1"})
            raise RuntimeError("Claude Code returned an error result: Reached maximum "
                               "number of turns (120)")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                            is_error=False, num_turns=1, session_id="sess-1",
                            total_cost_usd=0.02, result='{"jobs": [], "notes": "partial"}')

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(chrome_runner, "_child_env", lambda cli, s: {})
    text, _tools, cost = asyncio.run(chrome_runner._session(
        "search", cli="/bin/claude", secrets=Secrets(), model="claude-sonnet-5-5",
        max_turns=120, progress=None, salvage=True))
    assert '"partial"' in text and cost == pytest.approx(0.02)
    assert calls[1].resume == "sess-1" and calls[1].allowed_tools == []
    assert "mcp__claude-in-chrome__navigate" in calls[1].disallowed_tools
