"""Ashby scraper.

Ashby's public job board API (no auth required):

    https://api.ashbyhq.com/posting-api/job-board/<slug>

Response shape (only the fields we use; everything else ignored):

    {
      "apiVersion": "...",
      "jobs": [
        {
          "id": "uuid-here",
          "title": "Senior Software Engineer",
          "department": "Engineering",
          "team": "Platform",
          "employmentType": "FullTime",
          "location": "San Francisco",
          "secondaryLocations": [{"location": "Remote"}],
          "publishedAt": "2025-04-12T18:32:00.000+00:00",
          "isListed": true,
          "isRemote": false,
          "workplaceType": "Onsite",
          "jobUrl": "https://jobs.ashbyhq.com/<slug>/<uuid>",
          "applyUrl": "https://jobs.ashbyhq.com/<slug>/<uuid>/application",
          "descriptionHtml": "<p>...</p>",
          "descriptionPlain": "plain text version"
        }
      ]
    }

We prefer `descriptionPlain` (Ashby ships it pre-stripped); fall back
to `descriptionHtml` with HTML stripping. `jobUrl` is the canonical
public posting URL — use it over `applyUrl` so the human gets the
posting page (with full JD) instead of the bare application form.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..models import Job, Source
from .base import Scraper, http_get_json, strip_html

log = logging.getLogger(__name__)


class AshbyScraper(Scraper):
    """One Ashby board → many Jobs."""

    source = Source.ASHBY
    base_url_template = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

    def fetch(self) -> list[Job]:
        url = self.base_url_template.format(slug=self.slug)
        log.info("Fetching Ashby board %s: %s", self.name, url)
        data = http_get_json(self.client, url)

        raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
        discovered_at = datetime.now(UTC).isoformat()

        parsed: list[Job] = []
        for raw in raw_jobs:
            try:
                # Skip postings the company has explicitly unlisted.
                # Ashby keeps unlisted jobs in the API response but flags
                # them; treat that as "not actively hiring."
                if raw.get("isListed") is False:
                    continue
                parsed.append(self._parse_one(raw, discovered_at))
            except Exception as e:
                # Per spec: never let a single bad job tank the run.
                log.warning(
                    "Skipping malformed Ashby job at %s: %s",
                    raw.get("jobUrl", "<no url>") if isinstance(raw, dict) else "<bad>",
                    e,
                )
                continue

        log.info(
            "Ashby %s: %d raw, %d parsed",
            self.name,
            len(raw_jobs),
            len(parsed),
        )
        return parsed

    def _parse_one(self, raw: dict[str, Any], discovered_at: str) -> Job:
        """Convert one Ashby JSON object to a Job. Raises on bad input."""
        job_id = str(raw["id"])
        title = raw["title"].strip()
        # jobUrl is the human-readable posting page; applyUrl is the form.
        url = raw.get("jobUrl") or raw["applyUrl"]

        # Ashby surfaces several location signals — primary location plus
        # an `isRemote` flag plus a workplaceType ("Remote"/"Onsite"/
        # "Hybrid"). Merge them so the downstream CA-or-remote filter
        # can see "Remote" when only `isRemote: true` is set without a
        # location string.
        location_parts: list[str] = []
        primary_loc = (raw.get("location") or "").strip()
        if primary_loc:
            location_parts.append(primary_loc)
        for sec in raw.get("secondaryLocations") or []:
            sec_name = (sec.get("location") or "").strip() if isinstance(sec, dict) else ""
            if sec_name and sec_name not in location_parts:
                location_parts.append(sec_name)
        workplace = (raw.get("workplaceType") or "").strip()
        if workplace and workplace.lower() == "remote" and not any(
            "remote" in p.lower() for p in location_parts
        ):
            location_parts.append("Remote")
        elif raw.get("isRemote") and not any(
            "remote" in p.lower() for p in location_parts
        ):
            location_parts.append("Remote")
        location = " · ".join(location_parts)

        # Team is more specific than department; prefer team.
        team = (raw.get("team") or raw.get("department") or "").strip() or None

        # publishedAt is ISO 8601 with timezone — pass through unchanged.
        posted_date: str | None = raw.get("publishedAt")

        # Prefer descriptionPlain (pre-stripped); fall back to HTML strip.
        description = (raw.get("descriptionPlain") or "").strip()
        if not description:
            description = strip_html(raw.get("descriptionHtml") or "")

        return Job(
            company=self.name,
            job_id=job_id,
            title=title,
            team=team,
            source=self.source,
            url=url,
            location=location,
            posted_date=posted_date,
            discovered_at=discovered_at,
            description=description,
        )
