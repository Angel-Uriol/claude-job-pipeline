"""'Try a demo first': made-up profile, pre-scored jobs, Claude switched off."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_pipeline import demo, paths, profiles, tracker_db
from job_pipeline.config import load_config
from job_pipeline.llm.base import LLMError
from job_pipeline.llm.factory import build_llm
from job_pipeline.store import JobStore


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("JOB_PIPELINE_PROFILE_DIR", raising=False)
    r = tmp_path / "profiles"
    monkeypatch.setenv("JOB_PIPELINE_PROFILES_DIR", str(r))
    monkeypatch.setattr(paths, "LEGACY_PROFILE_DIR", tmp_path / "profile")
    return r


def test_demo_profile_has_jobs_applications_and_is_active(root: Path) -> None:
    summary = demo.create_demo()
    assert summary["demo"] is True
    assert paths.active_slug() == summary["slug"]
    d = paths.profile_dir_for(summary["slug"])
    cfg = load_config(d / "config.yaml")
    assert cfg.resume.tailor_enabled is False
    store = JobStore(cfg.paths.jobs_json)
    store.load()
    jobs = store.jobs
    assert len(jobs) >= 10
    assert all(j.fit_score is not None for j in jobs)
    assert any(j.fit_reasons and j.fit_gaps for j in jobs)
    rows = tracker_db.list_jobs(cfg)
    assert {r["status"] for r in rows} >= {"applied", "phone_screen"}
    assert all(r["follow_up_at"] for r in rows)


def test_recreating_the_demo_replaces_the_old_one(root: Path) -> None:
    demo.create_demo()
    demo.create_demo()
    demos = [p for p in profiles.list_profiles() if p.get("demo")]
    assert len(demos) == 1


def test_claude_is_refused_while_demo_is_active(root: Path) -> None:
    demo.create_demo()
    with pytest.raises(LLMError, match="demo"):
        build_llm()


def test_real_profiles_are_not_demo(root: Path) -> None:
    s = profiles.create("Real Person")
    assert s["demo"] is False
    assert demo.is_demo(paths.profile_dir_for(s["slug"])) is False
