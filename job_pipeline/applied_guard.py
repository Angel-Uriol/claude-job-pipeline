"""Never resurface jobs you've already applied to (or are tracking).

The Track tab's database is the record of what you've acted on. This module
reads it into a fast lookup and is used in two places:

* **Scrape / JSearch / Chrome Claude import** — a posting that matches a
  tracked job is skipped before it reaches Discover (and before any LLM cost).
  This also catches *re-posts*: the same company + same role title under a
  new job id.
* **Status sync** — when you mark a job applied / rejected / withdrawn on the
  Track tab, the matching Discover row follows, so it drops out of the inbox.

Matching: (company, ATS job id) first, then (company, normalized title).
Company names fold the same way as the target-company list ("Stripe, Inc."
== "stripe").
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .config import Config
from .models import Job, Status
from .target_companies import _normalize as _norm_company
from .tracker_db import db_path

# Tracker statuses that mean "you've acted on this" → mirrored into Discover.
_TO_DISCOVER_STATUS: dict[str, Status] = {
    "applied": Status.APPLIED,
    "recruiter_screen": Status.APPLIED,
    "phone_screen": Status.APPLIED,
    "take_home": Status.APPLIED,
    "onsite": Status.APPLIED,
    "offer": Status.APPLIED,
    "rejected": Status.REJECTED,
    "auto_rejected": Status.REJECTED,
    "withdrawn": Status.DISMISSED,
    "skip": Status.DISMISSED,
}

# Discover statuses that may be overwritten by the sync (never undo your own
# manual choices like dismissed / dead).
_SYNCABLE = {Status.NEW, Status.PENDING, Status.RESUME_ONLY}


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


@dataclass
class TrackedIndex:
    by_id: dict[tuple[str, str], str] = field(default_factory=dict)
    by_role: dict[tuple[str, str], str] = field(default_factory=dict)

    def match(self, job: Job) -> str | None:
        """Tracker status of the matching tracked job, or None."""
        company = _norm_company(job.company)
        if job.job_id:
            hit = self.by_id.get((company, str(job.job_id)))
            if hit is not None:
                return hit
        return self.by_role.get((company, _norm_title(job.title)))

    def __len__(self) -> int:
        return len(self.by_role)


def load_tracked_index(cfg: Config) -> TrackedIndex:
    """Read every tracker row (hidden ones too). Missing DB → empty index."""
    idx = TrackedIndex()
    p = db_path(cfg)
    if not p.exists():
        return idx
    conn = sqlite3.connect(p)
    try:
        rows = conn.execute("SELECT company, job_id, role, IFNULL(status, 'new') FROM jobs")
        for company, job_id, role, status in rows:
            c = _norm_company(company or "")
            if job_id:
                idx.by_id[(c, str(job_id))] = status
            if role:
                idx.by_role[(c, _norm_title(role))] = status
    except sqlite3.Error:
        return TrackedIndex()  # unreadable DB → don't block scraping
    finally:
        conn.close()
    return idx


def sync_statuses(jobs: list[Job], index: TrackedIndex) -> int:
    """Mirror Track-tab progress onto Discover rows. Returns rows changed."""
    changed = 0
    for job in jobs:
        status = index.match(job)
        target = _TO_DISCOVER_STATUS.get(status or "")
        if target is not None and job.status in _SYNCABLE and job.status != target:
            job.status = target
            changed += 1
    return changed
