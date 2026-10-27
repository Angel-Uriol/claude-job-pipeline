"""JSearch scraper — open-web job discovery via RapidAPI.

JSearch (https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) aggregates
Google for Jobs, which itself pulls LinkedIn, Indeed, Glassdoor, and
ZipRecruiter. Unlike every other scraper in this package, JSearch is
*query-driven*: instead of a company slug, you send a search string like
"ML Data Engineer in California" and get back jobs from MANY companies.
This is the pipeline's first true open-web discovery source.

API (GET, auth via headers — set on the injected client):

    GET https://jsearch.p.rapidapi.com/search
    Headers: X-RapidAPI-Key: <key>, X-RapidAPI-Host: jsearch.p.rapidapi.com
    Query params: query, page, num_pages, date_posted, remote_jobs_only, country

Response shape (only the fields we use):

    {
      "status": "OK",
      "data": [
        {
          "job_id": "opaque-per-day-hash",
          "employer_name": "Acme Corp",
          "job_title": "ML Data Engineer",
          "job_apply_link": "https://...",
          "job_description": "plain-ish text",
          "job_city": "San Francisco",
          "job_state": "CA",
          "job_country": "US",
          "job_is_remote": false,
          "job_posted_at_datetime_utc": "2026-05-15T00:00:00.000Z",
          "job_posted_at_timestamp": 1778900000,
          "job_employment_type": "FULLTIME"
        }
      ]
    }

IMPORTANT design notes:
- `job_id` is an opaque hash that changes day-to-day for the same role, so
  we deliberately set `Job.job_id = None`. That makes JSearch jobs dedup on
  the content key `(company, "title|team")`, which the cross-source dedup
  pass in runner.py keys on to collapse aggregator dupes of direct-ATS jobs.
- Auth lives on the httpx.Client (built and injected by `run_jsearch`), not
  in this module — so the scraper stays secret-agnostic and unit-testable
  with a plain mock client, exactly like the other scrapers.
- Free tier is ~200 requests/month; each page ≈ 1 request. We fetch one page
  per request (num_pages capped) and keep the configured query list short.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from ..models import Job, Source
from .base import Scraper, http_get_json, strip_html
from .remoteok import _ENG_TITLE_HINTS  # reuse the generous engineering gate

log = logging.getLogger(__name__)


def _is_engineering_title(title: str) -> bool:
    """Generous title-only engineering gate.

    JSearch is query-driven so results are already on-target, but a Google
    for Jobs query for "ML Data Engineer" still surfaces recruiter spam and
    adjacent non-eng roles. This trims the obvious misses before they cost
    downstream LLM tokens. Kept loose on purpose — the deep-fit stage does
    the precise tiering.
    """
    t = f" {title.lower()} "
    return any(hint in t for hint in _ENG_TITLE_HINTS)


class JSearchScraper(Scraper):
    """JSearch (RapidAPI) → many Jobs from one search query.

    Unlike Greenhouse/Lever/Ashby this isn't per-company — it's a search
    endpoint returning postings from many companies. `name` is the progress
    label; `slug` carries the query string (set by __init__). Pagination,
    date window, remote flag, and country are constructor params, not
    encoded in name/slug.

    Requires an authenticated client (X-RapidAPI-Key / -Host headers) passed
    via `client=`. The caller (`run_jsearch`) builds it; with no key the
    caller skips JSearch entirely rather than constructing this scraper.
    """

    source = Source.JSEARCH
    base_url = "https://jsearch.p.rapidapi.com/search"

    def __init__(
        self,
        name: str,
        query: str,
        *,
        num_pages: int = 1,
        date_posted: str = "week",
        remote_only: bool = False,
        country: str = "us",
        client: Any = None,
    ) -> None:
        super().__init__(name=name, slug=query, client=client)
        self.query = query
        self.num_pages = max(1, min(num_pages, 20))  # API allows 1-20
        self.date_posted = date_posted
        self.remote_only = remote_only
        self.country = country

    def _page_url(self, page: int) -> str:
        params = {
            "query": self.query,
            "page": str(page),
            "num_pages": "1",  # one page per request → predictable quota burn
            "date_posted": self.date_posted,
            "remote_jobs_only": "true" if self.remote_only else "false",
            "country": self.country,
        }
        return f"{self.base_url}?{urlencode(params)}"

    def fetch(self) -> list[Job]:
        discovered_at = datetime.now(UTC).isoformat()
        parsed: list[Job] = []
        skipped_filter = 0
        raw_total = 0

        for page in range(1, self.num_pages + 1):
            url = self._page_url(page)
            log.info("Fetching JSearch %s (page %d): %s", self.name, page, self.query)
            try:
                data = http_get_json(self.client, url)
            except Exception as e:
                # One bad page shouldn't kill the rest of the query's pages.
                log.warning("JSearch %s page %d failed: %s", self.name, page, e)
                continue

            if not isinstance(data, dict) or data.get("status") != "OK":
                log.warning(
                    "JSearch %s page %d: non-OK response (%s)",
                    self.name,
                    page,
                    data.get("status") if isinstance(data, dict) else "<not a dict>",
                )
                continue

            raw_jobs = data.get("data") or []
            raw_total += len(raw_jobs)
            for raw in raw_jobs:
                try:
                    if not isinstance(raw, dict):
                        continue
                    if not _is_engineering_title(str(raw.get("job_title") or "")):
                        skipped_filter += 1
                        continue
                    parsed.append(self._parse_one(raw, discovered_at))
                except Exception as e:
                    log.warning(
                        "Skipping malformed JSearch job %s: %s",
                        raw.get("job_id", "<no id>") if isinstance(raw, dict) else "<bad>",
                        e,
                    )
                    continue

        log.info(
            "JSearch %s: %d raw, %d filtered out, %d parsed",
            self.name,
            raw_total,
            skipped_filter,
            len(parsed),
        )
        return parsed

    def _parse_one(self, raw: dict[str, Any], discovered_at: str) -> Job:
        """Convert one JSearch job object to a Job. Raises on bad input."""
        # Per-posting company (NOT self.name — this is an aggregator).
        # Lowercased to match every other scraper's company convention and
        # so the cross-source dedup pass compares apples to apples.
        company = (raw.get("employer_name") or "").strip().lower()
        if not company:
            raise ValueError("missing employer_name")

        title = (raw.get("job_title") or "").strip()
        if not title:
            raise ValueError("missing job_title")

        url = (raw.get("job_apply_link") or "").strip()
        if not url:
            raise ValueError("missing job_apply_link")

        # Compose "City, ST, Country" so state-abbreviation tokens like
        # ", tx" in the profile's location_tokens match. Append "Remote" when
        # the API flags it, so the location filter passes remote roles even
        # when no geo is given (mirrors ashby/remoteok behavior).
        loc_parts = [
            (raw.get("job_city") or "").strip(),
            (raw.get("job_state") or "").strip(),
            (raw.get("job_country") or "").strip(),
        ]
        location = ", ".join(p for p in loc_parts if p)
        if raw.get("job_is_remote") and "remote" not in location.lower():
            location = f"{location}, Remote".strip(", ") if location else "Remote"

        # Prefer the ISO datetime; fall back to epoch seconds.
        posted_date: str | None = raw.get("job_posted_at_datetime_utc")
        if not posted_date:
            ts = raw.get("job_posted_at_timestamp")
            if isinstance(ts, int | float):
                posted_date = datetime.fromtimestamp(ts, tz=UTC).isoformat()

        description = strip_html(raw.get("job_description") or "")

        return Job(
            company=company,
            job_id=None,  # opaque per-day hash — omit to enable content dedup
            title=title,
            team=None,  # JSearch doesn't expose team/department
            source=self.source,
            url=url,
            location=location,
            posted_date=posted_date,
            discovered_at=discovered_at,
            description=description,
        )
