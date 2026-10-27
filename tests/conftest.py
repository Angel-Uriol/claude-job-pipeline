"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# Tests run against the shipped EXAMPLE profile (a fictional candidate), never
# a real user's profile/ folder. Set before any job_pipeline import resolves
# profile paths (e.g. the cached target-company index).
REPO = Path(__file__).resolve().parent.parent
EXAMPLE_PROFILE = REPO / "examples" / "profile"
EXAMPLE_CONFIG = EXAMPLE_PROFILE / "config.yaml"
os.environ["JOB_PIPELINE_PROFILE_DIR"] = str(EXAMPLE_PROFILE)
# Never let a developer's real key or auth mode leak into tests.
os.environ["JOB_PIPELINE_AUTH_MODE"] = "api_key"
os.environ["ANTHROPIC_API_KEY"] = ""

from job_pipeline.models import Job, Source  # noqa: E402
from job_pipeline.store import JobStore  # noqa: E402

# Minimal mirror of the tracker jobs.db schema (incl. the `hidden` column
# added by migration) — enough for tracker_db + tracker-endpoint tests.
_TRACKER_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, batch INTEGER, company TEXT NOT NULL, role TEXT NOT NULL,
    role_tier TEXT, role_category TEXT, job_id TEXT, location TEXT, location_pass TEXT,
    posted TEXT, level TEXT, salary TEXT, h1b TEXT, url TEXT,
    fit_score REAL, stack_score REAL, level_score REAL, function_score REAL,
    bridgeability REAL, desirability REAL, verdict TEXT DEFAULT 'CONSIDER',
    bridge_notes TEXT, key_requirements TEXT, key_nice_to_haves TEXT, full_jd_text TEXT,
    resume_number INTEGER, resume_variant TEXT, starred INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new', notes TEXT, applied_at TEXT,
    added_at TEXT DEFAULT (datetime('now')), hidden INTEGER DEFAULT 0
);
CREATE TABLE resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT REFERENCES jobs(id),
    resume_number INTEGER, variant TEXT, file_path TEXT, generated_at TEXT
);
"""


@pytest.fixture
def seed_tracker_db() -> Callable[[Path], Path]:
    """Returns a fn that creates <project_dir>/data/jobs.db with 3 jobs
    (one hidden, one applied) + 1 resume, for tracker tests."""

    def _seed(project_dir: Path) -> Path:
        db = project_dir / "data" / "jobs.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db)
        try:
            conn.executescript(_TRACKER_SCHEMA)
            jobs = [
                ("ja", "Anthropic", "ML Data Engineer", "APPLY", 9.1, 9.0,
                 "to_apply", 0, "Yes (CA)", None),
                ("jb", "Stripe", "Backend Engineer", "CONSIDER", 7.0, 6.0,
                 "applied", 0, "Yes (Remote)", "2026-05-01"),
                ("jc", "Hidden Co", "Recruiter Role", "SKIP", 3.0, 2.0,
                 "skip", 1, "NO", None),
            ]
            for r in jobs:
                conn.execute(
                    "INSERT INTO jobs (id, company, role, verdict, fit_score, "
                    "stack_score, level_score, function_score, bridgeability, "
                    "desirability, status, hidden, location_pass, applied_at, "
                    "key_requirements, key_nice_to_haves, full_jd_text) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r[0], r[1], r[2], r[3], r[4], r[5], 8.0, 7.0, 6.0, 5.0,
                     r[6], r[7], r[8], r[9], '["Python","SQL"]', "[]",
                     f"JD for {r[2]}"),
                )
            conn.execute(
                "INSERT INTO resumes (job_id, resume_number, variant, "
                "file_path, generated_at) VALUES (?,?,?,?,?)",
                ("ja", 1, "job-pipeline-v2",
                 str(project_dir / "resumes" / "output" / "ja.docx"), "2026-05-02"),
            )
            conn.commit()
        finally:
            conn.close()
        return db

    return _seed


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    """An empty, freshly-loaded JobStore in a temp dir.

    Uses a small excluded list so `test_upsert_excluded_company` has
    something to assert against.
    """
    s = JobStore(tmp_path / "jobs.json", excluded_companies=["apple", "tesla"])
    s.load()
    return s


@pytest.fixture
def make_job() -> Any:
    """Factory that builds a Job with sensible defaults.

    Any kwarg passed in overrides the corresponding default. Returning a
    callable (instead of a single Job) lets a test build multiple variants
    without repeating boilerplate.
    """

    def _make(**overrides: Any) -> Job:
        defaults: dict[str, Any] = {
            "company": "databricks",
            "job_id": "12345",
            "title": "Software Engineer, ML Platform",
            "team": "ML Platform",
            "source": Source.GREENHOUSE,
            "url": "https://example.com/job/12345",
            "location": "San Francisco, CA",
            "discovered_at": datetime.now(UTC).isoformat(),
            "description": "Build ML infrastructure...",
        }
        defaults.update(overrides)
        return Job(**defaults)

    return _make
