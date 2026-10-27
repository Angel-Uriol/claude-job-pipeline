"""Job database — atomic JSON read/write with dedup.

The store wraps a single JSON file (`data/jobs.json`) and is the only
place that reads or writes it. Everything else in the pipeline goes
through a `JobStore` instance.

Two guarantees the rest of the pipeline relies on:

1. **Writes are atomic.** We write to a sibling temp file and `os.replace`
   onto the target. A crash mid-write leaves the previous good file in
   place rather than a truncated mess. (POSIX `rename` is atomic on the
   same filesystem; that's why the temp lives in the same directory.)

2. **Re-running scrape doesn't lose scoring.** Upsert merges a freshly
   scraped job onto an existing one by refreshing only the scrape-owned
   fields (title, url, description, location, ...) and leaving scoring
   and lifecycle fields untouched.
"""

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .models import Job

# Bump this when jobs.json's on-disk shape changes in a way that would
# break older readers. The loader treats unknown future versions as an
# error rather than silently coercing.
SCHEMA_VERSION = 2


# Fields that a re-scrape is allowed to overwrite. Everything else on
# an existing Job is preserved — that's how scoring and applied-state
# survive across runs.
_REFRESHABLE_FIELDS = frozenset(
    {"title", "team", "url", "location", "posted_date", "description"}
)


class JobStore:
    """Read/write wrapper around jobs.json.

    Typical usage:

        store = JobStore("data/jobs.json", excluded_companies=cfg.excluded_companies)
        store.load()
        for j in fresh_jobs:
            store.upsert(j)
        store.save()
    """

    def __init__(
        self,
        path: str | Path,
        excluded_companies: list[str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.excluded_companies: set[str] = {
            c.lower() for c in (excluded_companies or [])
        }
        self._jobs: list[Job] = []
        self._loaded: bool = False

    # --- IO ---------------------------------------------------------------

    def load(self) -> None:
        """Load jobs.json into memory.

        If the file doesn't exist (first run), starts with an empty list.
        If the file exists but is unreadable or has the wrong schema
        version, raises — we never silently reset, because that would
        nuke real data on the first bad release.
        """
        if not self.path.exists():
            self._jobs = []
            self._loaded = True
            return

        with self.path.open("r", encoding="utf-8") as f:
            raw = json.load(f)  # raises on corrupt JSON; intentional

        # Schema version check. Missing _meta is treated as v1 (legacy).
        meta = raw.get("_meta") or {}
        version = meta.get("schema_version", 1)
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"jobs.json schema version {version} is newer than this "
                f"build supports (max {SCHEMA_VERSION}). Update the code."
            )

        loaded_jobs = [Job(**j) for j in raw.get("jobs", [])]
        # Self-healing exclusion: drop any job whose company is on the
        # exclusion list, even if it was stored before the company was
        # excluded. Keeps the store consistent with config without a
        # separate purge step. (upsert already blocks NEW excluded jobs.)
        if self.excluded_companies:
            loaded_jobs = [
                j for j in loaded_jobs if j.company.lower() not in self.excluded_companies
            ]
        self._jobs = loaded_jobs
        self._loaded = True

    def save(self) -> None:
        """Atomically persist the in-memory job list to disk.

        Algorithm:
          1. Serialize to JSON in a temp file *in the same directory*.
          2. `os.replace` the temp onto the real path (atomic on POSIX).
          3. On any error before step 2, delete the temp.
        """
        self._require_loaded()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "_meta": {
                "last_updated": _now_iso(),
                "total_jobs": len(self._jobs),
                "schema_version": SCHEMA_VERSION,
            },
            # Persist the excluded list so a future viewer of the file
            # knows why a given company isn't here.
            "excluded_companies": sorted(self.excluded_companies),
            "jobs": [j.model_dump(mode="json") for j in self._jobs],
        }

        # Sibling temp file — `os.replace` is only atomic within the same
        # filesystem, which we guarantee by putting the temp next door.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".jobs-",
            suffix=".json.tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.path)
        except Exception:
            # Best-effort cleanup. If the temp file is already gone (e.g.
            # the replace partially succeeded on a weird filesystem),
            # missing_ok keeps us from raising over a non-issue.
            Path(tmp_path).unlink(missing_ok=True)
            raise

    # --- queries ----------------------------------------------------------

    @property
    def jobs(self) -> list[Job]:
        """The live job list. Mutating individual Job objects is fine and
        will be persisted on the next save()."""
        self._require_loaded()
        return self._jobs

    def find_by_key(self, key: tuple[str, str]) -> Job | None:
        """Linear scan for a job matching a dedup key. O(n), but n stays
        small (low thousands) for this use case — a dict index would be
        premature."""
        self._require_loaded()
        for j in self._jobs:
            if j.dedup_key() == key:
                return j
        return None

    def find_by_job_id(self, company: str, job_id: str) -> Job | None:
        """Convenience: find by `(company, job_id)` without building the
        dedup key by hand."""
        return self.find_by_key((company.lower(), job_id))

    # --- mutations --------------------------------------------------------

    def upsert(self, job: Job) -> str:
        """Insert a new job or merge into an existing one.

        Returns one of:
          - "inserted" — wasn't seen before; added to the list.
          - "updated"  — was seen before; scrape-owned fields refreshed.
          - "excluded" — company is on the exclusion list; nothing stored.
        """
        self._require_loaded()

        if job.company.lower() in self.excluded_companies:
            return "excluded"

        existing = self.find_by_key(job.dedup_key())
        if existing is None:
            self._jobs.append(job)
            return "inserted"

        # Merge: refresh only the fields a scraper is allowed to overwrite.
        # Scoring fields (fit_score, fit_verdict, ...), lifecycle fields
        # (status, applied_at, ...), and the extracted blob are all
        # preserved — those are written by later phases and the human.
        merged = existing.model_dump()
        incoming = job.model_dump()
        for field in _REFRESHABLE_FIELDS:
            new_val = incoming.get(field)
            if new_val is not None:
                merged[field] = new_val

        idx = self._jobs.index(existing)
        self._jobs[idx] = Job(**merged)
        return "updated"

    def remove_jobs(self, predicate: Callable[[Job], bool]) -> int:
        """Drop every job for which `predicate(job)` is True.

        Returns the number removed. Used by the cross-source dedup pass to
        collapse aggregator-sourced duplicates of direct-ATS jobs without
        poking the private list from outside the store. Caller saves after.
        """
        self._require_loaded()
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if not predicate(j)]
        return before - len(self._jobs)

    # --- internal ---------------------------------------------------------

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise RuntimeError(
                "JobStore.load() must be called before accessing the store."
            )


def _now_iso() -> str:
    """UTC timestamp with timezone, ISO 8601 format."""
    return datetime.now(UTC).isoformat()
