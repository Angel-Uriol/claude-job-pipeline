"""The apply checklist and plain-language fit summaries.

Pure functions over a Job (and optionally its tracker row) so the Today
screen, the job drawer and the Applications page all agree on what "done"
means and what the next step is. No I/O here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import Job, ScoreResult, Status

FOLLOW_UP_DAYS = 7

# Tracker statuses that mean "you have an interview coming".
INTERVIEW_STATUSES = frozenset({"recruiter_screen", "phone_screen", "take_home", "onsite"})

# Plain words for the five scoring axes (mirrors ui/lib/plain.ts).
AXIS_WORDS = {
    "stack": "skills overlap",
    "level": "seniority fit",
    "function": "role type",
    "bridgeability": "gaps you can explain",
    "desirability": "company appeal",
}


def score_label(score: float | None) -> str:
    """1–10 fit score → words a non-engineer understands."""
    if score is None:
        return "Not scored yet"
    if score >= 8:
        return "Strong match"
    if score >= 6.5:
        return "Good match"
    if score >= 5:
        return "Possible match"
    return "Weak match"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _first_sentence(text: str, limit: int = 180) -> str:
    """First sentence of a note, cut at a word boundary with "…" if long."""
    line = text.strip().split("\n")[0]
    m = re.match(r"(.+?[.!?])(\s|$)", line)
    sentence = m.group(1) if m else line
    if len(sentence) <= limit:
        return sentence
    return sentence[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def reasons_and_gaps(job: Job) -> tuple[list[str], list[str]]:
    """Why it fits / what's missing. Uses the stored plain-language lists;
    falls back to the subscores for jobs scored before those existed."""
    if job.fit_reasons or job.fit_gaps:
        return job.fit_reasons[:3], job.fit_gaps[:2]
    sub: ScoreResult | None = job.fit_subscores
    if sub is None:
        return [], []
    scores = {k: getattr(sub, k, None) for k in AXIS_WORDS}
    known = {k: v for k, v in scores.items() if isinstance(v, int | float)}
    strong = sorted((k for k, v in known.items() if v >= 7), key=lambda k: -known[k])
    weak = sorted((k for k, v in known.items() if v <= 5), key=lambda k: known[k])
    reasons = [f"Strong {AXIS_WORDS[k]}" for k in strong[:3]]
    gaps = [f"Weaker {AXIS_WORDS[k]}" for k in weak[:1]]
    if job.honest_bridge_notes and len(gaps) < 2:
        gaps.append(_first_sentence(job.honest_bridge_notes))
    return reasons, gaps


def checklist(job: Job, tracker_row: dict[str, Any] | None = None,
              now: datetime | None = None) -> list[dict[str, Any]]:
    """Ordered apply steps with done flags. `follow_up` and `interview` only
    appear once they make sense."""
    now = now or datetime.now(UTC)
    row = tracker_row or {}
    applied = job.status == Status.APPLIED or bool(job.applied_at) or bool(row.get("applied_at"))
    steps: list[dict[str, Any]] = [
        {"id": "resume", "label": "Tailored resume ready",
         "done": bool(job.resume_path or row.get("resume_path"))},
        {"id": "cover_letter", "label": "Cover letter ready",
         "done": bool(job.cover_letter_path or row.get("cover_letter_path")), "optional": True},
        {"id": "open", "label": "Open the application page",
         "done": bool(job.apply_opened_at) or applied},
        {"id": "applied", "label": "Mark as applied", "done": applied},
    ]
    if applied:
        if row:
            due = _parse(row.get("follow_up_at"))  # tracker copy wins; NULL = done
        elif job.follow_up_at == "done":
            due = None
        else:
            due = _parse(job.follow_up_at)
            base = _parse(job.applied_at)
            if due is None and base is not None:
                due = base + timedelta(days=FOLLOW_UP_DAYS)
        steps.append({
            "id": "follow_up", "label": "Follow up with the recruiter",
            "done": due is None,  # cleared = done
            "due_at": due.isoformat() if due else None,
            "due_now": bool(due and due <= now),
        })
    if row.get("status") in INTERVIEW_STATUSES:
        steps.append({"id": "interview", "label": "Prepare for the interview", "done": False})
    return steps


def next_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First step that isn't done (optional steps are skipped over)."""
    for s in steps:
        if s.get("optional") or s["done"]:
            continue
        if s["id"] == "follow_up" and not s.get("due_now"):
            continue  # not yet — don't nag early
        return s
    return None
