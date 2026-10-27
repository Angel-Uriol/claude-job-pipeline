"""FastAPI server for the local web UI.

All endpoints are intentionally tiny — heavy lifting lives in
`runner.py` (pipeline phases) and `resume.generator` (resume rendering).
The server just orchestrates: locks, threading, JSON serialization, SSE.

Bind to 127.0.0.1 only (set in `cli.cmd_serve`). No auth — local tool.
The Next.js frontend at localhost:8766 reaches us via CORS-permitted
fetch; pytest tests use FastAPI's TestClient (no CORS in play).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .. import paths, tracker_db
from ..chrome_queue import ChromeQueue
from ..config import Config, load_config, load_secrets
from ..handoff import run_handoff
from ..llm import LLMError, build_llm, llm_available
from ..models import Job, Status
from ..prompts import render_prompt
from ..resume import build_resume_tailorer, generate_resume, load_resume_content
from ..runner import (
    ProgressEvent,
    run_clean_discover,
    run_deep_fit,
    run_digest,
    run_jsearch,
    run_pre_filter,
    run_resumes_batch,
    run_scrape,
    run_tracker,
)
from ..store import JobStore
from .events import EventBus, PipelineState, event_to_dict
from .setup_routes import register_setup_routes

log = logging.getLogger(__name__)


# Pipeline phases the UI is allowed to trigger. `resumes` is the bulk
# resume-generator phase — kept OFF the `daily` chain so you review
# jobs first, then triggers it manually with a confirm dialog in the UI.
_ALLOWED_PHASES = {
    "scrape",
    "jsearch",
    "pre-filter",
    "deep-fit",
    "digest",
    "tracker",
    "handoff",
    "clean-discover",
    "daily",
    "resumes",
}

# Resolve repo root once. Prompts dir + resume content yaml are all
# relative to this — keeps the server insensitive to cwd.
_REPO_ROOT = paths.REPO_ROOT
_PROMPTS_DIR = paths.PROMPTS_DIR


# --- request/response models -------------------------------------------


class JobPatch(BaseModel):
    """Fields the UI is allowed to modify on a Job."""

    status: str | None = None
    notes: str | None = None
    applied_at: str | None = None
    resume_path: str | None = None
    starred: bool | None = None


class StateResponse(BaseModel):
    counts: dict[str, int]
    last_refreshed: str | None
    pipeline_running: str | None
    last_run_phase: str | None
    last_run_error: str | None


class JobListResponse(BaseModel):
    jobs: list[dict[str, Any]]
    total: int


class RunResponse(BaseModel):
    started: str
    task_id: str  # symbolic — we only run one task at a time


class TaskStatusResponse(BaseModel):
    """Polling response for /api/tasks/{id}.

    Even though only one task runs at a time, the UI can poll this
    endpoint between SSE reconnections to recover state.
    """

    task_id: str
    running: str | None         # current phase if active, else null
    last_run_phase: str | None
    last_run_at: str | None
    last_run_error: str | None
    last_run_result: dict[str, Any]


class ResumeResponse(BaseModel):
    path: str


# --- tracker (Track tab) request bodies --------------------------------


class TrackerStatusBody(BaseModel):
    status: str


class TrackerNotesBody(BaseModel):
    notes: str = ""


class TrackerBulkStatusBody(BaseModel):
    ids: list[str]
    status: str


class ChromeNLBody(BaseModel):
    """Free-text body for POST /api/chrome-claude/parse."""

    text: str = ""


# --- factory ------------------------------------------------------------


def create_app(*, config_path: str | Path | None = None) -> FastAPI:
    """Build the FastAPI app.

    Factored as a function so tests can pass a stub config path. Default is
    the profile's config (`profile/config.yaml`), which the Setup page writes.
    """
    config_path = str(config_path) if config_path is not None else str(paths.config_path())

    state = PipelineState()
    bus = EventBus()
    # Serializes any pipeline run AND any UI mutation that touches
    # jobs.json. Reads don't need it (atomic-write guarantees consistency).
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        bus.bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="Job Pipeline UI", lifespan=lifespan)

    # CORS — the Next.js dev server runs at :8766 and needs to reach us
    # on :8765. Permissive for localhost only since this is a local tool.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8766",
            "http://127.0.0.1:8766",
            # Permissive for "next dev" port variations.
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Expose for testing / introspection.
    app.state.pipeline_state = state
    app.state.event_bus = bus
    app.state.lock = lock
    app.state.config_path = config_path

    # First-run Setup page API (works before a profile exists).
    register_setup_routes(app)

    @app.exception_handler(FileNotFoundError)
    async def _not_configured(_request: Request, exc: FileNotFoundError) -> JSONResponse:
        # No profile yet (or a configured file is missing). A handled response
        # keeps the CORS headers, so the UI sees a clear 409, not a CORS error.
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    # ==================== routes ====================
    # Grouped roughly by spec §4: state, jobs, actions, tasks,
    # companies, config, stream.

    # --- state ---------------------------------------------------------

    @app.get("/api/state")
    async def get_state() -> StateResponse:
        cfg = load_config(config_path)
        store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
        store.load()
        return StateResponse(
            counts=_compute_counts(store.jobs),
            last_refreshed=_jobs_json_mtime(cfg.paths.jobs_json),
            pipeline_running=state.running,
            last_run_phase=state.last_run_phase,
            last_run_error=state.last_run_error,
        )

    # --- jobs ----------------------------------------------------------

    @app.get("/api/jobs")
    async def list_jobs(
        # Filter params, mostly matching spec §4.
        min_score: float | None = None,
        verdict: str | None = None,
        status: str | None = None,
        company: str | None = None,
        source: str | None = None,
        # Spec adds these in §5:
        posted_within: int | None = Query(
            default=None,
            description="Filter to jobs posted within N days (uses discovered_at as fallback).",
        ),
        location: str | None = Query(
            default=None,
            description="Filter to preferred | remote | preferred_or_remote | all.",
        ),
        q: str | None = Query(
            default=None,
            description="Full-text search against title + description + team + company.",
        ),
        starred_only: bool = False,
        page: int = 1,
        page_size: int = 200,
        sort: str = "fit_score:desc",
    ) -> JobListResponse:
        """Filtered + paginated job list. `min_score` is on the 0-100 scale.

        `posted_within=15` filters by `posted_date` (falls back to
        `discovered_at` if absent) — matches spec §5.

        `location` filter values:
          - preferred           → matches the profile's location tokens
          - remote              → matches remote tokens
          - preferred_or_remote → either of the above (default)
          - all                 → no location filter
        """
        cfg = load_config(config_path)
        store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
        store.load()

        # min_score is 0-100 in the URL; internal fit_score is 1-10.
        threshold_internal = (min_score / 10.0) if min_score is not None else None

        # Posted-within cutoff (UTC).
        posted_cutoff: datetime | None = None
        if posted_within is not None and posted_within > 0:
            posted_cutoff = datetime.now(UTC) - timedelta(days=posted_within)

        # Lowercase the search query once.
        q_lower = q.lower().strip() if q else None

        rows: list[dict[str, Any]] = []
        for j in store.jobs:
            if threshold_internal is not None and (
                j.fit_score is None or j.fit_score < threshold_internal
            ):
                continue
            if verdict and (j.fit_verdict is None or j.fit_verdict.value != verdict):
                continue
            if status and j.status.value != status:
                continue
            if company and j.company.lower() != company.lower():
                continue
            if source and j.source.value != source:
                continue
            if starred_only and not j.starred:
                continue
            if posted_cutoff is not None and _job_date(j) < posted_cutoff:
                continue
            if location and not _location_passes(j.location, location, cfg):
                continue
            if q_lower and not _matches_search(j, q_lower):
                continue
            rows.append(_job_to_ui(j, cfg))

        rows = _sort_rows(rows, sort)
        total = len(rows)

        # Pagination — page_size of 200 is a sane default for table use.
        offset = max(0, (page - 1) * page_size)
        rows = rows[offset : offset + page_size]
        return JobListResponse(jobs=rows, total=total)

    @app.get("/api/jobs/{job_key}")
    async def get_job(job_key: str) -> dict[str, Any]:
        cfg = load_config(config_path)
        store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
        store.load()
        job = _find_job(store.jobs, job_key)
        if job is None:
            raise HTTPException(404, f"job {job_key!r} not found")
        return _job_to_ui(job, cfg)

    @app.patch("/api/jobs/{job_key}")
    async def patch_job(job_key: str, patch: JobPatch) -> dict[str, Any]:
        async with lock:
            cfg = load_config(config_path)
            store = JobStore(
                cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies
            )
            store.load()
            job = _find_job(store.jobs, job_key)
            if job is None:
                raise HTTPException(404, f"job {job_key!r} not found")

            if patch.status is not None:
                try:
                    job.status = Status(patch.status)
                except ValueError as e:
                    raise HTTPException(400, f"bad status: {patch.status}") from e
            if patch.notes is not None:
                job.notes = patch.notes
            if patch.applied_at is not None:
                job.applied_at = patch.applied_at
            if patch.resume_path is not None:
                job.resume_path = patch.resume_path
            if patch.starred is not None:
                job.starred = patch.starred

            store.save()
            return _job_to_ui(job, cfg)

    @app.post("/api/jobs/{job_key}/apply")
    async def mark_applied(job_key: str) -> dict[str, Any]:
        async with lock:
            cfg = load_config(config_path)
            store = JobStore(
                cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies
            )
            store.load()
            job = _find_job(store.jobs, job_key)
            if job is None:
                raise HTTPException(404, f"job {job_key!r} not found")
            job.status = Status.APPLIED
            job.applied_at = datetime.now(UTC).isoformat()
            store.save()
            return _job_to_ui(job, cfg)

    @app.post("/api/jobs/{job_key}/dismiss")
    async def dismiss_job(job_key: str) -> dict[str, Any]:
        """Soft-discard: mark the job dismissed (not interested). Kept in the
        store so a re-scrape won't resurface it — `status` survives upsert."""
        async with lock:
            cfg = load_config(config_path)
            store = JobStore(
                cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies
            )
            store.load()
            job = _find_job(store.jobs, job_key)
            if job is None:
                raise HTTPException(404, f"job {job_key!r} not found")
            job.status = Status.DISMISSED
            store.save()
            return _job_to_ui(job, cfg)

    @app.delete("/api/jobs/{job_key}")
    async def delete_job(job_key: str) -> dict[str, Any]:
        """Hard-delete: remove the job from the store entirely.

        Note: a still-live posting can be re-discovered on the next scrape.
        For that reason `dismiss` is the preferred everyday action.
        """
        async with lock:
            cfg = load_config(config_path)
            store = JobStore(
                cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies
            )
            store.load()
            if _find_job(store.jobs, job_key) is None:
                raise HTTPException(404, f"job {job_key!r} not found")
            removed = store.remove_jobs(lambda j: (j.job_id or j.dedup_key()[1]) == job_key)
            store.save()
            return {"deleted": removed}

    @app.post("/api/jobs/{job_key}/resume")
    async def generate_job_resume(
        job_key: str, family: str | None = None
    ) -> ResumeResponse:
        async with lock:
            cfg = load_config(config_path)
            store = JobStore(
                cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies
            )
            store.load()
            job = _find_job(store.jobs, job_key)
            if job is None:
                raise HTTPException(404, f"job {job_key!r} not found")

            content = load_resume_content(paths.resume_content_path())
            family_obj = None
            if family:
                from ..resume.families import RESUME_FAMILIES

                if family not in RESUME_FAMILIES:
                    raise HTTPException(400, f"unknown family {family!r}")
                family_obj = RESUME_FAMILIES[family]

            tailorer = _resume_tailorer_for(cfg)
            try:
                out_path = await asyncio.to_thread(
                    generate_resume,
                    job,
                    content,
                    cfg.paths.resumes_dir,
                    family=family_obj,
                    tailorer=tailorer,
                )
            except Exception as e:
                raise HTTPException(500, f"resume generation failed: {e}") from e

            job.resume_path = str(out_path.resolve())
            if job.status in (Status.NEW, Status.PENDING):
                job.status = Status.RESUME_ONLY
            store.save()
            return ResumeResponse(path=str(out_path.resolve()))

    @app.get("/api/jobs/{job_key}/resume/download")
    async def download_resume(job_key: str) -> FileResponse:
        cfg = load_config(config_path)
        store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
        store.load()
        job = _find_job(store.jobs, job_key)
        if job is None or not job.resume_path:
            raise HTTPException(404, "no resume on file for that job")
        p = Path(job.resume_path)
        if not p.exists():
            raise HTTPException(
                404, f"resume_path on job points at missing file: {p}"
            )
        return FileResponse(
            p,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=p.name,
        )

    # --- actions / tasks ----------------------------------------------

    @app.post("/api/run/{phase}")
    @app.post("/api/actions/{phase}")
    async def run_phase(
        phase: str,
        verdict: Annotated[
            list[str] | None,
            Query(
                description="(resumes phase only) Which fit_verdict values to include. "
                "Repeat the param: ?verdict=apply&verdict=consider. "
                "Defaults to apply-only for backwards compat.",
            ),
        ] = None,
        min_score: Annotated[
            float | None,
            Query(
                ge=0.0,
                le=100.0,
                description="(resumes phase only) Minimum fit_score on the 0-100 UI scale. "
                "Internally divided by 10 to match Job.fit_score's 0-10 scale.",
            ),
        ] = None,
        jobs: Annotated[
            list[str] | None,
            Query(
                description="(resumes phase only) Explicit list of job keys to render. "
                "Repeat the param: ?jobs=abc&jobs=def. When present, "
                "this overrides verdict + min_score and bypasses the "
                "max-per-run cap — the caller has explicitly chosen the set.",
            ),
        ] = None,
    ) -> RunResponse:
        """Kick off a pipeline phase. Returns immediately; progress streams
        via /api/stream (SSE). 409 if a phase is already running.

        For `phase=resumes`, three optional query params let the UI
        choose what to render:

          - `jobs` (list of keys) → explicit selection. Highest priority.
          - `verdict` + `min_score` → filter-based selection. Used when
            `jobs` is absent.
          - Neither → apply-only fallback (historical default).
        """
        if phase not in _ALLOWED_PHASES:
            raise HTTPException(400, f"unknown phase {phase!r}")
        if state.running is not None:
            raise HTTPException(409, f"pipeline busy with: {state.running}")

        # Build the resume filter only when relevant — ignored for all
        # other phases.
        resume_filter: ResumeFilter | None = None
        if phase == "resumes" and (jobs or verdict or min_score is not None):
            resume_filter = ResumeFilter(
                verdicts=verdict,
                min_score_internal=(min_score / 10.0) if min_score is not None else None,
                job_keys=jobs,
            )

        asyncio.create_task(
            _run_pipeline_task(phase, state, bus, lock, config_path, resume_filter)
        )
        # `current` is the only task id we ever return — there's only
        # one pipeline at a time and we want polling to be trivial.
        return RunResponse(started=phase, task_id="current")

    @app.get("/api/tasks/{task_id}")
    async def task_status(task_id: str) -> TaskStatusResponse:
        # We only support `current`. Other task_ids 404 so clients don't
        # silently get stale state.
        if task_id != "current":
            raise HTTPException(404, "unknown task_id (only 'current' is supported)")
        return TaskStatusResponse(
            task_id="current",
            running=state.running,
            last_run_phase=state.last_run_phase,
            last_run_at=state.last_run_at,
            last_run_error=state.last_run_error,
            last_run_result=state.last_run_result,
        )

    # --- bulk resume preview ------------------------------------------

    @app.get("/api/resumes/preview")
    async def preview_resumes_batch(
        verdict: Annotated[
            list[str] | None,
            Query(
                description="Which fit_verdict values to include. Repeat the param: "
                "?verdict=apply&verdict=consider. Defaults to apply-only.",
            ),
        ] = None,
        min_score: Annotated[
            float | None,
            Query(
                ge=0.0,
                le=100.0,
                description="Minimum fit_score on the 0-100 UI scale (divided by 10 internally).",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Return the FULL list of jobs eligible for resume generation,
        plus metadata. The confirm dialog renders this list with per-job
        checkboxes so the user can pick exactly which ones to generate.

        Returned `jobs` is sorted by `fit_score` descending (highest
        first) so the top picks float to the top of the dialog.

        The optional `verdict` + `min_score` params let the UI mirror
        its on-screen verdict filter so the dialog shows the right set.
        """
        from ..models import Verdict

        cfg = load_config(config_path)
        store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
        store.load()

        # Normalize verdict filter. None or empty → apply-only.
        allowed: set[Verdict] = set()
        if verdict:
            for v in verdict:
                try:
                    allowed.add(Verdict(v.lower()))
                except ValueError:
                    continue
        if not allowed:
            allowed = {Verdict.APPLY}

        # min_score arrives 0-100; internal fit_score is 0-10.
        threshold_internal: float | None = (
            (min_score / 10.0) if min_score is not None else None
        )

        def _is_eligible(j: Job) -> bool:
            if j.fit_verdict not in allowed:
                return False
            if j.resume_path:
                return False
            if threshold_internal is not None and (j.fit_score or 0.0) < threshold_internal:
                return False
            return True

        eligible_jobs = [j for j in store.jobs if _is_eligible(j)]
        # Highest-scoring first so the user sees top picks at the top.
        eligible_jobs.sort(key=lambda j: j.fit_score or 0.0, reverse=True)
        eligible = [
            {
                "key": j.job_id or j.dedup_key()[1],
                "company": j.company,
                "title": j.title,
                "score": int(round((j.fit_score or 0.0) * 10)),  # 0-100 UI scale
            }
            for j in eligible_jobs
        ]
        return {
            "count": len(eligible),
            # Show up to 100 — local-tool territory; the dialog scrolls.
            # We need the FULL list for selection, not just a preview.
            "jobs": eligible[:100],
            "max_per_run": 10,
            "verdicts": sorted(v.value for v in allowed),
            "min_score": min_score,  # echoed back on 0-100 scale for the UI
        }

    # --- tracker tab (reads/writes the built-in tracker DB) ---------
    #
    # The Track tab is a view over the SQLite tracker DB (tracker_db.py).
    # Resume *generation* is NOT here — Track only downloads the
    # tailored resume carried over by the handoff.

    @app.get("/api/tracker/jobs")
    async def tracker_jobs(
        search: Annotated[str, Query()] = "",
        location: Annotated[str, Query()] = "all",
        verdict: Annotated[str, Query()] = "all",
        status: Annotated[str, Query()] = "all",
        company: Annotated[str, Query()] = "all",
        sort: Annotated[str, Query()] = "score",
        hidden: Annotated[str, Query()] = "exclude",
    ) -> dict[str, Any]:
        cfg = load_config(config_path)
        ready = cfg.tracker.enabled
        jobs = (
            tracker_db.list_jobs(
                cfg,
                search=search,
                location=location,
                verdict=verdict,
                status=status,
                company=company,
                sort=sort,
                hidden=hidden,
            )
            if ready
            else []
        )
        return {"jobs": jobs, "tracker_ready": ready}

    @app.get("/api/tracker/jobs.csv")
    async def tracker_jobs_csv(
        search: Annotated[str, Query()] = "",
        location: Annotated[str, Query()] = "all",
        verdict: Annotated[str, Query()] = "all",
        status: Annotated[str, Query()] = "all",
        company: Annotated[str, Query()] = "all",
        sort: Annotated[str, Query()] = "score",
        hidden: Annotated[str, Query()] = "exclude",
    ) -> Response:
        """Export the currently-filtered tracker jobs as CSV."""
        cfg = load_config(config_path)
        jobs = (
            tracker_db.list_jobs(
                cfg,
                search=search,
                location=location,
                verdict=verdict,
                status=status,
                company=company,
                sort=sort,
                hidden=hidden,
            )
            if (cfg.tracker.enabled)
            else []
        )
        cols = [
            "id", "company", "role", "role_tier", "verdict", "status",
            "fit_score", "location", "location_pass", "h1b", "salary",
            "posted", "applied_at", "url", "notes",
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for j in jobs:
            writer.writerow({c: ("" if j.get(c) is None else j.get(c)) for c in cols})
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=tracker.csv"},
        )

    @app.get("/api/tracker/stats")
    async def tracker_stats() -> dict[str, Any]:
        cfg = load_config(config_path)
        ready = cfg.tracker.enabled
        out = tracker_db.stats(cfg)  # returns zeros if the DB is missing
        out["tracker_ready"] = ready
        return out

    @app.post("/api/tracker/jobs/{job_id}/status")
    async def tracker_set_status(job_id: str, body: TrackerStatusBody) -> dict[str, Any]:
        cfg = load_config(config_path)
        async with lock:
            try:
                ok = tracker_db.set_status(cfg, job_id, body.status)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        if not ok:
            raise HTTPException(404, f"tracker job {job_id!r} not found")
        return {"ok": True, "status": body.status}

    @app.post("/api/tracker/jobs/{job_id}/star")
    async def tracker_toggle_star(job_id: str) -> dict[str, Any]:
        cfg = load_config(config_path)
        async with lock:
            starred = tracker_db.toggle_star(cfg, job_id)
        if starred is None:
            raise HTTPException(404, f"tracker job {job_id!r} not found")
        return {"ok": True, "starred": starred}

    @app.post("/api/tracker/jobs/{job_id}/hide")
    async def tracker_toggle_hide(job_id: str) -> dict[str, Any]:
        cfg = load_config(config_path)
        async with lock:
            hidden_val = tracker_db.toggle_hide(cfg, job_id)
        if hidden_val is None:
            raise HTTPException(404, f"tracker job {job_id!r} not found")
        return {"ok": True, "hidden": hidden_val}

    @app.post("/api/tracker/jobs/{job_id}/notes")
    async def tracker_set_notes(job_id: str, body: TrackerNotesBody) -> dict[str, Any]:
        cfg = load_config(config_path)
        async with lock:
            ok = tracker_db.set_notes(cfg, job_id, body.notes)
        if not ok:
            raise HTTPException(404, f"tracker job {job_id!r} not found")
        return {"ok": True}

    @app.delete("/api/tracker/jobs/{job_id}")
    async def tracker_delete(job_id: str) -> dict[str, Any]:
        cfg = load_config(config_path)
        async with lock:
            ok = tracker_db.delete(cfg, job_id)
        if not ok:
            raise HTTPException(404, f"tracker job {job_id!r} not found")
        return {"ok": True}

    @app.post("/api/tracker/bulk/status")
    async def tracker_bulk_status(body: TrackerBulkStatusBody) -> dict[str, Any]:
        cfg = load_config(config_path)
        async with lock:
            try:
                n = tracker_db.bulk_set_status(cfg, body.ids, body.status)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        return {"updated": n}

    @app.get("/api/tracker/jobs/{job_id}/resume/download")
    async def tracker_resume_download(job_id: str) -> FileResponse:
        cfg = load_config(config_path)
        path_str = tracker_db.latest_resume_path(cfg, job_id)
        if not path_str:
            raise HTTPException(404, "no resume for this job")
        path = Path(path_str).resolve()
        # Path guard: only serve files under the tracker's resume output dir
        # (mirrors the Flask app's traversal protection).
        allowed_root = tracker_db.resume_output_dir(cfg).resolve()
        if allowed_root not in path.parents or not path.exists():
            raise HTTPException(404, "resume file not found")
        return FileResponse(
            str(path),
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            filename=path.name,
        )

    # --- companies -----------------------------------------------------

    @app.get("/api/companies")
    async def list_companies() -> dict[str, Any]:
        """Read-only company roster from config.yaml.

        Returns three buckets — `greenhouse`, `lever`, `no_api` —
        plus the `excluded` and `dead` lists. The Companies page in the
        UI is read-only for v0; full CRUD comes in a 9b pass.
        """
        cfg = load_config(config_path)
        return {
            "greenhouse": [c.model_dump() for c in cfg.scrapers.greenhouse],
            "lever": [c.model_dump() for c in cfg.scrapers.lever],
            "no_api": [c.model_dump() for c in cfg.scrapers.no_api],
            "excluded": cfg.excluded_companies,
            "dead": cfg.dead_companies,
        }

    # --- config --------------------------------------------------------

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        """Read-only config dump for the Settings page. PATCH comes later."""
        cfg = load_config(config_path)
        return cfg.model_dump()

    # --- chrome-claude (browser search) -------------------------------
    # Additive feature. The UI button can't drive the browser itself —
    # only a Claude Code agent session holds the Claude-in-Chrome
    # connection — so these endpoints just ENQUEUE a run and report queue
    # status. A Claude Code "watcher loop" drains data/chrome_runs/pending/
    # per prompts/chrome_claude_runbook.md. Nothing here touches the
    # existing run/_dispatch pipeline machinery.

    @app.get("/api/chrome-claude/preview")
    async def chrome_claude_preview() -> dict[str, Any]:
        """Targets the Run Chrome Claude modal offers.

        Splits config.scrapers.no_api into `selected` (real companies with
        a careers page) and `new` (the linkedin_search_* discovery entries),
        and lists the posting-date windows the modal can pick.
        """
        cfg = load_config(config_path)
        selected = [
            {"name": e.name, "careers_url": e.careers_url, "platform": e.platform}
            for e in cfg.scrapers.no_api
            if e.platform != "linkedin_search"
        ]
        new = [
            {"name": e.name, "careers_url": e.careers_url, "platform": e.platform}
            for e in cfg.scrapers.no_api
            if e.platform == "linkedin_search"
        ]
        return {
            "selected": selected,
            "new": new,
            "posted_within_options": [
                {"label": "Any time", "value": None},
                {"label": "Past 24 hours", "value": 1},
                {"label": "Past 3 days", "value": 3},
                {"label": "Past week", "value": 7},
                {"label": "Past month", "value": 30},
            ],
            "batch_size": cfg.chrome_claude.batch_size,
            "max_parallel_batches": cfg.chrome_claude.max_parallel_batches,
        }

    @app.post("/api/chrome-claude/run")
    async def chrome_claude_run(
        scope: Annotated[str, Query(description="selected | new | mix")] = "mix",
        company: Annotated[
            list[str] | None,
            Query(
                description="Repeat the param: ?company=snowflake&company=nvidia. "
                "Only used when scope includes 'selected'; empty = all selected companies.",
            ),
        ] = None,
        posted_within: Annotated[
            int | None,
            Query(
                ge=0,
                le=3650,
                description="Only collect roles posted within N days; omit or 0 = any age.",
            ),
        ] = None,
        score_after: Annotated[
            bool,
            Query(description="Run pre-filter + deep-fit after import (costs API tokens)."),
        ] = False,
        ad_hoc_company: Annotated[
            list[str] | None,
            Query(
                description="Companies NOT in config to also search — the watcher "
                "finds each one's careers page. Repeat: ?ad_hoc_company=figma.",
            ),
        ] = None,
        extra_notes: Annotated[
            str,
            Query(description="Free-text extra criteria injected into this run's prompt."),
        ] = "",
    ) -> dict[str, Any]:
        """Queue a Chrome-Claude browser-search run; return its record.

        Does NOT drive the browser — writes a request to
        data/chrome_runs/pending/ that the watcher loop picks up.
        """
        if scope not in ("selected", "new", "mix"):
            raise HTTPException(400, f"unknown scope {scope!r}")
        try:
            record = ChromeQueue().enqueue(
                scope=scope,
                companies=company or [],
                posted_within=posted_within,
                score_after=score_after,
                ad_hoc_companies=ad_hoc_company or [],
                extra_notes=extra_notes,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return record

    @app.get("/api/chrome-claude/status")
    async def chrome_claude_status() -> dict[str, Any]:
        """Queue state for the UI to poll (newest first)."""
        return {"requests": ChromeQueue().list_status()}

    @app.post("/api/chrome-claude/parse")
    async def chrome_claude_parse(body: ChromeNLBody) -> dict[str, Any]:
        """Parse a free-text request into a PROPOSED Chrome-Claude run via
        the fast Claude model (the modal lets the user confirm before queueing).

        Returns: scope, companies (matched to tracked no_api), ad_hoc_companies
        (named but untracked), posted_within, score_after, extra_notes.
        """
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(400, "empty request text")
        cfg = load_config(config_path)
        from ..chrome_nl import ClaudeChromeNLClient, parse_request

        try:
            llm = build_llm()
        except LLMError as e:
            raise HTTPException(503, str(e)) from e
        client = ClaudeChromeNLClient(llm=llm, model=cfg.llm.fast_model)
        try:
            spec = await asyncio.to_thread(parse_request, text, cfg=cfg, client=client)
        except Exception as e:  # network/parse/etc. — surface as 502
            log.exception("chrome-claude parse failed")
            raise HTTPException(502, f"parse failed: {e}") from e
        return spec

    # --- SSE stream ----------------------------------------------------

    async def _events_stream(request: Request) -> EventSourceResponse:
        """Pipeline progress event stream. Shared implementation for both
        /api/events (Phase 7 alias) and /api/stream (spec name)."""
        queue = await bus.subscribe()

        async def gen() -> AsyncIterator[dict[str, str]]:
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        # Heartbeat so proxies/browsers don't close the idle stream.
                        yield {"event": "ping", "data": "{}"}
                        continue
                    yield {
                        "event": "progress",
                        "data": json.dumps(event_to_dict(ev)),
                    }
            finally:
                await bus.unsubscribe(queue)

        return EventSourceResponse(gen())

    @app.get("/api/events")
    async def events(request: Request) -> EventSourceResponse:
        return await _events_stream(request)

    @app.get("/api/stream")
    async def stream(request: Request) -> EventSourceResponse:
        return await _events_stream(request)

    return app


# ==================== background task ====================


class ResumeFilter(BaseModel):
    """Per-run override of the bulk-resume eligibility filter.

    Threaded through `_run_pipeline_task` → `_dispatch` so the UI can
    drive what the resumes phase actually renders.

    Two modes (mirroring `run_resumes_batch`):
      - `job_keys` non-empty → render exactly those jobs (explicit
        selection from the confirm dialog's checkbox list).
      - Otherwise → use `verdicts` + `min_score_internal` as a filter.
    """

    verdicts: list[str] | None = None
    min_score_internal: float | None = None  # 0-10 scale (already divided)
    job_keys: list[str] | None = None


async def _run_pipeline_task(
    phase: str,
    state: PipelineState,
    bus: EventBus,
    lock: asyncio.Lock,
    config_path: str,
    resume_filter: ResumeFilter | None = None,
) -> None:
    """Background task: hold the lock for the whole run, emit events,
    update PipelineState when done."""
    async with lock:
        state.start(phase)
        bus.publish_threadsafe(
            ProgressEvent(phase=phase, message=f"Starting {phase}…")
        )

        try:
            cfg = load_config(config_path)
            store = JobStore(
                cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies
            )
            store.load()
            result = await asyncio.to_thread(
                _dispatch, phase, cfg, store, bus, resume_filter
            )
            state.finish(phase, result)
            bus.publish_threadsafe(
                ProgressEvent(
                    phase="done",
                    message=f"{phase} complete",
                    extra={"phase": phase, "result": result},
                )
            )
        except Exception as e:
            log.exception("Pipeline %s failed", phase)
            state.fail(phase, str(e))
            bus.publish_threadsafe(
                ProgressEvent(
                    phase="error",
                    message=f"{phase} failed: {e}",
                    extra={"phase": phase, "error": str(e)},
                )
            )


def _resume_tailorer_for(cfg: Config) -> Any:
    """Build the Claude resume tailorer, or None.

    None → resumes render from the deterministic template (no LLM).
    """
    return build_resume_tailorer(
        enabled=cfg.resume.tailor_enabled,
        model=cfg.resume.model,
        prompt_template=render_prompt("resume_tailor.txt", cfg),
    )


def _dispatch(
    phase: str,
    cfg: Config,
    store: JobStore,
    bus: EventBus,
    resume_filter: ResumeFilter | None = None,
) -> dict[str, Any]:
    """Pick the right run_* function for the phase and call it.

    `resume_filter` is only used when `phase == "resumes"` — it lets the
    UI override the default apply-only target set with its on-screen
    verdict + min_score filter.
    """

    def progress(event: ProgressEvent) -> None:
        bus.publish_threadsafe(event)

    if phase == "scrape":
        out = run_scrape(cfg, store, progress=progress)
        store.save()
        return out

    if phase == "jsearch":
        secrets = load_secrets()
        out = run_jsearch(cfg, store, secrets=secrets, progress=progress)
        store.save()
        return out

    if phase == "pre-filter":
        secrets = load_secrets()
        if not llm_available(secrets):
            raise RuntimeError("Claude is not connected — open the Setup page.")
        out = run_pre_filter(
            cfg,
            secrets,
            store,
            progress=progress,
            prompt_path=_PROMPTS_DIR / "pre_filter.txt",
        )
        store.save()
        return out

    if phase == "deep-fit":
        secrets = load_secrets()
        if not llm_available(secrets):
            raise RuntimeError("Claude is not connected — open the Setup page.")
        out = run_deep_fit(
            cfg,
            secrets,
            store,
            progress=progress,
            prompt_path=_PROMPTS_DIR / "deep_fit.txt",
        )
        store.save()
        return out

    if phase == "digest":
        out_path = run_digest(
            cfg,
            store,
            template_path=_PROMPTS_DIR / "digest.html.j2",
            chrome_template_path=_PROMPTS_DIR / "chrome_claude_template.txt",
        )
        return {"output": str(out_path)}

    if phase == "tracker":
        out_path = run_tracker(cfg, store)
        return {"output": str(out_path)}

    if phase == "handoff":
        out = run_handoff(cfg, store, progress=progress)
        # No store.save() — handoff only reads jobs + writes to the tracker DB.
        return out

    if phase == "clean-discover":
        out = run_clean_discover(cfg, store, progress=progress)
        store.save()
        return out

    if phase == "resumes":
        verdicts_arg = resume_filter.verdicts if resume_filter else None
        min_score_arg = resume_filter.min_score_internal if resume_filter else None
        job_keys_arg = resume_filter.job_keys if resume_filter else None
        out = run_resumes_batch(
            cfg,
            store,
            content_path=paths.resume_content_path(),
            progress=progress,
            verdicts=verdicts_arg,
            min_score=min_score_arg,
            job_keys=job_keys_arg,
            tailorer=_resume_tailorer_for(cfg),
        )
        store.save()
        return out

    if phase == "daily":
        scrape_counts = run_scrape(cfg, store, progress=progress)
        store.save()

        secrets = load_secrets()
        connected = llm_available(secrets)
        pf_counts: dict[str, int] = {}
        if connected:
            pf_counts = run_pre_filter(
                cfg,
                secrets,
                store,
                progress=progress,
                prompt_path=_PROMPTS_DIR / "pre_filter.txt",
            )
            store.save()
        else:
            progress(
                ProgressEvent(
                    phase="pre-filter",
                    message="Claude not connected — skipping pre-filter.",
                )
            )

        df_counts: dict[str, int] = {}
        if connected:
            df_counts = run_deep_fit(
                cfg,
                secrets,
                store,
                progress=progress,
                prompt_path=_PROMPTS_DIR / "deep_fit.txt",
            )
            store.save()
        else:
            progress(
                ProgressEvent(
                    phase="deep-fit",
                    message="Claude not connected — skipping deep-fit.",
                )
            )

        digest_path = run_digest(
            cfg,
            store,
            template_path=_PROMPTS_DIR / "digest.html.j2",
            chrome_template_path=_PROMPTS_DIR / "chrome_claude_template.txt",
        )
        tracker_path = run_tracker(cfg, store)
        return {
            "scrape": scrape_counts,
            "pre_filter": pf_counts,
            "deep_fit": df_counts,
            "digest": str(digest_path),
            "tracker": str(tracker_path),
        }

    raise ValueError(f"unknown phase {phase!r}")


# ==================== helpers ====================


def _compute_counts(jobs: list[Job]) -> dict[str, int]:
    """Top-line stats shown in the UI header."""
    counts = {
        "total": len(jobs),
        "apply": 0,
        "consider": 0,
        "skipped": 0,
        "applied": 0,
        "starred": 0,
    }
    for j in jobs:
        if j.starred:
            counts["starred"] += 1
        if j.status == Status.APPLIED:
            counts["applied"] += 1
            continue
        if j.status == Status.SKIPPED:
            counts["skipped"] += 1
            continue
        if j.fit_verdict is not None:
            counts[j.fit_verdict.value] = counts.get(j.fit_verdict.value, 0) + 1
    return counts


def _job_to_ui(j: Job, cfg: Config | None = None) -> dict[str, Any]:
    """Serialize a Job for the frontend. Adds `fit_score_pct` (0-100), a
    stable `key` the UI can pass back in URLs, and `location_kind`
    (preferred | remote | other | unknown) from the profile's token lists."""
    d = j.model_dump(mode="json")
    d["key"] = j.job_id or j.dedup_key()[1]
    if j.fit_score is not None:
        d["fit_score_pct"] = int(round(j.fit_score * 10))
    else:
        d["fit_score_pct"] = None
    # Compute days_since_posted for the UI's color-coded relative date.
    d["days_since_posted"] = _days_since_posted(j)
    d["location_kind"] = _location_kind(j.location, cfg) if cfg else "unknown"
    return d


def _location_kind(location: str, cfg: Config) -> str:
    if not location:
        return "unknown"
    s = location.lower()
    if any(t.lower() in s for t in cfg.filters.location_tokens):
        return "preferred"
    if any(t.lower() in s for t in cfg.filters.remote_tokens):
        return "remote"
    return "other"


def _find_job(jobs: list[Job], key: str) -> Job | None:
    """Look up by job_id OR dedup-key fallback."""
    for j in jobs:
        if j.job_id == key:
            return j
        if j.dedup_key()[1] == key:
            return j
    return None


def _jobs_json_mtime(path: str) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    return datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).isoformat()


# --- date helpers ------------------------------------------------------


def _parse_iso(s: str | None) -> datetime | None:
    """Best-effort ISO 8601 parse. Returns None on failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.rstrip("Z").replace("Z", ""))
    except (ValueError, TypeError):
        return None


def _job_date(j: Job) -> datetime:
    """`posted_date` if set, else `discovered_at` — per spec §5.

    Returned datetime is always tz-aware (we coerce naive → UTC).
    """
    candidate = _parse_iso(j.posted_date) or _parse_iso(j.discovered_at)
    if candidate is None:
        # Worst case: pretend it's epoch so the job sorts as ancient.
        return datetime.fromtimestamp(0, tz=UTC)
    if candidate.tzinfo is None:
        return candidate.replace(tzinfo=UTC)
    return candidate


def _days_since_posted(j: Job) -> int | None:
    """Number of days since the job's posted_date (or discovered_at)."""
    when = _job_date(j)
    if when.year == 1970:
        return None
    return (datetime.now(UTC) - when).days


# --- filter helpers ----------------------------------------------------


def _location_passes(location: str, mode: str, cfg: Config) -> bool:
    """UI location filter: preferred | remote | preferred_or_remote | all.

    Uses the same token lists as the scrape-time filter (profile config),
    but is independent of it — flip to "all" to see everything stored.
    """
    if mode == "all":
        return True
    if not location:
        return False
    s = location.lower()

    in_pref = any(t.lower() in s for t in cfg.filters.location_tokens)
    in_remote = any(t.lower() in s for t in cfg.filters.remote_tokens)

    if mode == "preferred":
        return in_pref
    if mode == "remote":
        return in_remote
    if mode == "preferred_or_remote":
        return in_pref or in_remote
    return True  # unknown mode → don't filter


def _matches_search(j: Job, q_lower: str) -> bool:
    """Full-text search across title + description + team + company."""
    haystack = " ".join(
        s.lower() for s in (j.title, j.description, j.team or "", j.company)
    )
    return q_lower in haystack


def _sort_rows(rows: list[dict[str, Any]], spec: str) -> list[dict[str, Any]]:
    """Sort `rows` per spec §4's `<column>:<asc|desc>` convention.

    Supported columns: fit_score, company, title, posted (days_since_posted),
    status. Unknown column → fit_score desc.
    Stable tiebreaker: company name (so equal scores group readably).
    """
    parts = spec.split(":", 1)
    col = parts[0].lower() if parts else "fit_score"
    direction = (parts[1] if len(parts) > 1 else "desc").lower()
    reverse = direction != "asc"

    def key(row: dict[str, Any]) -> tuple[Any, Any]:
        company = (row.get("company") or "").lower()
        if col == "company":
            return (company, row.get("fit_score_pct") or 0)
        if col == "title":
            return ((row.get("title") or "").lower(), company)
        if col == "posted":
            # 999999 puts None at the end regardless of direction.
            return (row.get("days_since_posted") or 999999, company)
        if col == "status":
            return ((row.get("status") or ""), company)
        # Default: fit_score.
        return (row.get("fit_score_pct") or 0, company)

    return sorted(rows, key=key, reverse=reverse)
