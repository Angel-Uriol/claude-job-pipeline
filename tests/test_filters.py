"""Tests for the scrape-time filters: location (allowlist-or-remote), age, title."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from job_pipeline.config import load_config
from job_pipeline.scrapers.filters import (
    apply_filters,
    is_disqualified_hybrid,
    is_remote,
    is_too_old,
    matches_location,
    passes_location_filter,
    title_is_excluded,
)
from tests.conftest import EXAMPLE_CONFIG

# Example profile (Bay Area or remote) — realistic location/remote/hybrid tokens.
CFG = load_config(EXAMPLE_CONFIG)


# --- location allowlist ----------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "San Francisco, CA",
        "San Francisco, California",
        "Cupertino, CA",
        "Mountain View, CA",
        "Palo Alto, CA",
        "Bay Area",
        "Santa Clara, CA, USA",
    ],
)
def test_matches_location_accepts_allowed_places(location: str) -> None:
    assert matches_location(location, CFG.filters.location_tokens) is True


@pytest.mark.parametrize(
    "location",
    [
        "New York, NY",
        "Austin, TX",
        "Seattle, WA",
        "London, United Kingdom",
        "Remote",        # Remote alone isn't an allowed place
        "",
    ],
)
def test_matches_location_rejects_other_places(location: str) -> None:
    assert matches_location(location, CFG.filters.location_tokens) is False


# --- Remote allowlist --------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "Remote",
        "Remote - United States",
        "Remote, USA",
        "US-Remote",
        "Fully Remote",
        "Anywhere",
    ],
)
def test_is_remote_matches_remote_variants(location: str) -> None:
    assert is_remote(location, CFG.filters.remote_tokens) is True


@pytest.mark.parametrize(
    "location",
    [
        "San Francisco, CA",
        "New York, NY",
        "Onsite — Austin, TX",
        "",
    ],
)
def test_is_remote_rejects_non_remote(location: str) -> None:
    assert is_remote(location, CFG.filters.remote_tokens) is False


# --- Hybrid disqualifier -----------------------------------------------


def test_hybrid_disqualifier_catches_seattle_hybrid() -> None:
    """Hybrid - Seattle / Remote should not pass the location filter."""
    assert (
        is_disqualified_hybrid("Hybrid - Seattle, WA / Remote", CFG.filters.hybrid_disqualifiers)
        is True
    )


def test_hybrid_disqualifier_misses_pure_remote() -> None:
    assert is_disqualified_hybrid("Remote", CFG.filters.hybrid_disqualifiers) is False


# --- Compound location filter ------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "San Francisco, CA",
        "Cupertino, CA",
        "Remote",
        "Remote, USA",
        "Hybrid - San Francisco, CA",   # hybrid in an allowed city still passes
    ],
)
def test_passes_location_filter_accepts(location: str) -> None:
    assert passes_location_filter(location, CFG) is True


@pytest.mark.parametrize(
    "location",
    [
        "New York, NY",
        "Seattle, WA",
        "Austin, TX",
        "London, United Kingdom",
        "Hybrid - Seattle, WA / Remote",  # hybrid+non-CA disqualifies
        "",                                # missing location → drop (CA-or-remote enforced)
    ],
)
def test_passes_location_filter_rejects(location: str) -> None:
    assert passes_location_filter(location, CFG) is False


# --- title exclusion ---------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Director of Engineering",
        "Principal Engineer",
        "Staff Software Engineer",
        "VP of Engineering",
        "Head of Data",
    ],
)
def test_title_excluded_for_senior_roles(title: str) -> None:
    assert title_is_excluded(title, CFG.profile.excluded_titles) is True


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer",
        "Senior Software Engineer",
        "ML Data Engineer",
        # Word-boundary: "Staffing" should NOT match "staff"
        "Staffing Coordinator",
    ],
)
def test_title_not_excluded_for_target_roles(title: str) -> None:
    assert title_is_excluded(title, CFG.profile.excluded_titles) is False


# --- age filter --------------------------------------------------------


def test_is_too_old_with_old_date() -> None:
    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    assert is_too_old(old, max_age_days=60) is True


def test_is_too_old_with_recent_date() -> None:
    recent = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    assert is_too_old(recent, max_age_days=60) is False


def test_is_too_old_with_no_date_keeps_job() -> None:
    assert is_too_old(None, max_age_days=60) is False
    assert is_too_old("", max_age_days=60) is False


def test_is_too_old_with_unparseable_date_keeps_job() -> None:
    assert is_too_old("yesterday", max_age_days=60) is False


def test_is_too_old_with_date_only_format() -> None:
    old = (datetime.now(UTC) - timedelta(days=200)).date().isoformat()
    assert is_too_old(old, max_age_days=60) is True


# --- apply_filters compound -------------------------------------------


def _make_job_dict(**overrides: Any) -> dict[str, Any]:
    defaults = {
        "company": "test",
        "job_id": "1",
        "title": "Software Engineer",
        "team": "Eng",
        "source": "greenhouse",
        "url": "u",
        "location": "San Francisco, CA",
        "posted_date": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
        "discovered_at": datetime.now(UTC).isoformat(),
        "description": "build stuff",
    }
    defaults.update(overrides)
    return defaults


def test_apply_filters_passes_good_ca_job() -> None:
    from job_pipeline.models import Job

    j = Job(**_make_job_dict())
    result = apply_filters(j, CFG)
    assert result.passed is True
    assert result.reason == ""


def test_apply_filters_passes_us_remote() -> None:
    from job_pipeline.models import Job

    j = Job(**_make_job_dict(location="Remote, USA"))
    assert apply_filters(j, CFG).passed is True


def test_apply_filters_blocks_excluded_title() -> None:
    from job_pipeline.models import Job

    j = Job(**_make_job_dict(title="Principal Engineer"))
    result = apply_filters(j, CFG)
    assert result.passed is False
    assert result.reason == "excluded_title"


def test_apply_filters_blocks_location_mismatch() -> None:
    """Seattle isn't an allowed place and isn't remote — dropped at scrape."""
    from job_pipeline.models import Job

    j = Job(**_make_job_dict(location="Seattle, WA"))
    result = apply_filters(j, CFG)
    assert result.passed is False
    assert result.reason == "location_mismatch"


def test_apply_filters_blocks_hybrid_elsewhere() -> None:
    """Hybrid-Seattle is partial remote in a non-allowed city — drop."""
    from job_pipeline.models import Job

    j = Job(**_make_job_dict(location="Hybrid - Seattle, WA / Remote"))
    assert apply_filters(j, CFG).reason == "location_mismatch"


def test_apply_filters_blocks_old_posting() -> None:
    from job_pipeline.models import Job

    old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
    j = Job(**_make_job_dict(posted_date=old))
    result = apply_filters(j, CFG)
    assert result.passed is False
    assert result.reason == "too_old"


def test_apply_filters_title_takes_priority_over_location() -> None:
    """A Director in London is reported as excluded_title (more specific)."""
    from job_pipeline.models import Job

    j = Job(**_make_job_dict(title="Director, ML", location="London"))
    result = apply_filters(j, CFG)
    assert result.passed is False
    assert result.reason == "excluded_title"


def test_remote_rejected_when_profile_disallows_remote() -> None:
    cfg = CFG.model_copy(deep=True)
    cfg.filters.allow_remote = False
    assert passes_location_filter("Remote", cfg) is False
    assert passes_location_filter("San Francisco, CA", cfg) is True


def test_location_filter_can_be_turned_off() -> None:
    from job_pipeline.models import Job

    cfg = CFG.model_copy(deep=True)
    cfg.filters.location_filter_enabled = False
    j = Job(**_make_job_dict(location="Berlin, Germany"))
    assert apply_filters(j, cfg).passed is True
