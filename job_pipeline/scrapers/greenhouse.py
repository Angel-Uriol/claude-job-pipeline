"""Greenhouse scraper.

Greenhouse exposes a free, public JSON endpoint per board. We hit:

    https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true

The `?content=true` is critical — without it, the `content` field (the
full JD HTML) is omitted and we'd have to do a second request per job.

Response shape (only the fields we use; everything else ignored):

    {
      "jobs": [
        {
          "id": 4567890,
          "title": "Software Engineer, ML Platform",
          "updated_at": "2026-04-15T18:32:00Z",
          "absolute_url": "https://...",
          "location": {"name": "San Francisco, CA"},
          "departments": [{"name": "Engineering"}],
          "content": "<p>full JD HTML</p>"
        }
      ]
    }
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..models import Job, Source
from .base import Scraper, http_get_json, strip_html

log = logging.getLogger(__name__)


class GreenhouseScraper(Scraper):
    """One Greenhouse board → many Jobs."""

    source = Source.GREENHOUSE
    base_url_template = (
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    )

    def fetch(self) -> list[Job]:
        url = self.base_url_template.format(slug=self.slug)
        log.info("Fetching Greenhouse board %s: %s", self.name, url)
        data = http_get_json(self.client, url)

        raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
        discovered_at = datetime.now(UTC).isoformat()

        parsed: list[Job] = []
        for raw in raw_jobs:
            try:
                parsed.append(self._parse_one(raw, discovered_at))
            except Exception as e:
                # Per spec: never let a single bad job tank the run.
                log.warning(
                    "Skipping malformed Greenhouse job at %s: %s",
                    raw.get("absolute_url", "<no url>"),
                    e,
                )
                continue

        log.info(
            "Greenhouse %s: %d raw, %d parsed",
            self.name,
            len(raw_jobs),
            len(parsed),
        )
        return parsed

    def _parse_one(self, raw: dict[str, Any], discovered_at: str) -> Job:
        """Convert one Greenhouse JSON object to a Job. Raises on bad input."""
        # The job_id is always present on Greenhouse and is the
        # authoritative identity — we cast to str so it round-trips
        # cleanly through JSON regardless of the source type.
        job_id = str(raw["id"])

        title = raw["title"].strip()
        url = raw["absolute_url"]

        # Location can be missing entirely on edge cases.
        location_blob = raw.get("location") or {}
        location = (location_blob.get("name") or "").strip()

        # Departments are an array; take the first as `team`. Many
        # postings list multiple departments — using just the first
        # keeps the tracker readable.
        departments = raw.get("departments") or []
        team = departments[0].get("name", "").strip() if departments else None

        # `updated_at` exists on every Greenhouse job; `first_published`
        # is what we'd really want for "posted_date" but it's not always
        # there. Use whichever we can find, prefer first_published.
        posted_date = raw.get("first_published") or raw.get("updated_at")

        # `content` is HTML. Strip to plain text before storing — the
        # LLM pre-filter and the tracker both want readable text.
        description_html = raw.get("content", "")
        description = strip_html(description_html)

        return Job(
            company=self.name,
            job_id=job_id,
            title=title,
            team=team or None,
            source=self.source,
            url=url,
            location=location,
            posted_date=posted_date,
            discovered_at=discovered_at,
            description=description,
        )
