"""Changing a finished profile: reopen the setup chat without losing edits
made later in the forms (or by Jobi), and re-save without resetting settings."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from job_pipeline.onboarding.interview import InterviewState, ProfileDraft, reopen
from job_pipeline.onboarding.writer import write_files
from job_pipeline.ui import create_app
from tests.conftest import EXAMPLE_PROFILE


@pytest.fixture
def saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A finished profile (fictional example files) with later edits."""
    for name in ("config.yaml", "resume_content.yaml", "target_companies.yaml"):
        shutil.copy(EXAMPLE_PROFILE / name, tmp_path / name)
    monkeypatch.setenv("JOB_PIPELINE_PROFILE_DIR", str(tmp_path))
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    cfg["profile"]["headline"] = "Edited in the form"   # a later form edit
    cfg["scoring"]["max_jobs_per_run"] = 7              # a later settings change
    cfg["dead_companies"] = ["acme"]
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    # The old chat state from setup still has the original answers.
    InterviewState(draft=ProfileDraft(name="Old", headline="Old headline",
                                      industry="fintech"),
                   phase="done", done=True, resume_mode="upload").save()
    return tmp_path


def test_reopen_rebuilds_draft_from_saved_files(saved: Path) -> None:
    state = reopen(InterviewState.load())
    assert state.phase == "round3" and state.done is False
    assert state.draft.headline == "Edited in the form"   # form edit kept
    assert state.draft.industry == "fintech"             # chat-only field kept
    assert state.messages[-1].text.startswith("Welcome back")
    names = {c["name"]: c for c in state.companies}
    assert names["stripe"]["platform"] == "greenhouse" and names["stripe"]["tier"] == 1
    assert names["uber"]["careers_url"]
    assert state.resume_content and state.resume_content["experiences"]
    assert InterviewState.load().phase == "round3"  # saved


def test_resave_after_reopen_keeps_edits_ids_and_settings(saved: Path) -> None:
    before_ids = [b["id"] for e in yaml.safe_load((saved / "resume_content.yaml").read_text())
                  ["experiences"].values() for b in e["bullets"]]
    state = reopen(InterviewState.load())
    state.phase, state.done = "done", True
    write_files(state)
    cfg = yaml.safe_load((saved / "config.yaml").read_text())
    assert cfg["profile"]["headline"] == "Edited in the form"
    assert cfg["scoring"]["max_jobs_per_run"] == 7      # not reset to the example default
    assert cfg["dead_companies"] == ["acme"]
    assert {"name": "stripe", "slug": "stripe"} in cfg["scrapers"]["greenhouse"]
    after_ids = [b["id"] for e in yaml.safe_load((saved / "resume_content.yaml").read_text())
                 ["experiences"].values() for b in e["bullets"]]
    assert after_ids == before_ids
    assert (saved / "config.yaml.bak").exists()


def test_reopen_route(saved: Path) -> None:
    c = TestClient(create_app(config_path=str(saved / "config.yaml")))
    r = c.post("/api/setup/interview/reopen").json()
    assert r["done"] is False and r["phase"] == "round3"
    assert c.get("/api/setup/status").json()["configured"] is True
