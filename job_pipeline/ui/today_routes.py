"""The Today screen: what to look at and what's waiting on you.

GET  /api/today                       top new matches + "waiting on you" list
POST /api/jobs/{key}/opened           record that the apply page was opened
POST /api/follow-ups                  mark a follow-up done, or snooze it

Reads jobs.json and the tracker DB; writes only the small checklist fields.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .. import paths, tracker_db
from ..checklist import (
    INTERVIEW_STATUSES,
    checklist,
    next_step,
    reasons_and_gaps,
    score_label,
)
from ..config import Config, load_config
from ..llm.budget import status as budget_status
from ..models import Job, Status, Verdict
from ..store import JobStore

TOP_MATCHES = 5
OPEN_STATUSES = (Status.NEW, Status.PENDING, Status.RESUME_ONLY)


class BudgetBody(BaseModel):
    monthly_usd: float = Field(ge=0, le=10_000)  # 0 = no budget
    mode: Literal["warn", "stop"] = "warn"


class FollowUpBody(BaseModel):
    action: Literal["done", "snooze"]
    days: int = 3
    job_key: str | None = None      # a job from jobs.json …
    tracker_id: str | None = None   # … or a row in the tracker DB


def _key(j: Job) -> str:
    return j.job_id or j.dedup_key()[1]


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def _tracker_rows(cfg: Config) -> list[dict[str, Any]]:
    try:
        return tracker_db.list_jobs(cfg, hidden="exclude")
    except Exception:  # noqa: BLE001 — a broken tracker must not break Today
        return []


def _row_for(job: Job, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for r in rows:
        if job.job_id and r.get("job_id") == job.job_id:
            return r
    name = (_norm(job.company), _norm(job.title))
    return next((r for r in rows if (_norm(r.get("company")), _norm(r.get("role"))) == name), None)


def _when(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def build_today(cfg: Config, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()
    rows = _tracker_rows(cfg)

    def card(j: Job) -> dict[str, Any]:
        row = _row_for(j, rows)
        steps = checklist(j, row, now)
        reasons, gaps = reasons_and_gaps(j)
        disc = _when(j.discovered_at)
        return {
            "key": _key(j), "company": j.company, "title": j.title,
            "location": j.location, "url": j.url,
            "score": j.fit_score, "label": score_label(j.fit_score),
            "reasons": reasons, "gaps": gaps,
            "steps": steps, "next_step": next_step(steps),
            "is_new": bool(disc and now - disc <= timedelta(days=3)),
            "has_resume": bool(j.resume_path),
        }

    open_jobs = [
        j for j in store.jobs
        if j.status in OPEN_STATUSES and j.fit_score is not None
        and j.fit_verdict in (Verdict.APPLY, Verdict.CONSIDER)
    ]
    open_jobs.sort(key=lambda j: (-(j.fit_score or 0), j.discovered_at), reverse=False)
    matches = [card(j) for j in open_jobs[:TOP_MATCHES]]

    waiting: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    # 1) Follow-ups due — tracker rows first (the tracker is the record of
    #    applications), then jobs marked applied here but not handed off yet.
    for r in rows:
        due = _when(r.get("follow_up_at"))
        if r.get("status") == "applied" and due and due <= now:
            seen.add((_norm(r.get("company")), _norm(r.get("role"))))
            waiting.append({"kind": "follow_up", "company": r["company"], "title": r["role"],
                            "tracker_id": r["id"], "due_at": due.isoformat(),
                            "detail": "Applied a while ago — a short, polite check-in helps."})
    for j in store.jobs:
        if j.status != Status.APPLIED or (_norm(j.company), _norm(j.title)) in seen:
            continue
        if _row_for(j, rows) is not None:
            continue
        step = next((s for s in checklist(j, None, now) if s["id"] == "follow_up"), None)
        if step and step.get("due_now"):
            waiting.append({"kind": "follow_up", "company": j.company, "title": j.title,
                            "job_key": _key(j), "due_at": step["due_at"],
                            "detail": "Applied a while ago — a short, polite check-in helps."})
    # 2) Resumes made but not sent.
    for j in store.jobs:
        if j.resume_path and j.status in OPEN_STATUSES:
            waiting.append({"kind": "resume_not_sent", "company": j.company, "title": j.title,
                            "job_key": _key(j),
                            "detail": "Your tailored resume is ready — apply when you can."})
    # 3) Interviews to prepare for.
    for r in rows:
        if r.get("status") in INTERVIEW_STATUSES:
            waiting.append({"kind": "interview", "company": r["company"], "title": r["role"],
                            "tracker_id": r["id"],
                            "detail": f"Interview stage: {str(r['status']).replace('_', ' ')}."})

    return {
        "matches": matches,
        "waiting": waiting,
        "total_open": len(open_jobs),
        "applied_count": sum(1 for r in rows if r.get("applied_at"))
        + sum(1 for j in store.jobs if j.status == Status.APPLIED and _row_for(j, rows) is None),
    }


def register_today_routes(app: FastAPI) -> None:
    def config_file() -> Path:
        return Path(app.state.config_path or paths.config_path()).resolve()

    @app.get("/api/today")
    async def today() -> dict[str, Any]:
        data = build_today(load_config(config_file()))
        data["budget"] = budget_status()
        return data

    @app.get("/api/budget")
    async def get_budget() -> dict[str, Any]:
        return budget_status()

    @app.put("/api/budget")
    async def put_budget(body: BudgetBody) -> dict[str, Any]:
        from ..onboarding.env_file import set_env_values

        set_env_values({"JOB_PIPELINE_MONTHLY_BUDGET": f"{body.monthly_usd:g}",
                        "JOB_PIPELINE_BUDGET_MODE": body.mode})
        return budget_status()

    @app.post("/api/jobs/{job_key}/opened")
    async def opened(job_key: str) -> dict[str, Any]:
        async with app.state.lock:
            cfg = load_config(config_file())
            store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
            store.load()
            job = next((j for j in store.jobs if _key(j) == job_key or j.job_id == job_key), None)
            if job is None:
                raise HTTPException(404, f"job {job_key!r} not found")
            if not job.apply_opened_at:
                job.apply_opened_at = datetime.now(UTC).isoformat()
                store.save()
            return {"apply_opened_at": job.apply_opened_at, "url": job.url}

    @app.post("/api/follow-ups")
    async def follow_up(body: FollowUpBody) -> dict[str, Any]:
        when = None if body.action == "done" else (
            datetime.now(UTC) + timedelta(days=max(1, min(body.days, 60)))).isoformat()
        cfg = load_config(config_file())
        if body.tracker_id:
            if not tracker_db.set_follow_up(cfg, body.tracker_id, when):
                raise HTTPException(404, "application not found")
            return {"follow_up_at": when}
        if not body.job_key:
            raise HTTPException(400, "job_key or tracker_id is required")
        async with app.state.lock:
            store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
            store.load()
            job = next((j for j in store.jobs if _key(j) == body.job_key), None)
            if job is None:
                raise HTTPException(404, f"job {body.job_key!r} not found")
            job.follow_up_at = when or "done"
            store.save()
        return {"follow_up_at": job.follow_up_at}
