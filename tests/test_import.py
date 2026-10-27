"""Tests for the Chrome Claude JSONL importer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_pipeline.importers import import_chrome_claude_jsonl
from job_pipeline.models import Source
from job_pipeline.store import JobStore


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    """Helper: write one JSON object per line."""
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row))
            f.write("\n")


def _good_row(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "company": "snowflake",
        "job_id": "12345",
        "title": "Software Engineer",
        "team": "ML Platform",
        "url": "https://careers.example.com/job/12345",
        "location": "San Mateo, CA",
        "posted_date": "2026-04-15",
        "description": "Build ML infrastructure with Iceberg and Spark.",
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    s = JobStore(tmp_path / "jobs.json", excluded_companies=["apple"])
    s.load()
    return s


# --- happy path -----------------------------------------------------------


def test_import_parses_single_row(tmp_path: Path, store: JobStore) -> None:
    p = tmp_path / "in.jsonl"
    _write_jsonl(p, [_good_row()])

    stats = import_chrome_claude_jsonl(p, store)
    assert stats.inserted == 1
    assert stats.skipped_malformed == 0
    assert len(store.jobs) == 1

    job = store.jobs[0]
    assert job.company == "snowflake"
    assert job.source == Source.CHROME_CLAUDE
    assert job.url == "https://careers.example.com/job/12345"


def test_import_sets_source_chrome_claude(tmp_path: Path, store: JobStore) -> None:
    p = tmp_path / "in.jsonl"
    _write_jsonl(p, [_good_row()])
    import_chrome_claude_jsonl(p, store)
    assert store.jobs[0].source == Source.CHROME_CLAUDE


def test_import_dedups_against_existing_store(tmp_path: Path, store: JobStore) -> None:
    """If a job with the same (company, job_id) already exists, it's an update."""
    p = tmp_path / "in.jsonl"
    _write_jsonl(p, [_good_row(title="Original")])
    import_chrome_claude_jsonl(p, store)

    _write_jsonl(p, [_good_row(title="Updated")])
    stats = import_chrome_claude_jsonl(p, store)

    assert stats.updated == 1
    assert stats.inserted == 0
    assert len(store.jobs) == 1
    assert store.jobs[0].title == "Updated"


def test_import_multiple_rows(tmp_path: Path, store: JobStore) -> None:
    p = tmp_path / "in.jsonl"
    _write_jsonl(p, [
        _good_row(job_id="a", title="Role A"),
        _good_row(job_id="b", title="Role B"),
        _good_row(job_id="c", title="Role C"),
    ])
    stats = import_chrome_claude_jsonl(p, store)
    assert stats.inserted == 3
    assert len(store.jobs) == 3


# --- excluded company ---------------------------------------------------


def test_import_excluded_company_skipped(tmp_path: Path, store: JobStore) -> None:
    p = tmp_path / "in.jsonl"
    _write_jsonl(p, [_good_row(company="apple")])
    stats = import_chrome_claude_jsonl(p, store)
    assert stats.excluded == 1
    assert stats.inserted == 0
    assert len(store.jobs) == 0


# --- malformed input ----------------------------------------------------


def test_import_malformed_json_logged_and_skipped(tmp_path: Path, store: JobStore) -> None:
    """One bad line shouldn't take down the import."""
    p = tmp_path / "in.jsonl"
    p.write_text(
        json.dumps(_good_row(job_id="good"))
        + "\nthis is not valid json\n"
        + json.dumps(_good_row(job_id="alsoogood")) + "\n",
        encoding="utf-8",
    )
    stats = import_chrome_claude_jsonl(p, store)
    assert stats.inserted == 2
    assert stats.skipped_malformed == 1
    assert any("invalid JSON" in e for e in stats.errors)


def test_import_missing_required_field_skipped(tmp_path: Path, store: JobStore) -> None:
    p = tmp_path / "in.jsonl"
    bad = _good_row()
    del bad["title"]
    _write_jsonl(p, [bad])
    stats = import_chrome_claude_jsonl(p, store)
    assert stats.skipped_malformed == 1
    assert stats.inserted == 0
    assert any("title" in e for e in stats.errors)


def test_import_empty_string_required_field_treated_as_missing(
    tmp_path: Path, store: JobStore
) -> None:
    """Empty required field is as bad as a missing one — both skip."""
    p = tmp_path / "in.jsonl"
    _write_jsonl(p, [_good_row(url="")])
    stats = import_chrome_claude_jsonl(p, store)
    assert stats.skipped_malformed == 1


def test_import_top_level_array_per_line_rejected(
    tmp_path: Path, store: JobStore
) -> None:
    """A JSON array on a line (instead of an object) is malformed for us."""
    p = tmp_path / "in.jsonl"
    p.write_text('["just", "an", "array"]\n', encoding="utf-8")
    stats = import_chrome_claude_jsonl(p, store)
    assert stats.skipped_malformed == 1


# --- whitespace and comments ---------------------------------------------


def test_import_skips_blank_and_comment_lines(tmp_path: Path, store: JobStore) -> None:
    p = tmp_path / "in.jsonl"
    p.write_text(
        "# this is a comment\n"
        "\n"
        + json.dumps(_good_row()) + "\n"
        + "\n"
        + "# another comment\n",
        encoding="utf-8",
    )
    stats = import_chrome_claude_jsonl(p, store)
    assert stats.inserted == 1
    assert stats.skipped_malformed == 0


# --- optional fields ----------------------------------------------------


def test_import_handles_missing_optional_fields(tmp_path: Path, store: JobStore) -> None:
    """team / location / posted_date / job_id are all optional."""
    p = tmp_path / "in.jsonl"
    minimal = {
        "company": "openai",
        "title": "Software Engineer",
        "url": "https://example.com/job",
        "description": "Build cool stuff.",
    }
    _write_jsonl(p, [minimal])
    stats = import_chrome_claude_jsonl(p, store)
    assert stats.inserted == 1
    job = store.jobs[0]
    assert job.team is None
    assert job.location == ""
    assert job.posted_date is None
    assert job.job_id is None
