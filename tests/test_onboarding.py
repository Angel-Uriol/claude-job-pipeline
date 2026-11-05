"""Setup questionnaire: interview engine, profile writer, and /api/setup routes.

A scripted fake Claude stands in for the model, so these run offline.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel

from job_pipeline.config import Config, load_config
from job_pipeline.onboarding import interview as iv
from job_pipeline.onboarding.env_file import mask, set_env_values
from job_pipeline.onboarding.interview import (
    FunctionDraft,
    InterviewState,
    InterviewTurn,
    ProfileDraft,
    prefill_from_resume,
    resume_received,
    start,
    step,
)
from job_pipeline.onboarding.writer import build_config, build_files, write_files

COMPLETE = ProfileDraft(
    name="Sam Lee", email="sam@example.com", years_experience=3,
    target_functions=[FunctionDraft(name="Backend Engineering", tier="A")],
    target_levels=["mid"], location_preference="Austin or remote",
    location_tokens=["austin", ", tx"], allow_remote=True,
    work_authorization="US citizen", needs_sponsorship=False,
    preferred_skills=["python", "go"], honest_gaps=[],
)


class ScriptedLLM:
    """Returns queued structured answers; records prompts."""

    auth_mode = "api_key"

    def __init__(self, answers: list[BaseModel]) -> None:
        self.answers: Iterator[BaseModel] = iter(answers)
        self.prompts: list[str] = []

    def structured(self, prompt: str, schema: type[Any], **kw: Any) -> Any:
        self.prompts.append(prompt)
        # StopIteration inside a worker thread hangs the test client — fail loudly.
        answer = next(self.answers, None)
        assert answer is not None, f"ScriptedLLM ran out of answers (asked for {schema})"
        return answer

    def text(self, prompt: str, **kw: Any) -> str:
        return "OK"


@pytest.fixture
def profile_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "profile"
    monkeypatch.setenv("JOB_PIPELINE_PROFILE_DIR", str(d))
    return d


# --- interview engine ------------------------------------------------------


def _turn(q: str = "Next?", done: bool = False, **updates: Any) -> InterviewTurn:
    return InterviewTurn(updates=ProfileDraft(**updates), next_question=q,
                         options=["A", "B"], done=done)


def test_chat_opens_with_resume_question(profile_dir: Path) -> None:
    state = start(InterviewState())
    assert state.phase == "start"
    assert state.messages[0].options == iv.START_OPTIONS
    # No LLM needed for the opener; an empty step is a no-op.
    assert step(state, None, llm=ScriptedLLM([]), model="m").phase == "start"


def test_yes_goes_to_upload_without_llm(profile_dir: Path) -> None:
    state = start(InterviewState())
    state = step(state, "Yes, I'll upload it", llm=ScriptedLLM([]), model="m")
    assert state.phase == "upload"
    assert state.messages[-1].kind == "upload"


def test_no_resume_starts_build_mode_round1(profile_dir: Path) -> None:
    llm = ScriptedLLM([_turn("What industry are you in?")])
    state = step(start(InterviewState()), "No, let's build one together", llm=llm, model="m")
    assert state.phase == "round1" and state.build_mode
    assert state.messages[-1].text == "What industry are you in?"
    assert state.messages[-1].options == ["A", "B"]
    assert "industry" in state.missing()
    assert "NO resume" in llm.prompts[0]  # build-mode guidance sent


def test_resume_received_moves_to_round1() -> None:
    state = InterviewState(phase="upload", resume_mode="upload")
    resume_received(state, {"header": {"name": "Sam Lee"}, "experiences": {
        "x": {"bullets": [{"text": "a"}, {"text": "b"}]}}})
    assert state.phase == "round1"
    assert "1 role and 2 bullet points" in state.messages[-1].text
    assert "industry" not in state.missing()  # upload mode: no build fields


def test_model_cannot_finish_round1_early(profile_dir: Path) -> None:
    state = InterviewState(phase="round1", resume_mode="upload")
    state = step(state, "hi", llm=ScriptedLLM([_turn("All done!", done=True)]), model="m")
    assert state.phase == "round1"
    assert "Before we move on" in state.messages[-1].text


def test_round1_complete_hands_off_to_market(profile_dir: Path) -> None:
    state = InterviewState(phase="round1", resume_mode="upload")
    turn = InterviewTurn(updates=COMPLETE, next_question="Let me look at real jobs.", done=True)
    state = step(state, "everything", llm=ScriptedLLM([turn]), model="m")
    assert state.phase == "market1"
    assert state.messages[-1].kind == "note"


def test_round2_is_capped(profile_dir: Path) -> None:
    state = InterviewState(phase="round2", resume_mode="upload", draft=COMPLETE)
    llm = ScriptedLLM([_turn() for _ in range(iv.ROUND_LIMITS["round2"] + 1)])
    for _ in range(iv.ROUND_LIMITS["round2"] + 1):
        state = step(state, "yes", llm=llm, model="m")
    assert state.phase == "market2"


def test_market_run_posts_jobs_and_starts_next_round(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from job_pipeline.onboarding import market

    monkeypatch.setattr(market, "scan_market", lambda state, **kw: {
        "round": 2, "summary": "Most ask for Kafka.", "boards": 3, "scanned": 40,
        "requirements": [{"skill": "kafka", "count": 3, "confirmed": False}],
        "jobs": [{"company": "Stripe", "title": "Backend Engineer", "url": "u", "location": "SF"}],
    })
    state = InterviewState(phase="market1", resume_mode="upload", draft=COMPLETE)
    llm = ScriptedLLM([_turn("3 of 8 roles ask for Kafka — used it?")])
    state = market.run_market(state, llm=llm, model="m")
    assert state.phase == "round2"
    assert any(m.kind == "market" and m.jobs for m in state.messages)
    assert "kafka: in 3 of 1 postings" in llm.prompts[0]
    assert state.messages[-1].text.startswith("3 of 8")


def test_pick_jobs_ranks_by_title_and_caps_per_company() -> None:
    from datetime import UTC, datetime

    from job_pipeline.models import Job, Source
    from job_pipeline.onboarding.market import pick_jobs

    def j(company: str, title: str, n: int) -> Job:
        return Job(company=company, job_id=str(n), title=title, source=Source.GREENHOUSE,
                   url=f"u{n}", location="SF", description="d",
                   discovered_at=datetime.now(UTC).isoformat())

    draft = ProfileDraft(search_keywords=["backend engineer"], excluded_titles=["director"])
    jobs = [j("A", "Backend Engineer", 1), j("A", "Senior Backend Engineer", 2),
            j("A", "Backend Engineer II", 3), j("B", "Director, Backend Engineering", 4),
            j("B", "Sales Lead", 5), j("C", "Backend Engineer", 6)]
    picked = pick_jobs(jobs, draft, exclude_urls={"u6"})
    assert [p.url for p in picked] == ["u1", "u2"]  # 2 per company, excluded/seen dropped


def test_round3_done_builds_resume_in_build_mode(profile_dir: Path) -> None:
    from job_pipeline.resume.parse_resume import ParsedResume

    state = InterviewState(phase="round3", resume_mode="build", draft=COMPLETE)
    llm = ScriptedLLM([
        _turn("You're all set.", done=True),
        ParsedResume(name="Sam Lee", summary="Backend engineer."),
    ])
    state = step(state, "looks good", llm=llm, model="m")
    assert state.done and state.phase == "done"
    assert state.resume_content is not None
    assert state.resume_content["header"]["email"] == "sam@example.com"
    assert state.messages[-1].text.startswith("All set")


def test_prefill_from_resume() -> None:
    state = InterviewState()
    prefill_from_resume(state, {
        "header": {"name": "Sam Lee", "email": "sam@example.com", "location": "Austin, TX"},
        "skills": {"languages": ["Python", "Go"]},
    })
    assert state.draft.name == "Sam Lee"
    assert state.draft.preferred_skills == ["python", "go"]
    assert state.resume_content is not None


def test_old_state_file_is_ignored(profile_dir: Path) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / ".interview.json").write_text('{"phase": "bogus"}')
    assert InterviewState.load().phase == "start"


# --- writer ------------------------------------------------------------------


def _state_with_companies() -> InterviewState:
    return InterviewState(draft=COMPLETE, done=True, companies=[
        {"name": "Stripe", "tier": 1, "selected": True, "platform": "greenhouse",
         "slug": "stripe", "jobs": 100},
        {"name": "Acme", "tier": 2, "selected": True, "platform": None,
         "careers_url": "https://acme.example/jobs"},
        {"name": "Dropped", "tier": 3, "selected": False, "platform": "lever", "slug": "dropped"},
    ])


def test_build_config_maps_companies_and_filters() -> None:
    cfg = Config(**build_config(COMPLETE, _state_with_companies().companies))
    assert [g.slug for g in cfg.scrapers.greenhouse] == ["stripe"]
    assert [n.name for n in cfg.scrapers.no_api] == ["acme"]
    assert cfg.scrapers.lever == []  # unselected company dropped
    assert cfg.filters.location_tokens == ["austin", ", tx"]
    assert cfg.profile.name == "Sam Lee"
    assert any(q.query.endswith("remote") for q in cfg.scrapers.jsearch)
    assert cfg.scrapers.remoteok  # allow_remote → RemoteOK on


def test_build_files_and_write_files(profile_dir: Path) -> None:
    state = _state_with_companies()
    files = build_files(state)
    assert set(files) == {"config.yaml", "resume_content.yaml", "target_companies.yaml"}
    written = write_files(state)
    assert {p.name for p in written} == set(files)
    cfg = load_config(profile_dir / "config.yaml")
    assert cfg.profile.email == "sam@example.com"
    targets = yaml.safe_load((profile_dir / "target_companies.yaml").read_text())
    assert [c["name"] for c in targets["companies"]] == ["stripe", "acme"]
    # Second write backs up the first.
    write_files(state)
    assert (profile_dir / "config.yaml.bak").exists()


# --- .env helper ---------------------------------------------------------------


def test_set_env_values_upserts_and_preserves(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nJSEARCH_API_KEY=abc\nANTHROPIC_API_KEY=old\n")
    set_env_values({"ANTHROPIC_API_KEY": "new", "JOB_PIPELINE_AUTH_MODE": "api_key"}, env)
    text = env.read_text()
    assert "# comment" in text and "JSEARCH_API_KEY=abc" in text
    assert "ANTHROPIC_API_KEY=new" in text and "old" not in text
    assert "JOB_PIPELINE_AUTH_MODE=api_key" in text
    assert oct(env.stat().st_mode)[-3:] == "600"
    assert mask("sk-ant-api03-abcdefgh1234") == "sk-ant-…1234"


# --- HTTP routes -----------------------------------------------------------------


@pytest.fixture
def client(profile_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import job_pipeline.onboarding.env_file as ef
    from job_pipeline import paths
    from job_pipeline.ui import create_app

    monkeypatch.setattr(paths, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ef.paths, "ENV_FILE", tmp_path / ".env")
    return TestClient(create_app(config_path=profile_dir / "config.yaml"))


def test_setup_status_unconfigured(client: TestClient) -> None:
    body = client.get("/api/setup/status").json()
    assert body["configured"] is False
    assert body["auth"]["connected"] is False
    assert body["interview"]["messages"] == []
    assert body["interview"]["required_total"] == len(iv.REQUIRED_FIELDS)


def test_setup_auth_rejects_non_anthropic_key(client: TestClient) -> None:
    res = client.post("/api/setup/auth", json={"mode": "api_key", "api_key": "sk-openai-123"})
    assert res.status_code == 400


def test_setup_interview_opener_needs_no_claude(client: TestClient) -> None:
    body = client.post("/api/setup/interview", json={"message": ""}).json()
    assert body["phase"] == "start" and body["messages"][0]["options"]
    # Answering does need Claude (no key configured in tests).
    res = client.post("/api/setup/interview", json={"message": "No, let's build one"})
    assert res.status_code == 503


def test_setup_full_flow_with_fake_claude(
    client: TestClient, profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import job_pipeline.ui.setup_routes as sr
    from job_pipeline.onboarding import market

    llm = ScriptedLLM([
        InterviewTurn(updates=ProfileDraft(), next_question="What roles?", done=False),
        InterviewTurn(updates=COMPLETE, next_question="Let me check real jobs.", done=True),
        InterviewTurn(next_question="Used Kafka?", options=["Yes", "No"], done=False),
        InterviewTurn(next_question="Thanks!", done=True),
        InterviewTurn(next_question="Senior roles too?", options=["Yes", "No"], done=False),
        InterviewTurn(next_question="Done!", done=True),
    ])
    monkeypatch.setattr(sr, "build_llm", lambda: llm)
    monkeypatch.setattr(market, "scan_market", lambda state, **kw: {
        "round": state.round, "summary": "", "requirements": [], "jobs": [],
        "boards": 0, "scanned": 0})
    monkeypatch.setattr(sr, "suggest_companies", lambda draft, **kw: [
        {"name": "Stripe", "tier": 1, "why": "x", "selected": True}])
    monkeypatch.setattr(sr, "verify_companies", lambda cs: [
        {**c, "platform": "greenhouse", "slug": "stripe", "jobs": 5} for c in cs])

    post = client.post
    assert post("/api/setup/interview", json={"message": ""}).json()["phase"] == "start"
    upload = post("/api/setup/interview", json={"message": "Yes, I'll upload it"}).json()
    assert upload["phase"] == "upload"
    r1 = post("/api/setup/interview", json={"message": "Actually, I don't have one"}).json()
    assert r1["phase"] == "round1" and r1["resume_mode"] == "build"
    # Build mode needs resume material too; supply it via the next turn's updates.
    llm.answers = iter([
        InterviewTurn(updates=COMPLETE.model_copy(update={
            "industry": "software", "experience_entries": [], "projects": [],
            "education": []}), next_question="Let me check real jobs.", done=True),
        InterviewTurn(next_question="Used Kafka?", options=["Yes", "No"], done=False),
        InterviewTurn(next_question="Thanks!", done=True),
        InterviewTurn(next_question="Senior roles too?", options=["Yes", "No"], done=False),
    ])
    assert post("/api/setup/interview", json={"message": "all of it"}).json()["phase"] == "market1"
    r2 = post("/api/setup/market").json()
    assert r2["phase"] == "round2" and r2["round"] == 2
    assert post("/api/setup/interview", json={"message": "Yes"}).json()["phase"] == "market2"
    assert post("/api/setup/market").json()["phase"] == "round3"
    # Build mode → the resume draft is requested when round 3 ends.
    from job_pipeline.resume.parse_resume import ParsedResume

    llm.answers = iter([InterviewTurn(next_question="Done!", done=True),
                        ParsedResume(name="Sam Lee", summary="Engineer.")])
    final = post("/api/setup/interview", json={"message": "No"}).json()
    assert final["done"] is True and final["resume"]["parsed"] is True
    companies = client.post("/api/setup/companies/suggest").json()["companies"]
    assert companies[0]["name"] == "Stripe"
    verified = client.post("/api/setup/companies/verify", json={"companies": companies}).json()
    assert verified["companies"][0]["platform"] == "greenhouse"
    preview = client.get("/api/setup/preview").json()["files"]
    assert "Sam Lee" in preview["config.yaml"]
    assert client.post("/api/setup/commit").json()["configured"] is True
    assert client.get("/api/setup/status").json()["configured"] is True
    assert (profile_dir / "config.yaml").exists()


def test_pipeline_routes_return_409_before_setup(client: TestClient) -> None:
    res = client.get("/api/state", headers={"Origin": "http://localhost:8766"})
    assert res.status_code == 409
    assert "setup" in res.json()["detail"].lower()
    assert res.headers.get("access-control-allow-origin") == "http://localhost:8766"


def test_setup_auth_claude_code_saves_token(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import job_pipeline.ui.setup_routes as sr

    monkeypatch.setattr(sr, "self_check", lambda model: {"ok": True, "detail": "ok"})
    bad = client.post("/api/setup/auth", json={"mode": "claude_code", "oauth_token": "nope"})
    assert bad.status_code == 400
    ok = client.post(
        "/api/setup/auth", json={"mode": "claude_code", "oauth_token": "sk-ant-oat01-xyz"}
    )
    assert ok.status_code == 200
    env = (tmp_path / ".env").read_text()
    assert "JOB_PIPELINE_AUTH_MODE=claude_code" in env
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-xyz" in env


# --- install step ------------------------------------------------------------


def test_system_checks_shape() -> None:
    from job_pipeline.onboarding.system import system_checks

    checks = {c["id"]: c for c in system_checks()}
    assert set(checks) == {"node", "renderer", "claude_cli", "chrome", "chrome_extension"}
    assert checks["claude_cli"]["blocks"] is False  # API-key users can skip it
    assert checks["renderer"]["group"] == "required"
    assert checks["chrome_extension"]["group"] == "chrome"
    assert checks["chrome_extension"]["optional"] is True
    assert all(c["why"] for c in checks.values())


def test_install_unknown_component() -> None:
    from job_pipeline.onboarding.system import install

    assert install("nope")["ok"] is False


def test_install_claude_cli_uses_official_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    import job_pipeline.onboarding.system as sysmod

    seen: list[Any] = []
    monkeypatch.setattr(sysmod, "_run", lambda cmd, **kw: seen.append(cmd) or {"ok": True,
                                                                              "log": ""})
    monkeypatch.setattr(sysmod.platform, "system", lambda: "Darwin")
    assert sysmod.install("claude_cli")["ok"] is True
    assert seen == [sysmod.CLAUDE_INSTALL_UNIX]


def test_resolve_cli_prefers_real_install_over_execpath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import job_pipeline.llm.cli_client as cc

    bundled = tmp_path / "bundled-claude"
    bundled.write_text("")
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", str(bundled))
    monkeypatch.setattr(cc.shutil, "which", lambda _n: "/usr/local/bin/claude")
    assert cc.resolve_claude_cli() == "/usr/local/bin/claude"
    monkeypatch.setattr(cc.shutil, "which", lambda _n: None)
    monkeypatch.setattr(cc.Path, "home", lambda: tmp_path / "home")
    assert cc.resolve_claude_cli() == str(bundled)
