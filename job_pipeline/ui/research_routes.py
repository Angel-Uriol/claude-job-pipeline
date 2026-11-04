"""Start, persist and poll research without holding an HTTP request open."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from .. import paths
from ..config import load_config
from ..llm import LLMError, build_llm
from ..research.service import (
    Kind,
    ResearchRequest,
    build_report,
    candidates,
    list_reports,
    save_report,
)


def register_research_routes(app: FastAPI) -> None:
    tasks: set[asyncio.Task[None]] = set()
    active: set[str] = set()

    def config_file() -> Path:
        return Path(app.state.config_path or paths.config_path()).resolve()

    @app.get("/api/research/options")
    async def options(kind: Kind) -> dict[str, Any]:
        jobs = candidates(load_config(config_file()), kind)
        return {"jobs": [{k: j[k] for k in ("job_key", "company", "title", "fit_score")}
                         for j in jobs]}

    @app.get("/api/research")
    async def reports() -> dict[str, Any]:
        folder = config_file().parent
        rows = list_reports(folder)
        for row in rows:
            if row.get("status") == "running" and row.get("id") not in active:
                # Left over from a stopped server — persist, so it isn't "running" forever.
                row.update(status="failed", progress="Interrupted by a server restart. Run again.")
                try:
                    save_report(folder, row)
                except (OSError, ValueError, KeyError):
                    pass
        return {"reports": rows}

    @app.post("/api/research", status_code=202)
    async def start(body: ResearchRequest) -> dict[str, Any]:
        if app.state.pipeline_state.running or app.state.lock.locked():
            raise HTTPException(409, "A run is already in progress. Wait for it to finish.")
        if len(set(body.job_keys)) != len(body.job_keys):
            raise HTTPException(422, "Select each job only once.")
        if body.kind == "interview" and len(body.job_keys) != 1:
            raise HTTPException(422, "Choose one application for interview prep.")
        file = config_file()
        cfg = load_config(file)
        available = {j["job_key"]: j for j in candidates(cfg, body.kind)}
        if any(k not in available for k in body.job_keys):
            raise HTTPException(404, "A selected job is no longer eligible in this profile.")
        jobs = [available[k] for k in body.job_keys]
        # Read resume/profile inside the pinned directory, never the active global later.
        record: dict[str, Any] = dict(
            id=str(uuid.uuid4()), kind=body.kind, status="running", progress="Queued",
            created_at=datetime.now(UTC).isoformat(), request=body.model_dump(), jobs=jobs,
        )
        save_report(file.parent, record)
        active.add(record["id"])
        app.state.pipeline_state.running = "research"

        async def run() -> None:
            try:
                async with app.state.lock:
                    def progress(message: str) -> None:
                        record["progress"] = message
                        save_report(file.parent, record)

                    def work() -> dict[str, Any]:
                        return build_report(cfg, file.parent, body, jobs, build_llm(),
                                            progress=progress)

                    result = await asyncio.to_thread(work)
                    record.update(result, status="complete", progress="Complete")
            except LLMError as e:
                # LLMError messages are written for users and carry no secrets.
                record.update(status="failed", progress=f"{e} Previous reports are still here.")
            except Exception:  # noqa: BLE001 - no raw provider errors / secrets in saved reports
                record.update(status="failed", progress=(
                    "Research could not finish. Check your Claude connection and retry; "
                    "previous reports are still available."
                ))
            finally:
                try:
                    save_report(file.parent, record)
                finally:
                    active.discard(record["id"])
                    app.state.pipeline_state.running = None

        task = asyncio.create_task(run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return record
