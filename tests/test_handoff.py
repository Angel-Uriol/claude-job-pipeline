"""Tests for the tracker handoff (export mapping + run_handoff into SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_pipeline.config import load_config
from job_pipeline.handoff import (
    _location_pass,
    _role_tier,
    build_tracker_export,
    run_handoff,
)
from job_pipeline.models import Job, ScoreResult, Source, Status, Verdict
from job_pipeline.store import JobStore
from tests.conftest import EXAMPLE_CONFIG

REPO = Path(__file__).resolve().parent.parent


def _job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "company": "anthropic",
        "job_id": "1",
        "title": "ML Data Engineer",
        "team": "Data",
        "source": Source.GREENHOUSE,
        "url": "https://example.com/job",
        "location": "San Francisco, CA",
        "discovered_at": datetime.now(UTC).isoformat(),
        "description": "JD text here",
        "fit_score": 8.5,
        "fit_verdict": Verdict.APPLY,
        "fit_subscores": ScoreResult(
            stack=9.0, level=10.0, function=9.0, bridgeability=8.0, desirability=10.0
        ),
        "honest_bridge_notes": "bridge",
        "status": Status.PENDING,
    }
    defaults.update(overrides)
    return Job(**defaults)


# --- build_tracker_export -------------------------------------------------


def test_export_includes_apply_consider_excludes_skip() -> None:
    jobs = [
        _job(job_id="a", fit_verdict=Verdict.APPLY),
        _job(job_id="b", fit_verdict=Verdict.CONSIDER),
        _job(job_id="c", fit_verdict=Verdict.SKIP),
    ]
    out = build_tracker_export(jobs, verdicts=["apply", "consider"])
    assert len(out) == 2
    assert {o["verdict"] for o in out} == {"APPLY", "CONSIDER"}


def test_export_excludes_dismissed_even_if_apply() -> None:
    jobs = [_job(job_id="a", fit_verdict=Verdict.APPLY, status=Status.DISMISSED)]
    assert build_tracker_export(jobs, verdicts=["apply", "consider"]) == []


def test_export_field_mapping() -> None:
    j = _job(
        fit_verdict=Verdict.APPLY,
        status=Status.RESUME_ONLY,
        resume_path="/tmp/r.docx",
    )
    out = build_tracker_export([j], verdicts=["apply"])[0]
    assert out["role"] == "ML Data Engineer"
    assert out["verdict"] == "APPLY"
    assert out["full_jd_text"] == "JD text here"
    assert out["stack_score"] == 9.0
    assert out["bridge_notes"] == "bridge"
    assert out["location_pass"] == "Unknown"  # no cfg → no preferred tokens
    cfg = load_config(EXAMPLE_CONFIG)
    out_cfg = build_tracker_export([j], verdicts=["apply"], cfg=cfg)[0]
    assert out_cfg["location_pass"] == "Yes (Preferred)"
    assert out["role_tier"] == "A"  # function 9.0 → A
    assert out["status"] == "to_apply"  # resume_only → to_apply
    assert out["resume_path"] == "/tmp/r.docx"


def test_helpers() -> None:
    assert _location_pass("Remote - US") == "Yes (Remote)"
    assert _location_pass("New York, NY") == "Unknown"
    assert _location_pass("Sunnyvale, CA", load_config(EXAMPLE_CONFIG)) == "Yes (Preferred)"
    assert _role_tier(9.5) == "A"
    assert _role_tier(7.0) == "B"
    assert _role_tier(4.0) == "C"
    assert _role_tier(None) is None


# --- run_handoff ----------------------------------------------------------


def test_run_handoff_disabled(tmp_path: Path) -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.tracker.enabled = False
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    counts = run_handoff(cfg, store)
    assert counts == {"exported": 0, "imported": 0}


def test_run_handoff_inserts_into_tracker_db(tmp_path: Path) -> None:
    from job_pipeline import tracker_db

    cfg = load_config(EXAMPLE_CONFIG)
    cfg.tracker.enabled = True
    cfg.tracker.db_path = str(tmp_path / "data" / "tracker.db")
    cfg.paths.jobs_json = str(tmp_path / "jobs.json")

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_job(job_id="a", fit_verdict=Verdict.APPLY))
    store.upsert(_job(job_id="b", fit_verdict=Verdict.CONSIDER, title="Backend Eng"))
    store.upsert(_job(job_id="c", fit_verdict=Verdict.SKIP, title="Nope"))

    counts = run_handoff(cfg, store)
    assert counts["exported"] == 2
    assert counts["imported"] == 2
    assert "error" not in counts
    roles = {j["role"] for j in tracker_db.list_jobs(cfg)}
    assert roles == {"ML Data Engineer", "Backend Eng"}

    # Re-running is idempotent thanks to dedup.
    again = run_handoff(cfg, store)
    assert again["imported"] == 0
    assert again["skipped"]["duplicate"] == 2
