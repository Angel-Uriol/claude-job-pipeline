"""Lever scraper.

Lever's public postings API:

    https://api.lever.co/v0/postings/<slug>?mode=json

Response is a JSON ARRAY (not an object) — that's the gotcha vs.
Greenhouse. Each posting looks like:

    [
      {
        "id": "abc-def-uuid",
        "text": "Senior Data Engineer",
        "hostedUrl": "https://jobs.lever.co/<slug>/abc-def-uuid",
        "createdAt": 1709308800000,           // ms epoch UTC
        "categories": {
          "location": "San Francisco",
          "team": "Engineering",
          "department": "Data"
        },
        "description": "<p>HTML</p>",
        "descriptionPlain": "plain text version",
        "lists": [{"text": "Responsibilities", "content": "<ul>..."}]
      },
      ...
    ]

We prefer `descriptionPlain` when available (Lever sometimes ships it
pre-stripped); fall back to `description` with HTML stripping.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..models import Job, Source
from .base import Scraper, http_get_json, strip_html

log = logging.getLogger(__name__)


class LeverScraper(Scraper):
    """One Lever board → many Jobs."""

    source = Source.LEVER
    base_url_template = "https://api.lever.co/v0/postings/{slug}?mode=json"

    def fetch(self) -> list[Job]:
        url = self.base_url_template.format(slug=self.slug)
        log.info("Fetching Lever board %s: %s", self.name, url)
        data = http_get_json(self.client, url)

        # Lever returns a top-level list, not a dict.
        raw_jobs = data if isinstance(data, list) else []
        discovered_at = datetime.now(UTC).isoformat()

        parsed: list[Job] = []
        for raw in raw_jobs:
            try:
                parsed.append(self._parse_one(raw, discovered_at))
            except Exception as e:
                log.warning(
                    "Skipping malformed Lever job at %s: %s",
                    raw.get("hostedUrl", "<no url>") if isinstance(raw, dict) else "<bad>",
                    e,
                )
                continue

        log.info("Lever %s: %d raw, %d parsed", self.name, len(raw_jobs), len(parsed))
        return parsed

    def _parse_one(self, raw: dict[str, Any], discovered_at: str) -> Job:
        """Convert one Lever JSON object to a Job. Raises on bad input."""
        job_id = str(raw["id"])
        title = raw["text"].strip()
        url = raw["hostedUrl"]

        categories = raw.get("categories") or {}
        location = (categories.get("location") or "").strip()
        # Lever splits "team" and "department" — use team if present,
        # else department. Both are useful but team is closer to what
        # Greenhouse calls a department.
        team = (categories.get("team") or categories.get("department") or "").strip() or None

        # createdAt is ms-since-epoch UTC. Normalize to ISO 8601 so
        # downstream code only has to handle one date format.
        posted_date: str | None = None
        created_ms = raw.get("createdAt")
        if isinstance(created_ms, int | float):
            posted_date = datetime.fromtimestamp(created_ms / 1000, tz=UTC).isoformat()

        # Build a description from whatever Lever gives us. Some postings
        # include section-by-section lists (Responsibilities, etc.) on
        # top of the main description — concatenate the lot.
        description_parts: list[str] = []
        if (plain := raw.get("descriptionPlain")):
            description_parts.append(plain)
        elif (html := raw.get("description")):
            description_parts.append(strip_html(html))
        for section in raw.get("lists") or []:
            heading = (section.get("text") or "").strip()
            content = strip_html(section.get("content") or "")
            if heading or content:
                description_parts.append(f"{heading}: {content}".strip(": ").strip())

        description = "\n\n".join(p for p in description_parts if p)

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
