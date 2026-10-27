"""Tests for tracker_db — the built-in SQLite application tracker.

We seed a TEMP SQLite DB with the relevant columns of the live schema (incl.
the `hidden` column added by migration) and point a Config at it."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from job_pipeline import tracker_db
from job_pipeline.config import load_config
from tests.conftest import EXAMPLE_CONFIG

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path: Path, seed_tracker_db: Callable[[Path], Path]) -> Any:
    c = load_config(EXAMPLE_CONFIG)
    c.tracker.enabled = True
    c.tracker.db_path = str(seed_tracker_db(tmp_path / "proj"))
    c.paths.resumes_dir = str(tmp_path / "proj" / "resumes" / "output")
    return c


# --- list / serialize ----------------------------------------------------


def test_list_excludes_hidden_by_default(cfg: Any) -> None:
    rows = tracker_db.list_jobs(cfg)
    ids = {r["id"] for r in rows}
    assert "jc" not in ids  # hidden
    assert {"ja", "jb"} <= ids


def test_list_hidden_only(cfg: Any) -> None:
    rows = tracker_db.list_jobs(cfg, hidden="only")
    assert [r["id"] for r in rows] == ["jc"]


def test_list_status_and_verdict_filters(cfg: Any) -> None:
    assert [r["id"] for r in tracker_db.list_jobs(cfg, status="applied")] == ["jb"]
    assert [r["id"] for r in tracker_db.list_jobs(cfg, verdict="APPLY")] == ["ja"]


def test_list_search(cfg: Any) -> None:
    rows = tracker_db.list_jobs(cfg, search="backend")
    assert [r["id"] for r in rows] == ["jb"]


def test_list_company_filter(cfg: Any) -> None:
    # Case-insensitive exact company match.
    assert [r["id"] for r in tracker_db.list_jobs(cfg, company="anthropic")] == ["ja"]
    assert [r["id"] for r in tracker_db.list_jobs(cfg, company="Stripe")] == ["jb"]


def test_stats_by_company_excludes_hidden(cfg: Any) -> None:
    bc = tracker_db.stats(cfg)["by_company"]
    assert bc.get("Anthropic") == 1
    assert bc.get("Stripe") == 1
    assert "Hidden Co" not in bc  # hidden excluded


def test_serialize_shape(cfg: Any) -> None:
    ja = next(r for r in tracker_db.list_jobs(cfg) if r["id"] == "ja")
    assert ja["fit_breakdown"]["stack"] == 9.0
    assert ja["fit_breakdown"]["function"] == 7.0
    assert ja["key_requirements"] == ["Python", "SQL"]
    assert ja["resume_file_path"] and ja["resume_file_path"].endswith("ja.docx")
    # stack_score etc. are popped into fit_breakdown.
    assert "stack_score" not in ja


# --- mutations -----------------------------------------------------------


def test_set_status_sets_applied_at(cfg: Any) -> None:
    assert tracker_db.set_status(cfg, "ja", "applied") is True
    ja = next(r for r in tracker_db.list_jobs(cfg) if r["id"] == "ja")
    assert ja["status"] == "applied"
    assert ja["applied_at"]  # stamped


def test_set_status_invalid_raises(cfg: Any) -> None:
    with pytest.raises(ValueError, match="invalid status"):
        tracker_db.set_status(cfg, "ja", "bogus")


def test_set_status_unknown_job_returns_false(cfg: Any) -> None:
    assert tracker_db.set_status(cfg, "nope", "applied") is False


def test_toggle_star_and_hide(cfg: Any) -> None:
    assert tracker_db.toggle_star(cfg, "ja") == 1
    assert tracker_db.toggle_star(cfg, "ja") == 0
    assert tracker_db.toggle_hide(cfg, "ja") == 1  # now hidden
    assert "ja" not in {r["id"] for r in tracker_db.list_jobs(cfg)}


def test_set_notes_and_delete(cfg: Any) -> None:
    assert tracker_db.set_notes(cfg, "jb", "called recruiter") is True
    jb = next(r for r in tracker_db.list_jobs(cfg) if r["id"] == "jb")
    assert jb["notes"] == "called recruiter"
    assert tracker_db.delete(cfg, "jb") is True
    assert "jb" not in {r["id"] for r in tracker_db.list_jobs(cfg)}


def test_bulk_set_status(cfg: Any) -> None:
    n = tracker_db.bulk_set_status(cfg, ["ja", "jb"], "withdrawn")
    assert n == 2
    statuses = {r["id"]: r["status"] for r in tracker_db.list_jobs(cfg)}
    assert statuses["ja"] == "withdrawn"
    assert statuses["jb"] == "withdrawn"


def test_stats(cfg: Any) -> None:
    s = tracker_db.stats(cfg)
    assert s["total"] == 2  # hidden excluded
    assert s["applied_count"] == 1
    assert s["hidden_count"] == 1


# --- missing DB (graceful) -----------------------------------------------


def test_missing_db_is_graceful(tmp_path: Path) -> None:
    c = load_config(EXAMPLE_CONFIG)
    c.tracker.db_path = str(tmp_path / "nope" / "tracker.db")
    assert tracker_db.db_exists(c) is False
    assert tracker_db.list_jobs(c) == []
    assert tracker_db.stats(c)["total"] == 0
    assert tracker_db.latest_resume_path(c, "ja") is None


# --- schema creation + importing ----------------------------------------


def _fresh_cfg(tmp_path: Path) -> Any:
    c = load_config(EXAMPLE_CONFIG)
    c.tracker.db_path = str(tmp_path / "data" / "tracker.db")
    c.paths.resumes_dir = str(tmp_path / "outputs" / "resumes")
    return c


def test_init_db_creates_schema_and_is_idempotent(tmp_path: Path) -> None:
    c = _fresh_cfg(tmp_path)
    tracker_db.init_db(c)
    tracker_db.init_db(c)
    assert tracker_db.db_exists(c)
    assert tracker_db.stats(c)["total"] == 0


def test_insert_jobs_dedups_case_insensitively_and_caps(tmp_path: Path) -> None:
    c = _fresh_cfg(tmp_path)
    c.tracker.max_per_company = 2
    rows = [
        {"company": "Stripe", "role": "Backend Engineer", "job_id": "1", "verdict": "apply"},
        {"company": "stripe", "role": "backend engineer"},           # dup by role
        {"company": "STRIPE", "role": "Data Engineer", "job_id": "1"},  # dup by job_id
        {"company": "Stripe", "role": "Platform Engineer"},
        {"company": "Stripe", "role": "Infra Engineer"},            # over cap
        {"company": "", "role": "No company"},                      # invalid
    ]
    out = tracker_db.insert_jobs(c, rows)
    assert out["inserted"] == 2
    assert out["skipped"] == {"duplicate": 2, "cap": 1, "invalid": 1}
    jobs = tracker_db.list_jobs(c)
    assert {j["role"] for j in jobs} == {"Backend Engineer", "Platform Engineer"}
    assert all(j["verdict"] in ("APPLY", "CONSIDER") for j in jobs)


def test_insert_jobs_registers_resume(tmp_path: Path) -> None:
    c = _fresh_cfg(tmp_path)
    docx = tmp_path / "elsewhere" / "r.docx"
    docx.parent.mkdir()
    docx.write_bytes(b"fake")
    out = tracker_db.insert_jobs(
        c, [{"company": "Acme", "role": "Engineer", "resume_path": str(docx)}]
    )
    assert out["resumes"] == 1
    jid = tracker_db.list_jobs(c)[0]["id"]
    stored = tracker_db.latest_resume_path(c, jid)
    assert stored is not None and Path(stored).parent == Path(c.paths.resumes_dir)


def test_insert_jobs_dry_run_writes_nothing(tmp_path: Path) -> None:
    c = _fresh_cfg(tmp_path)
    out = tracker_db.insert_jobs(c, [{"company": "Acme", "role": "Eng"}], dry_run=True)
    assert out["inserted"] == 1
    assert tracker_db.stats(c)["total"] == 0
