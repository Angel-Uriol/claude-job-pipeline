"""Jobi: change cards, the command allowlist, tools, hand-off and the routes.

All offline: Claude is never called (run_turn is replaced or not reached),
and board lookups are faked.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from job_pipeline.assistant import agent, commands, proposals, tools
from job_pipeline.assistant.proposals import ProposalError, ResumeEdit
from job_pipeline.ui import create_app
from tests.conftest import EXAMPLE_PROFILE
from tests.test_ui_server import _write_isolated_config


@pytest.fixture
def prof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private copy of the fictional profile, pinned as the active one."""
    cfg = _write_isolated_config(tmp_path)
    for name in ("resume_content.yaml", "target_companies.yaml"):
        shutil.copy(EXAMPLE_PROFILE / name, tmp_path / name)
    monkeypatch.setenv("JOB_PIPELINE_PROFILE_DIR", str(tmp_path))
    from job_pipeline import chrome_queue

    monkeypatch.setattr(chrome_queue, "_DEFAULT_BASE", tmp_path / "chrome_runs")
    return cfg


def _ctx(cfg: Path) -> tools.ToolContext:
    return tools.ToolContext(config_path=cfg)


# --- proposals ---------------------------------------------------------------------


def test_profile_card_is_not_saved_until_applied(prof: Path) -> None:
    before = prof.read_text()
    p = proposals.propose_profile({"headline": "Data engineer"}, "user asked", prof)
    assert prof.read_text() == before  # nothing written yet
    assert p.after == {"headline": "Data engineer"} and p.status == "pending"
    out = proposals.apply(p.id, prof)
    assert out["changed"] == "profile"
    assert yaml.safe_load(prof.read_text())["profile"]["headline"] == "Data engineer"
    assert prof.read_text().startswith("# Pipeline config")  # header kept
    assert (prof.parent / "config.yaml.bak").exists()
    assert proposals.get(p.id).status == "applied"
    with pytest.raises(ProposalError):
        proposals.apply(p.id, prof)  # can't apply twice


def test_profile_card_rejects_unknown_and_invalid(prof: Path) -> None:
    with pytest.raises(ProposalError, match="Unknown profile fields"):
        proposals.propose_profile({"favourite_colour": "blue"}, "", prof)
    with pytest.raises(ProposalError, match="isn't valid"):
        proposals.propose_profile({"years_experience": "lots"}, "", prof)


def test_resume_card_warns_on_new_numbers_and_keeps_ids(prof: Path) -> None:
    resume = prof.parent / "resume_content.yaml"
    raw = yaml.safe_load(resume.read_text())
    key, exp = next(iter(raw["experiences"].items()))
    first = exp["bullets"][0]["id"]
    edits = [ResumeEdit(op="replace_bullet", bullet_id=first, text="Cut costs 87%"),
             ResumeEdit(op="add_bullet", key=key, text="Mentored new engineers")]
    p = proposals.propose_resume(edits, "user asked", prof)
    assert any("87%" in w for w in p.warnings)
    proposals.apply(p.id, prof)
    saved = yaml.safe_load(resume.read_text())["experiences"][key]["bullets"]
    assert saved[0] == {**saved[0], "id": first, "text": "Cut costs 87%"}
    assert saved[-1]["text"] == "Mentored new engineers"
    assert saved[-1]["id"] not in {b["id"] for b in exp["bullets"]}


def test_resume_card_unknown_bullet(prof: Path) -> None:
    with pytest.raises(ProposalError, match="No bullet"):
        proposals.propose_resume([ResumeEdit(op="remove_bullet", bullet_id="b999")], "", prof)


def test_company_card_adds_board_and_target(prof: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_verify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**r, "platform": "greenhouse", "slug": "figma"} if r["name"] == "Figma"
                else r for r in rows]

    monkeypatch.setattr("job_pipeline.onboarding.companies.verify_companies", fake_verify)
    p = proposals.propose_companies([{"name": "Figma", "tier": 1}, {"name": "Tiny Co"}],
                                    ["palantir"], "user asked", prof)
    assert "Figma" in p.after and "palantir" not in p.after
    assert any("Tiny Co" in w for w in p.warnings)
    proposals.apply(p.id, prof)
    cfg = yaml.safe_load(prof.read_text())
    assert {"name": "Figma", "slug": "figma"} in cfg["scrapers"]["greenhouse"]
    assert not cfg["scrapers"]["lever"]  # palantir removed
    targets = yaml.safe_load((prof.parent / "target_companies.yaml").read_text())["companies"]
    names = {t["name"] for t in targets}
    assert {"Figma", "Tiny Co"} <= names and "palantir" not in names


def test_settings_card_validates(prof: Path) -> None:
    with pytest.raises(ProposalError, match="Can't change"):
        proposals.propose_settings({"llm.smart_model": "x"}, "", prof)
    with pytest.raises(ProposalError, match="isn't valid"):
        proposals.propose_settings({"filters.max_age_days": "soon"}, "", prof)
    p = proposals.propose_settings({"scoring.max_jobs_per_run": 10}, "cheaper", prof)
    assert p.before == {"scoring.max_jobs_per_run": 40}
    proposals.apply(p.id, prof)
    assert yaml.safe_load(prof.read_text())["scoring"]["max_jobs_per_run"] == 10


def test_search_cards_hand_back_client_actions(prof: Path) -> None:
    p = proposals.propose_action("start_search", "Find new jobs", "")
    assert proposals.apply(p.id, prof)["client_action"] == {"run": "daily"}
    p = proposals.propose_action("start_premium_search", "x", "", {"companies": ["NVIDIA"]})
    out = proposals.apply(p.id, prof)
    assert out["client_action"] == {"run": "premium"}


# --- commands --------------------------------------------------------------------------


def test_command_allowlist_only(prof: Path) -> None:
    with pytest.raises(ProposalError, match="Not an allowed command"):
        proposals.propose_action("run_command", "x", "", {"command": "rm -rf /"})
    assert commands.run_command("bash")["ok"] is False


def test_commands_never_use_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_run(argv: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append({"argv": argv, **kw})
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert commands.run_command("doctor")["ok"] is True
    assert isinstance(seen[0]["argv"], list) and seen[0]["shell"] is False


def test_command_card_runs_and_reports(prof: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(commands.COMMANDS, "doctor", commands.Command(
        "doctor", "Check the app's setup", "", "job-pipeline doctor",
        lambda: {"ok": True, "log": "All good."}))
    p = proposals.propose_action("run_command", "doctor", "", {"command": "doctor"})
    assert p.payload["shown"] == "job-pipeline doctor"
    out = proposals.apply(p.id, prof)
    assert out["ok"] is True and out["log"] == "All good."


# --- tools --------------------------------------------------------------------------------


def test_tool_call_validates_and_emits_card(prof: Path) -> None:
    cards: list[proposals.Proposal] = []
    ctx = tools.ToolContext(config_path=prof, on_proposal=cards.append)
    text, err = tools.call("propose_profile_change",
                           {"changes": {"headline": "Hi"}, "reason": "x"}, ctx)
    assert not err and json.loads(text)["proposed"] is True and len(cards) == 1
    text, err = tools.call("propose_profile_change", {"reason": "missing changes"}, ctx)
    assert err and "invalid input" in text
    text, err = tools.call("no_such_tool", {}, ctx)
    assert err


def test_read_tools(prof: Path) -> None:
    ctx = _ctx(prof)
    assert json.loads(tools.call("get_profile", {}, ctx)[0])["profile"]["name"]
    comp = json.loads(tools.call("get_companies", {}, ctx)[0])
    assert "stripe" in comp["job_boards"]["greenhouse"]
    settings = json.loads(tools.call("get_search_settings", {}, ctx)[0])
    assert settings["scoring.max_jobs_per_run"]["value"] == 40
    assert json.loads(tools.call("list_jobs", {}, ctx)[0])["total"] == 0


def test_handoff_has_context_but_no_secrets(prof: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-123")
    from job_pipeline.chrome_queue import ChromeQueue

    q = ChromeQueue()
    rec = q.enqueue(scope="mix", ad_hoc_companies=["NVIDIA"], premium=True)
    q.fail(rec["id"], error="Claude in Chrome isn't connected")
    ctx = tools.ToolContext(config_path=prof, pipeline={"last_run_error": "boom"})
    text = tools.build_handoff("Chrome search failed", "search NVIDIA", ctx)
    assert "chrome_claude_runbook.md" in text and "NVIDIA" in text and "boom" in text
    assert "sk-ant" not in text and "never log in" in text


# --- agent history + demo ------------------------------------------------------------------


def test_history_alternates_roles(prof: Path) -> None:
    agent.reset_history()
    agent.append_history("assistant", "hello")  # leading assistant is dropped
    agent.append_history("user", "hi")
    agent.append_history("note", "Applied: x")  # notes count as user context
    agent.append_history("assistant", "done")
    msgs = agent._past_messages()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "Applied: x" in msgs[0]["content"]


def test_demo_refuses(prof: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_pipeline.llm.base import LLMError

    monkeypatch.setattr("job_pipeline.demo.is_demo", lambda *a, **k: True)
    with pytest.raises(LLMError, match="demo"):
        agent.run_turn("hi", page="/", ctx=_ctx(prof), emit=lambda e: None)


# --- routes ------------------------------------------------------------------------------------


def test_routes_stream_and_apply(prof: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_turn(message: str, *, page: str, ctx: tools.ToolContext,
                  emit: Any, secrets: Any = None) -> dict[str, Any]:
        emit({"type": "status", "text": "Reading your profile…"})
        ctx.on_proposal = lambda p: emit({"type": "proposal", "card": p.model_dump()})
        tools.call("propose_profile_change",
                   {"changes": {"headline": "New headline"}, "reason": message}, ctx)
        emit({"type": "text", "text": "Review the card."})
        emit({"type": "done", "cost_usd": 0.01, "text": "Review the card."})
        return {}

    monkeypatch.setattr(agent, "run_turn", fake_turn)
    c = TestClient(create_app(config_path=str(prof)))
    r = c.post("/api/jobi/chat", json={"message": "change my headline", "page": "/profile"})
    events = [json.loads(line) for line in r.text.splitlines()]
    assert [e["type"] for e in events] == ["status", "proposal", "text", "done"]
    card = events[1]["card"]
    state = c.get("/api/jobi").json()
    assert state["model"] == "claude-opus-5-5" and state["cards"][-1]["id"] == card["id"]
    applied = c.post(f"/api/jobi/proposals/{card['id']}/apply").json()
    assert applied["card"]["status"] == "applied"
    assert yaml.safe_load(prof.read_text())["profile"]["headline"] == "New headline"
    assert any(m["text"].startswith("Applied:") for m in c.get("/api/jobi").json()["history"])
    assert c.post("/api/jobi/proposals/nope/apply").status_code == 404
    assert c.post("/api/jobi/reset").json()["ok"] is True


def test_route_reports_llm_errors(prof: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_pipeline.llm.base import LLMError

    def boom(*a: Any, **k: Any) -> dict[str, Any]:
        raise LLMError("No Anthropic API key saved")

    monkeypatch.setattr(agent, "run_turn", boom)
    c = TestClient(create_app(config_path=str(prof)))
    r = c.post("/api/jobi/chat", json={"message": "hi"})
    assert json.loads(r.text.splitlines()[0]) == {"type": "error",
                                                  "text": "No Anthropic API key saved"}
    assert c.get("/api/jobi").json()["busy"] is False
