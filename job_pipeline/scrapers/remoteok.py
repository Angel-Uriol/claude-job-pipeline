"""RemoteOK scraper — aggregator for remote-only postings.

RemoteOK is a free aggregator with a no-auth JSON endpoint:

    https://remoteok.com/api

The response is a top-level ARRAY whose FIRST element is a legal
disclaimer (with `last_updated` + `legal` keys, no job fields). Actual
jobs start at index 1. Each looks like:

    {
      "slug": "remote-senior-data-engineer-foo-1234567",
      "id": "1234567",
      "epoch": 1778947203,
      "date": "2026-05-16T16:00:03+00:00",
      "company": "Foo Co",
      "position": "Senior Data Engineer",
      "tags": ["python", "aws", "engineer", "engineering", ...],
      "description": "<p>HTML</p>",
      "location": "Worldwide",
      "apply_url": "https://...",
      "salary_min": 120000,
      "salary_max": 180000,
      "url": "https://remoteok.com/remote-jobs/<id>"
    }

Two practical notes:

1. The endpoint returns ~100 most-recent listings. RemoteOK's `?offset=N`
   pagination exists but is brittle and undocumented — we just take the
   recent batch and let downstream max-age + dedup handle the rest.

2. Signal/noise: RemoteOK covers everything from "Senior Engineer" to
   "Social Media Manager." We filter at scrape time to engineering-shaped
   tags/titles so the LLM pre-filter never has to chew through "Customer
   Support" postings. The filter is intentionally generous — pre-filter
   does the precise tiering.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..models import Job, Source
from .base import Scraper, http_get_json, strip_html

log = logging.getLogger(__name__)


# Tags / title keywords we accept. Lowercase. Generous on purpose —
# bridgeability/function axes get final say in deep-fit.
_ENG_TAGS = {
    "engineer",
    "engineering",
    "developer",
    "dev",
    "python",
    "javascript",
    "typescript",
    "scala",
    "java",
    "go",
    "rust",
    "c++",
    "data",
    "data engineer",
    "data engineering",
    "ml",
    "ai",
    "machine learning",
    "backend",
    "frontend",
    "fullstack",
    "full stack",
    "full-stack",
    "platform",
    "infrastructure",
    "infra",
    "devops",
    "sre",
    "site reliability",
    "cloud",
    "aws",
    "gcp",
    "azure",
    "kubernetes",
    "docker",
    "spark",
    "pyspark",
    "airflow",
    "iceberg",
    "snowflake",
    "postgres",
    "sql",
    "react",
    "pytorch",
    "tensorflow",
    "llm",
    "rag",
}

_ENG_TITLE_HINTS = (
    "engineer",
    "developer",
    "architect",
    "data scientist",
    " ml ",
    "ml ",
    " mle",
    "machine learning",
    "platform",
    "infrastructure",
    "devops",
    "sre",
    "backend",
    "frontend",
    "fullstack",
    "full stack",
    "full-stack",
)


def _is_engineering(raw: dict[str, Any]) -> bool:
    """Generous engineering filter on a raw RemoteOK posting."""
    title = (raw.get("position") or "").lower()
    if any(hint in title for hint in _ENG_TITLE_HINTS):
        return True
    tags = [str(t).lower() for t in (raw.get("tags") or [])]
    return any(t in _ENG_TAGS for t in tags)


class RemoteOKScraper(Scraper):
    """RemoteOK aggregator → many Jobs.

    Unlike Greenhouse/Lever this isn't per-company — it's one endpoint
    that returns postings from many companies. We stamp `company` from
    each posting's `company` field instead of a configured `name`.

    The constructor accepts `name` and `slug` for API symmetry but
    ignores both (the endpoint is fixed). The name is reused as a
    label in the scrape progress emit (e.g. "remoteok").
    """

    source = Source.REMOTEOK
    base_url_template = "https://remoteok.com/api"

    def fetch(self) -> list[Job]:
        url = self.base_url_template
        log.info("Fetching RemoteOK feed: %s", url)
        data = http_get_json(self.client, url)

        # Response is a list whose [0] is a legal disclaimer.
        raw_jobs = data[1:] if isinstance(data, list) and len(data) > 1 else []
        discovered_at = datetime.now(UTC).isoformat()

        parsed: list[Job] = []
        skipped_filter = 0
        for raw in raw_jobs:
            try:
                if not isinstance(raw, dict):
                    continue
                if not _is_engineering(raw):
                    skipped_filter += 1
                    continue
                parsed.append(self._parse_one(raw, discovered_at))
            except Exception as e:
                log.warning(
                    "Skipping malformed RemoteOK job %s: %s",
                    raw.get("id", "<no id>") if isinstance(raw, dict) else "<bad>",
                    e,
                )
                continue

        log.info(
            "RemoteOK: %d raw, %d filtered out, %d parsed",
            len(raw_jobs),
            skipped_filter,
            len(parsed),
        )
        return parsed

    def _parse_one(self, raw: dict[str, Any], discovered_at: str) -> Job:
        """Convert one RemoteOK JSON object to a Job."""
        job_id = str(raw["id"])
        title = (raw.get("position") or "").strip()
        if not title:
            raise ValueError("missing position")

        # `company` per posting (NOT self.name — this is an aggregator).
        # Lowercase to match other scrapers' company-name convention.
        company = (raw.get("company") or "").strip().lower()
        if not company:
            raise ValueError("missing company")

        # Prefer the public listing URL over the apply form so the
        # human lands on the full JD page.
        url = raw.get("url") or raw.get("apply_url") or ""
        if not url:
            raise ValueError("missing url")

        # RemoteOK is, well, remote — but the `location` field can hold
        # geo constraints like "USA only" or "Worldwide". Keep what's
        # there; if empty, stamp "Remote" so the CA-or-remote filter
        # passes it through.
        location = (raw.get("location") or "").strip() or "Remote"

        # `date` is ISO 8601 — pass through. `epoch` is also there but
        # iso form keeps the store consistent across sources.
        posted_date = raw.get("date")

        # Strip HTML from the description.
        description = strip_html(raw.get("description") or "")

        return Job(
            company=company,
            job_id=job_id,
            title=title,
            team=None,  # RemoteOK doesn't expose team/department
            source=self.source,
            url=url,
            location=location,
            posted_date=posted_date,
            discovered_at=discovered_at,
            description=description,
        )
