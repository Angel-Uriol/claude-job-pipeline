"""Hand off scored jobs to the built-in tracker (the final step).

Discover finds + scores + tailors; the tracker (`tracker_db`, the Track tab)
is the system of record for applications. This module maps our `Job`s onto
the tracker's `jobs` schema and inserts them via `tracker_db.insert_jobs`
(which applies dedup + the per-company cap).

Only apply/consider verdicts are exported, and never jobs you've discarded
(dismissed) or that were skipped/rejected/dead. The Claude-tailored .docx is
carried over via `resume_path` so the tracker registers the resume you already
generated — no regeneration.
"""

from __future__ import annotations

import logging
from typing import Any

from . import tracker_db
from .config import Config
from .models import Job
from .runner import ProgressCallback, ProgressEvent
from .store import JobStore

log = logging.getLogger(__name__)

# Statuses that should NEVER be pushed to the tracker, regardless of verdict —
# you've reviewed these out (dismissed) or they're dead ends.
_EXCLUDED_STATUSES = {
    "dismissed",
    "skipped",
    "rejected",
    "auto_rejected",
    "dead",
    "mismatch",
    "excluded",
}

# Discover status → tracker status.
_STATUS_MAP = {
    "new": "new",
    "pending": "new",
    "resume_only": "to_apply",  # resume ready, not yet submitted
    "applied": "applied",
}

def _emit(progress: ProgressCallback | None, **kwargs: Any) -> None:
    if progress is not None:
        progress(ProgressEvent(**kwargs))


def _location_pass(location: str, cfg: Config | None = None) -> str:
    """Map a location string to the tracker's location_pass label."""
    s = (location or "").lower()
    if cfg is not None and any(t.lower() in s for t in cfg.filters.location_tokens):
        return "Yes (Preferred)"
    remote = cfg.filters.remote_tokens if cfg is not None else ["remote", "anywhere"]
    if any(t.lower() in s for t in remote):
        return "Yes (Remote)"
    return "Unknown"


def _role_tier(function_score: float | None) -> str | None:
    """Derive a coarse A/B/C tier from the deep-fit function sub-score."""
    if function_score is None:
        return None
    if function_score >= 9.0:
        return "A"
    if function_score >= 7.0:
        return "B"
    return "C"


def _map_job(job: Job, cfg: Config | None = None) -> dict[str, Any]:
    """Map one Discover Job onto the tracker `jobs` schema."""
    subs = job.fit_subscores
    extracted = job.extracted or {}
    verdict = job.fit_verdict.value.upper() if job.fit_verdict else "CONSIDER"
    return {
        "company": job.company,
        "role": job.title,
        "job_id": job.job_id,
        "url": job.url,
        "location": job.location,
        "posted": job.posted_date,
        "level": extracted.get("level"),
        "fit_score": job.fit_score,
        "stack_score": subs.stack if subs else None,
        "level_score": subs.level if subs else None,
        "function_score": subs.function if subs else None,
        "bridgeability": subs.bridgeability if subs else None,
        "desirability": subs.desirability if subs else None,
        "verdict": verdict,
        "bridge_notes": job.honest_bridge_notes,
        "full_jd_text": job.description,
        "location_pass": _location_pass(job.location, cfg),
        "h1b": extracted.get("visa_stance"),
        "role_tier": _role_tier(subs.function if subs else None),
        "status": _STATUS_MAP.get(job.status.value, "new"),
        "applied_at": job.applied_at,
        "starred": job.starred,
        "resume_path": job.resume_path,
        # Discover doesn't extract these structured lists.
        "key_requirements": [],
        "key_nice_to_haves": [],
    }


def build_tracker_export(
    jobs: list[Job], *, verdicts: list[str], cfg: Config | None = None
) -> list[dict[str, Any]]:
    """Select + map the jobs to hand off.

    Includes only jobs whose `fit_verdict` is in `verdicts` AND whose status
    isn't in `_EXCLUDED_STATUSES` (so discarded/dead jobs never reach the tracker).
    """
    allowed = {v.lower() for v in verdicts}
    out: list[dict[str, Any]] = []
    for job in jobs:
        if job.fit_verdict is None or job.fit_verdict.value not in allowed:
            continue
        if job.status.value in _EXCLUDED_STATUSES:
            continue
        out.append(_map_job(job, cfg))
    return out


def run_handoff(
    cfg: Config,
    store: JobStore,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Copy apply/consider jobs (and their resumes) into the tracker DB.

    Returns counts: exported / imported / skipped. Never raises on tracker
    trouble — emits a clear message and returns an `error` key instead.
    """
    counts: dict[str, Any] = {"exported": 0, "imported": 0}

    if not cfg.tracker.enabled:
        _emit(progress, phase="handoff", message="Tracker handoff is disabled in config.")
        return counts

    export = build_tracker_export(store.jobs, verdicts=cfg.tracker.verdicts, cfg=cfg)
    counts["exported"] = len(export)
    if not export:
        _emit(progress, phase="handoff", message="No apply/consider jobs to hand off.")
        return counts

    try:
        result = tracker_db.insert_jobs(cfg, export, source="discover")
    except Exception as e:  # noqa: BLE001 - surface any DB problem to the UI
        msg = f"Tracker import failed: {e}"
        log.error(msg)
        _emit(progress, phase="handoff", message=msg, extra={"error": True})
        counts["error"] = msg
        return counts

    counts["imported"] = result["inserted"]
    counts["skipped"] = result["skipped"]
    _emit(
        progress,
        phase="handoff",
        message=(
            f"Tracker updated — inserted={result['inserted']} "
            f"resumes={result['resumes']} skipped={result['skipped']}"
        ),
    )
    return counts
