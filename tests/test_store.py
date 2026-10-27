"""Tests for the JobStore — the only thing with non-trivial logic in Phase 1.

We exercise:
- upsert dedup (both job_id-based and title/team fallback)
- merge semantics (re-scrape preserves scoring)
- exclusion list
- atomic write (no temp file leftovers, real file written)
- load behavior on missing / corrupt files
- full save → load round-trip
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from job_pipeline.models import PreFilterVerdict, Status, Verdict
from job_pipeline.store import JobStore

# --- upsert / dedup -------------------------------------------------------


def test_upsert_new_job(store: JobStore, make_job: Any) -> None:
    """A brand-new job is inserted."""
    result = store.upsert(make_job())
    assert result == "inserted"
    assert len(store.jobs) == 1


def test_upsert_dedups_by_job_id(store: JobStore, make_job: Any) -> None:
    """Same (company, job_id) → merge, not duplicate."""
    store.upsert(make_job(title="Original Title"))
    result = store.upsert(make_job(title="Updated Title"))
    assert result == "updated"
    assert len(store.jobs) == 1
    assert store.jobs[0].title == "Updated Title"


def test_upsert_dedups_by_title_team_when_no_job_id(
    store: JobStore, make_job: Any
) -> None:
    """When job_id is absent, dedup falls back to (company, title, team)."""
    j1 = make_job(job_id=None, title="ML Engineer", team="Pipelines")
    j2 = make_job(
        job_id=None,
        title="ML Engineer",
        team="Pipelines",
        url="https://example.com/new-url",
    )
    store.upsert(j1)
    result = store.upsert(j2)
    assert result == "updated"
    assert len(store.jobs) == 1
    assert store.jobs[0].url == "https://example.com/new-url"


def test_upsert_treats_different_teams_as_separate(
    store: JobStore, make_job: Any
) -> None:
    """Same title, different team, no job_id → two separate jobs."""
    store.upsert(make_job(job_id=None, title="ML Engineer", team="Search"))
    store.upsert(make_job(job_id=None, title="ML Engineer", team="Ads"))
    assert len(store.jobs) == 2


def test_upsert_company_casing_doesnt_break_dedup(
    store: JobStore, make_job: Any
) -> None:
    """`Databricks` and `databricks` should collide."""
    store.upsert(make_job(company="Databricks"))
    result = store.upsert(make_job(company="databricks"))
    assert result == "updated"
    assert len(store.jobs) == 1


# --- merge semantics ------------------------------------------------------


def test_upsert_preserves_scoring_on_rescrape(
    store: JobStore, make_job: Any
) -> None:
    """A second scrape must not stomp scoring or lifecycle fields."""
    store.upsert(make_job())
    # Simulate the pipeline having scored and the human having reacted.
    j = store.jobs[0]
    j.fit_score = 8.5
    j.fit_verdict = Verdict.APPLY
    j.pre_filter_verdict = PreFilterVerdict.PASS
    j.status = Status.PENDING
    j.notes = "applied via referral"

    # Re-scrape: title and description change, scoring should not.
    store.upsert(make_job(title="Better title", description="Updated JD"))

    j2 = store.jobs[0]
    assert j2.title == "Better title"
    assert j2.description == "Updated JD"
    assert j2.fit_score == 8.5
    assert j2.fit_verdict == Verdict.APPLY
    assert j2.pre_filter_verdict == PreFilterVerdict.PASS
    assert j2.status == Status.PENDING
    assert j2.notes == "applied via referral"


# --- exclusion ------------------------------------------------------------


def test_upsert_rejects_excluded_company(store: JobStore, make_job: Any) -> None:
    """Excluded companies never make it into the store."""
    result = store.upsert(make_job(company="apple"))
    assert result == "excluded"
    assert len(store.jobs) == 0


def test_exclusion_is_case_insensitive(store: JobStore, make_job: Any) -> None:
    result = store.upsert(make_job(company="Apple"))
    assert result == "excluded"


# --- atomic save ----------------------------------------------------------


def test_save_writes_valid_json(store: JobStore, make_job: Any) -> None:
    store.upsert(make_job())
    store.save()
    assert store.path.exists()
    with store.path.open() as f:
        data = json.load(f)
    assert data["_meta"]["total_jobs"] == 1
    assert data["_meta"]["schema_version"] == 2
    assert len(data["jobs"]) == 1


def test_save_leaves_no_temp_files(
    store: JobStore, make_job: Any, tmp_path: Path
) -> None:
    store.upsert(make_job())
    store.save()
    assert list(tmp_path.glob(".jobs-*.tmp")) == []
    assert list(tmp_path.glob(".jobs-*.json.tmp")) == []


def test_save_creates_parent_dir(tmp_path: Path, make_job: Any) -> None:
    """`save` must mkdir-p the destination directory."""
    nested = tmp_path / "deeply" / "nested" / "jobs.json"
    s = JobStore(nested)
    s.load()
    s.upsert(make_job())
    s.save()
    assert nested.exists()


# --- load behavior --------------------------------------------------------


def test_load_missing_file_starts_empty(tmp_path: Path) -> None:
    s = JobStore(tmp_path / "does-not-exist.json")
    s.load()
    assert s.jobs == []


def test_load_corrupt_file_raises(tmp_path: Path) -> None:
    """Corrupt JSON is loud: better to crash than silently overwrite real data."""
    p = tmp_path / "jobs.json"
    p.write_text("{this is not valid json", encoding="utf-8")
    s = JobStore(p)
    with pytest.raises(json.JSONDecodeError):
        s.load()


def test_load_future_schema_version_raises(tmp_path: Path) -> None:
    """If the file's schema_version is newer than ours, we refuse to load."""
    p = tmp_path / "jobs.json"
    p.write_text(
        json.dumps(
            {
                "_meta": {"schema_version": 999, "last_updated": "x", "total_jobs": 0},
                "excluded_companies": [],
                "jobs": [],
            }
        ),
        encoding="utf-8",
    )
    s = JobStore(p)
    with pytest.raises(RuntimeError, match="schema version"):
        s.load()


def test_roundtrip_save_then_load(tmp_path: Path, make_job: Any) -> None:
    """Save in one store, load in another, recover the same jobs."""
    p = tmp_path / "jobs.json"
    s1 = JobStore(p, excluded_companies=["apple"])
    s1.load()
    s1.upsert(make_job())
    s1.upsert(make_job(job_id="67890", title="Other Role"))
    s1.save()

    s2 = JobStore(p)
    s2.load()
    assert len(s2.jobs) == 2
    titles = {j.title for j in s2.jobs}
    assert titles == {"Software Engineer, ML Platform", "Other Role"}


# --- guards ---------------------------------------------------------------


def test_access_before_load_raises(tmp_path: Path) -> None:
    """Touching the store without load() should fail fast — never silently
    operate on empty state."""
    s = JobStore(tmp_path / "jobs.json")
    with pytest.raises(RuntimeError, match="load"):
        _ = s.jobs


def test_find_by_job_id_returns_match(store: JobStore, make_job: Any) -> None:
    store.upsert(make_job(job_id="abc123", company="Stripe"))
    found = store.find_by_job_id("stripe", "abc123")
    assert found is not None
    assert found.job_id == "abc123"


def test_find_by_job_id_returns_none_when_absent(store: JobStore) -> None:
    assert store.find_by_job_id("stripe", "nonexistent") is None


# --- remove_jobs ----------------------------------------------------------


def test_remove_jobs_drops_matching(store: JobStore, make_job: Any) -> None:
    store.upsert(make_job(job_id="keep-1", company="Stripe"))
    store.upsert(make_job(job_id="drop-1", company="Lyft"))
    store.upsert(make_job(job_id="keep-2", company="Figma"))

    removed = store.remove_jobs(lambda j: j.company.lower() == "lyft")
    assert removed == 1
    companies = {j.company.lower() for j in store.jobs}
    assert "lyft" not in companies
    assert {"stripe", "figma"} <= companies


def test_remove_jobs_returns_zero_when_no_match(store: JobStore, make_job: Any) -> None:
    store.upsert(make_job(job_id="a"))
    assert store.remove_jobs(lambda j: False) == 0
    assert len(store.jobs) == 1


def test_upsert_preserves_dismissed_status_on_rescrape(
    store: JobStore, make_job: Any
) -> None:
    """A discarded (dismissed) job must STAY dismissed when re-scraped —
    status isn't a refreshable field, so the soft-discard sticks."""
    store.upsert(make_job(job_id="x", title="Original"))
    store.jobs[0].status = Status.DISMISSED
    # Simulate a later scrape of the same posting.
    store.upsert(make_job(job_id="x", title="Updated Title"))
    assert len(store.jobs) == 1
    assert store.jobs[0].status == Status.DISMISSED  # preserved
    assert store.jobs[0].title == "Updated Title"  # refreshable field updated


# --- load-time exclusion purge --------------------------------------------


def test_load_drops_excluded_company(tmp_path: Path, make_job: Any) -> None:
    """A job stored before its company was excluded is purged on load."""
    path = tmp_path / "jobs.json"
    # First, write a store (no exclusions) containing an OpenAI job + a keeper.
    writer = JobStore(path)
    writer.load()
    writer.upsert(make_job(job_id="openai-1", company="OpenAI"))
    writer.upsert(make_job(job_id="keep-1", company="Stripe"))
    writer.save()
    assert len(writer.jobs) == 2

    # Now load with OpenAI excluded → the OpenAI job is dropped.
    reader = JobStore(path, excluded_companies=["openai"])
    reader.load()
    companies = {j.company.lower() for j in reader.jobs}
    assert "openai" not in companies
    assert "stripe" in companies
    assert len(reader.jobs) == 1
