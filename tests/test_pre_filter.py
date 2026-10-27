"""Tests for the pre-filter stage.

We don't hit OpenAI. Tests inject a `FakePreFilterClient` that returns
canned `PreFilterExtraction` objects so the logic around the LLM (prompt
formatting, hard-skip rules, state mutations) can be exercised in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_pipeline.models import Job, Source
from job_pipeline.scoring.pre_filter import (
    DESCRIPTION_TRUNCATE,
    PreFilterer,
    PreFilterExtraction,
    apply_hard_skip_rules,
)

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


# --- fake client ----------------------------------------------------------


class FakePreFilterClient:
    """Returns a canned extraction; records every prompt seen."""

    def __init__(self, response: PreFilterExtraction) -> None:
        self.response = response
        self.prompts_seen: list[str] = []

    def call(self, prompt: str) -> PreFilterExtraction:
        self.prompts_seen.append(prompt)
        return self.response


def _make_job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "company": "databricks",
        "job_id": "1",
        "title": "Software Engineer, ML Platform",
        "team": "ML",
        "source": Source.GREENHOUSE,
        "url": "u",
        "location": "San Francisco, CA",
        "discovered_at": datetime.now(UTC).isoformat(),
        "description": "Build ML infrastructure with PySpark and Airflow.",
    }
    defaults.update(overrides)
    return Job(**defaults)


def _basic_pass() -> PreFilterExtraction:
    return PreFilterExtraction(
        level="L4",
        years_required=4,
        degree_required="BS",
        visa_stance="sponsors",
        tech_stack=["python", "pyspark", "airflow"],
        verdict="pass",
        reason="mid-level ML data eng role with stack overlap",
    )


# --- PreFilterer ----------------------------------------------------------


def test_prefilterer_formats_prompt_with_job_fields() -> None:
    template = "Title: {title}\nLocation: {location}\n\n{description}"
    client = FakePreFilterClient(_basic_pass())
    pf = PreFilterer(client, template)

    pf.score(_make_job(title="ML Eng", location="NYC", description="JD text"))

    prompt = client.prompts_seen[0]
    assert "Title: ML Eng" in prompt
    assert "Location: NYC" in prompt
    assert "JD text" in prompt


def test_prefilterer_truncates_long_description() -> None:
    template = "{description}"
    client = FakePreFilterClient(_basic_pass())
    pf = PreFilterer(client, template)

    long_desc = "x" * (DESCRIPTION_TRUNCATE * 2)
    pf.score(_make_job(description=long_desc))

    # The prompt sent to the LLM should be capped at DESCRIPTION_TRUNCATE chars.
    assert len(client.prompts_seen[0]) == DESCRIPTION_TRUNCATE


def test_prefilterer_loads_from_file() -> None:
    """The shipped prompt file should load and contain expected placeholders."""
    client = FakePreFilterClient(_basic_pass())
    pf = PreFilterer.from_file(client, PROMPTS / "pre_filter.txt")
    pf.score(_make_job())

    prompt = client.prompts_seen[0]
    assert "Software Engineer, ML Platform" in prompt
    assert "San Francisco, CA" in prompt


def test_prefilter_extraction_schema_round_trips() -> None:
    """The pydantic model must serialize to JSON and deserialize unchanged."""
    e = _basic_pass()
    data = e.model_dump()
    e2 = PreFilterExtraction(**data)
    assert e2 == e


# --- hard skip rules ------------------------------------------------------


def test_hard_skip_8_plus_years_skips() -> None:
    job = _make_job(title="Software Engineer")
    extraction = _basic_pass()
    extraction.years_required = 8
    skip, reason = apply_hard_skip_rules(job, extraction)
    assert skip is True
    assert "8" in reason


def test_hard_skip_under_8_years_passes() -> None:
    job = _make_job(title="Software Engineer")
    extraction = _basic_pass()
    extraction.years_required = 7
    skip, _ = apply_hard_skip_rules(job, extraction)
    assert skip is False


def test_hard_skip_phd_research_scientist() -> None:
    job = _make_job(title="Research Scientist, NLP")
    extraction = _basic_pass()
    extraction.degree_required = "PhD"
    skip, reason = apply_hard_skip_rules(job, extraction)
    assert skip is True
    assert "research" in reason.lower()


def test_hard_skip_phd_applied_scientist() -> None:
    job = _make_job(title="Applied Scientist, Computer Vision")
    extraction = _basic_pass()
    extraction.degree_required = "PhD"
    skip, _ = apply_hard_skip_rules(job, extraction)
    assert skip is True


def test_hard_skip_phd_engineer_does_not_trigger() -> None:
    """A PhD-required ENGINEERING role (not scientist) is NOT auto-skipped."""
    job = _make_job(title="Software Engineer, ML Infrastructure")
    extraction = _basic_pass()
    extraction.degree_required = "PhD"
    skip, _ = apply_hard_skip_rules(job, extraction)
    assert skip is False


def test_hard_skip_us_only_clearance() -> None:
    job = _make_job()
    extraction = _basic_pass()
    extraction.visa_stance = "us_only_clearance"
    skip, reason = apply_hard_skip_rules(job, extraction)
    assert skip is True
    assert "clearance" in reason.lower()


@pytest.mark.parametrize(
    "title",
    [
        "Director of Engineering",
        "Principal Software Engineer",
        "Staff ML Engineer",
        "Sr Staff Engineer",
        "VP of Data",
        "Head of Platform",
    ],
)
def test_hard_skip_senior_titles(title: str) -> None:
    job = _make_job(title=title)
    skip, _ = apply_hard_skip_rules(job, _basic_pass())
    assert skip is True


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer, ML Platform",
        "Senior Data Engineer",
        "ML Data Engineer II",
    ],
)
def test_hard_skip_passes_normal_titles(title: str) -> None:
    job = _make_job(title=title)
    skip, _ = apply_hard_skip_rules(job, _basic_pass())
    assert skip is False


def test_hard_skip_independent_of_llm_verdict() -> None:
    """Even if the LLM said 'pass', a Director title still gets skipped."""
    job = _make_job(title="Director of Engineering")
    extraction = _basic_pass()  # verdict=pass
    skip, _ = apply_hard_skip_rules(job, extraction)
    assert skip is True
