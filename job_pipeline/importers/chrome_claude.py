"""Import jobs from Chrome Claude JSONL output.

The Chrome Claude prompts in the digest produce one JSON object per line.
This importer parses that file, validates each row, builds a `Job` with
`source=CHROME_CLAUDE`, and upserts into the store. Pre-filter and
deep-fit pick the new jobs up naturally on the next run — same as a
scraped job.

Failure modes:
- Malformed JSON on a line → log, skip the line, keep going.
- JSON missing required fields → log, skip.
- Company on the exclusion list → counted as "excluded", skipped.

Returns `ImportStats` so the CLI can show a summary.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..models import Job, Source
from ..store import JobStore

log = logging.getLogger(__name__)


# Fields the Chrome Claude prompt is instructed to return. Everything
# else is optional. We're permissive: empty strings get coerced to None
# where the model allows it.
REQUIRED_FIELDS = ("company", "title", "url", "description")


@dataclass
class ImportStats:
    """Per-line outcome counts and the parsed lines that failed."""

    inserted: int = 0
    updated: int = 0
    excluded: int = 0
    skipped_malformed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return self.inserted + self.updated + self.excluded + self.skipped_malformed


def import_chrome_claude_jsonl(path: Path, store: JobStore) -> ImportStats:
    """Read `path`, upsert each row into `store`, return stats.

    The caller is responsible for calling `store.save()` after — same
    pattern as the scraper command. Atomic save → either all the
    imports land, or none do (on crash).
    """
    stats = ImportStats()
    discovered_at = datetime.now(UTC).isoformat()

    with path.open("r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue  # blank or comment line — skip silently

            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                stats.skipped_malformed += 1
                stats.errors.append(f"line {lineno}: invalid JSON ({e.msg})")
                continue

            if not isinstance(row, dict):
                stats.skipped_malformed += 1
                stats.errors.append(f"line {lineno}: top-level value is not an object")
                continue

            missing = [k for k in REQUIRED_FIELDS if not row.get(k)]
            if missing:
                stats.skipped_malformed += 1
                stats.errors.append(
                    f"line {lineno}: missing required field(s): {', '.join(missing)}"
                )
                continue

            try:
                job = _build_job(row, discovered_at=discovered_at)
            except Exception as e:
                stats.skipped_malformed += 1
                stats.errors.append(f"line {lineno}: failed to build Job: {e}")
                continue

            outcome = store.upsert(job)
            if outcome == "inserted":
                stats.inserted += 1
            elif outcome == "updated":
                stats.updated += 1
            elif outcome == "excluded":
                stats.excluded += 1

    log.info(
        "import: inserted=%d updated=%d excluded=%d malformed=%d",
        stats.inserted,
        stats.updated,
        stats.excluded,
        stats.skipped_malformed,
    )
    return stats


def _build_job(row: dict[str, Any], *, discovered_at: str) -> Job:
    """Build a Chrome-Claude-sourced Job from one JSONL row.

    Coerces empty strings to None for optional fields so the model's
    `extra="forbid"` doesn't get confused, and so dedup logic treats
    missing-team consistently across sources.
    """

    def _opt(key: str) -> str | None:
        v = row.get(key)
        if v is None or v == "":
            return None
        return str(v)

    return Job(
        company=str(row["company"]).strip(),
        job_id=_opt("job_id"),
        title=str(row["title"]).strip(),
        team=_opt("team"),
        source=Source.CHROME_CLAUDE,
        url=str(row["url"]).strip(),
        location=str(row.get("location") or "").strip(),
        posted_date=_opt("posted_date"),
        discovered_at=discovered_at,
        description=str(row["description"]),
    )
