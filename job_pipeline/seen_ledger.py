"""Persistent ledger of every job key ever discovered.

The store (`jobs.json`) already dedups against the jobs it currently holds —
including `dismissed` tombstones, which survive re-scrapes. This ledger is the
DURABLE backstop on top of that: it remembers a job's key even if that row is
later deleted from `jobs.json`, so a fresh discovery run never re-surfaces a
posting the user already reviewed.

Keys are `Job.dedup_key()` tuples — `(company, job_id)` or
`(company, "title|team")` — joined by a unit separator and stored as a JSON
list at `data/seen_jobs.json` (sibling of jobs.json). Writes are atomic
(temp-file + os.replace), mirroring JobStore.save().
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Unit Separator — cannot appear in company/title/job_id text, so it's a safe
# join for the (a, b) dedup-key tuple.
_SEP = "\x1f"


class SeenLedger:
    """A set of dedup-keys persisted to a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._keys: set[str] = set()
        self._loaded = False

    @staticmethod
    def _encode(key: tuple[str, str]) -> str:
        return f"{key[0]}{_SEP}{key[1]}"

    def load(self) -> None:
        """Load the ledger. Missing/corrupt file → start empty (never raises)."""
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = []
            keys = raw.get("keys", []) if isinstance(raw, dict) else raw
            self._keys = {str(k) for k in keys} if isinstance(keys, list) else set()
        else:
            self._keys = set()
        self._loaded = True

    def contains(self, key: tuple[str, str]) -> bool:
        return self._encode(key) in self._keys

    def add(self, key: tuple[str, str]) -> None:
        self._keys.add(self._encode(key))

    def __len__(self) -> int:
        return len(self._keys)

    def save(self) -> None:
        """Atomically persist the ledger (temp-file + os.replace)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"keys": sorted(self._keys)}
        fd, tmp_path = tempfile.mkstemp(
            prefix=".seen-", suffix=".json.tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_path, self.path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise


def ledger_path_for(jobs_json_path: str | Path) -> Path:
    """The ledger lives next to jobs.json as `seen_jobs.json`."""
    return Path(jobs_json_path).with_name("seen_jobs.json")
