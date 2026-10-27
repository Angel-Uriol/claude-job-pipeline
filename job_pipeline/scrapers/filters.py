"""Scrape-time filters — the candidate's location / age / title rules.

Runs AFTER a scraper returns its raw jobs and BEFORE we upsert into the
store. The point is to drop obviously-not-a-fit postings before they hit
the LLM pre-filter, which costs real money.

Three filters live here:

1. **Location (allowlist).** Job's `location` must match one of the
   candidate's `location_tokens` (cities / states / regions) OR, when
   remote is allowed, a "fully remote" variant. Postings like
   "Hybrid - Seattle, WA / Remote" are dropped unless Seattle is itself
   allowed — `hybrid` paired with a non-allowed city is partial-remote.

2. **Age.** If `posted_date` is set, drop postings older than
   `max_age_days`. Postings without a date pass through.

3. **Title exclusion.** Drop titles containing any token from
   `profile.excluded_titles` (case-insensitive whole-word match) —
   e.g. "Director", "Principal", "VP".

All three are pure functions of `(job, config)`. They return a
`FilterResult` so the caller can log *why* a job was rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import Config
from ..models import Job


@dataclass(frozen=True)
class FilterResult:
    """Outcome of running all filters on one job."""

    passed: bool
    reason: str  # empty when passed; otherwise a short tag


# --- location -----------------------------------------------------------


def matches_location(location: str, location_tokens: list[str]) -> bool:
    """True if `location` contains one of the candidate's allowed places.

    Substring match, case-insensitive. Tokens can be state names
    ("california"), abbreviations (", ca"), or cities ("austin").
    """
    if not location:
        return False
    s = location.lower()
    for token in location_tokens:
        if token.lower() in s:
            return True
    return False


def is_remote(location: str, remote_tokens: list[str]) -> bool:
    """True if `location` describes fully-remote work.

    Substring match. "remote" is the most common token, but we also
    catch "us-remote", "remote, usa", etc.
    """
    if not location:
        return False
    s = location.lower()
    for token in remote_tokens:
        if token.lower() in s:
            return True
    return False


def is_disqualified_hybrid(location: str, hybrid_tokens: list[str]) -> bool:
    """True if `location` looks like partial-remote in a non-allowed place.

    Caller should check this only when `matches_location` is False — a
    hybrid posting in an allowed city is still valid.
    """
    if not location:
        return False
    s = location.lower()
    for token in hybrid_tokens:
        if token.lower() in s:
            return True
    return False


def passes_location_filter(location: str, cfg: Config) -> bool:
    """Combined allowed-place-or-remote check.

    Pass if:
      - location matches an allowed place, OR
      - remote is allowed AND location is remote AND not flagged as a
        partial-hybrid posting for a non-allowed city.
    """
    if matches_location(location, cfg.filters.location_tokens):
        return True
    if cfg.filters.allow_remote and is_remote(location, cfg.filters.remote_tokens):
        # Partial-remote postings ("Hybrid - Seattle, WA / Remote") are
        # not full remote. Reject those when the city isn't allowed.
        if is_disqualified_hybrid(location, cfg.filters.hybrid_disqualifiers):
            return False
        return True
    return False


# --- title exclusion ----------------------------------------------------


def title_is_excluded(title: str, excluded_titles: list[str]) -> bool:
    """True if `title` contains any excluded token as a whole word.

    Word-bounded match so "Staff" matches "Staff Engineer" but NOT
    "Staffing Coordinator".
    """
    if not title:
        return False
    s = title.lower()
    for token in excluded_titles:
        if re.search(rf"\b{re.escape(token.lower())}\b", s):
            return True
    return False


# --- age ----------------------------------------------------------------


def is_too_old(posted_date: str | None, max_age_days: int) -> bool:
    """True if `posted_date` is more than `max_age_days` ago.

    Returns False (keeps the job) when posted_date is missing or
    unparseable — we'd rather over-include than throw out something real.
    """
    if not posted_date:
        return False
    try:
        s = posted_date.rstrip("Z").replace("Z", "")
        parsed = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return False
    if parsed.tzinfo is None:
        now = datetime.now()
    else:
        now = datetime.now(parsed.tzinfo)
    return (now - parsed) > timedelta(days=max_age_days)


# --- compound filter ----------------------------------------------------


def apply_filters(job: Job, cfg: Config) -> FilterResult:
    """Run all filters in priority order. First failure wins.

    Priority is chosen for the most useful `reason` string:
      1. Excluded title — most specific.
      2. Location — the candidate's allowed places / remote rule.
      3. Age — least surprising rejection.
    """
    if title_is_excluded(job.title, cfg.profile.excluded_titles):
        return FilterResult(passed=False, reason="excluded_title")

    if cfg.filters.location_filter_enabled and not passes_location_filter(job.location, cfg):
        return FilterResult(passed=False, reason="location_mismatch")

    if is_too_old(job.posted_date, cfg.filters.max_age_days):
        return FilterResult(passed=False, reason="too_old")

    return FilterResult(passed=True, reason="")
