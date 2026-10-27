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
        return next(self.answers)

    def text(self, prompt: str, **kw: Any) -> str:
        return "OK"


@pytest.fixture
def profile_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "profile"
    monkeypatch.setenv("JOB_PIPELINE_PROFILE_DIR", str(d))
    return d


# --- interview engine ------------------------------------------------------


def test_step_merges_updates_and_saves(profile_dir: Path) -> None:
    llm = ScriptedLLM([
        InterviewTurn(updates=ProfileDraft(), next_question="What's your name?", done=False),
        InterviewTurn(updates=ProfileDraft(name="Sam Lee"), next_question="Email?", done=False),
    ])
    state = step(InterviewState(), None, llm=llm, model="m")
    assert state.messages[-1].text == "What's your name?"
    state = step(state, "I'm Sam Lee", llm=llm, model="m")
    assert state.draft.name == "Sam Lee"
    assert [m.role for m in state.messages] == ["assistant", "user", "assistant"]
    assert InterviewState.load().draft.name == "Sam Lee"  # persisted
    assert "I'm Sam Lee" in llm.prompts[1]


def test_model_cannot_finish_early(profile_dir: Path) -> None:
    llm = ScriptedLLM([InterviewTurn(next_question="All done!", done=True)])
    state = step(InterviewState(), "hi", llm=llm, model="m")
    assert state.done is False
    assert "Before we wrap up" in state.messages[-1].text


def test_done_when_required_complete(profile_dir: Path) -> None:
    llm = ScriptedLLM([InterviewTurn(updates=COMPLETE, next_question="Great, next: companies.",
                                     done=True)])
    state = step(InterviewState(), "everything", llm=llm, model="m")
    assert state.missing() == []
    assert state.done is True


def test_prefill_from_resume() -> None:
    state = InterviewState()
    prefill_from_resume(state, {
        "header": {"name": "Sam Lee", "email": "sam@example.com", "location": "Austin, TX"},
        "skills": {"languages": ["Python", "Go"]},
    })
    assert state.draft.name == "Sam Lee"
    assert state.draft.preferred_skills == ["python", "go"]
    assert state.resume_content is not None


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


def test_setup_interview_needs_claude(client: TestClient) -> None:
    assert client.post("/api/setup/interview", json={"message": ""}).status_code == 503


def test_setup_full_flow_with_fake_claude(
    client: TestClient, profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import job_pipeline.ui.setup_routes as sr

    llm = ScriptedLLM([
        InterviewTurn(updates=ProfileDraft(), next_question="What roles?", done=False),
        InterviewTurn(updates=COMPLETE, next_question="Next: companies.", done=True),
    ])
    monkeypatch.setattr(sr, "build_llm", lambda: llm)
    monkeypatch.setattr(sr, "suggest_companies", lambda draft, **kw: [
        {"name": "Stripe", "tier": 1, "why": "x", "selected": True}])
    monkeypatch.setattr(sr, "verify_companies", lambda cs: [
        {**c, "platform": "greenhouse", "slug": "stripe", "jobs": 5} for c in cs])

    assert client.post("/api/setup/interview", json={"message": ""}).json()["done"] is False
    assert client.post("/api/setup/interview", json={"message": "backend"}).json()["done"] is True
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
