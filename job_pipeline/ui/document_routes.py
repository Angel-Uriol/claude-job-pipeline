"""Finishing an application: cover letters, "what Claude changed", print view.

POST /api/jobs/{key}/cover-letter            write an honest cover letter (.docx)
GET  /api/jobs/{key}/cover-letter/download   download it
GET  /api/jobs/{key}/resume/spec             the rendered resume as JSON (print page)
GET  /api/jobs/{key}/resume/changes          original vs tailored wording
POST /api/jobs/{key}/resume/original         re-render with your original wording
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .. import paths
from ..config import Config, load_config
from ..llm import LLMError, build_llm
from ..models import Job, Status
from ..prompts import render_prompt
from ..resume.content_loader import load_resume_content
from ..resume.cover_letter import generate_cover_letter
from ..resume.generator import generate_resume, sidecar
from ..store import JobStore

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _key(j: Job) -> str:
    return j.job_id or j.dedup_key()[1]


def register_document_routes(app: FastAPI) -> None:
    def config_file() -> Path:
        return Path(app.state.config_path or paths.config_path()).resolve()

    def load(cfg: Config, key: str) -> tuple[JobStore, Job]:
        store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
        store.load()
        job = next((j for j in store.jobs if _key(j) == key or j.job_id == key), None)
        if job is None:
            raise HTTPException(404, f"job {key!r} not found")
        return store, job

    def read_json(p: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @app.post("/api/jobs/{job_key}/cover-letter")
    async def cover_letter(job_key: str) -> dict[str, Any]:
        cfg_path = config_file()
        cfg = load_config(cfg_path)
        _, job = load(cfg, job_key)
        try:
            llm = build_llm()
            result = await asyncio.to_thread(
                generate_cover_letter, job,
                load_resume_content(cfg_path.parent / "resume_content.yaml"),
                cfg, cfg_path.parent, llm=llm,
                template=render_prompt("cover_letter.txt", cfg),
                output_dir=Path(cfg.paths.resumes_dir) / "cover_letters",
            )
        except LLMError as e:
            raise HTTPException(400, str(e)) from e
        async with app.state.lock:
            store, job = load(cfg, job_key)  # reload: the store may have changed meanwhile
            job.cover_letter_path = result.path
            store.save()
        return result.model_dump()

    @app.get("/api/jobs/{job_key}/cover-letter/download")
    async def cover_letter_download(job_key: str) -> FileResponse:
        _, job = load(load_config(config_file()), job_key)
        p = Path(job.cover_letter_path or "")
        if not job.cover_letter_path or not p.exists():
            raise HTTPException(404, "no cover letter for that job yet")
        return FileResponse(p, media_type=DOCX, filename=p.name)

    @app.get("/api/jobs/{job_key}/resume/spec")
    async def resume_spec(job_key: str) -> dict[str, Any]:
        _, job = load(load_config(config_file()), job_key)
        if not job.resume_path:
            raise HTTPException(404, "make the tailored resume first")
        spec = read_json(sidecar(job.resume_path, "spec"))
        if spec is None:
            raise HTTPException(404, "this resume was made before the print view existed — "
                                     "make it again to print it")
        letter = read_json(Path(job.cover_letter_path).with_suffix(".json")) \
            if job.cover_letter_path else None
        return {"resume": spec, "cover_letter": letter, "company": job.company,
                "title": job.title}

    @app.get("/api/jobs/{job_key}/resume/changes")
    async def resume_changes(job_key: str) -> dict[str, Any]:
        _, job = load(load_config(config_file()), job_key)
        if not job.resume_path:
            raise HTTPException(404, "make the tailored resume first")
        changes = read_json(sidecar(job.resume_path, "changes"))
        return {"tailored": changes is not None, "changes": changes}

    @app.post("/api/jobs/{job_key}/resume/original")
    async def resume_original(job_key: str) -> dict[str, Any]:
        cfg_path = config_file()
        cfg = load_config(cfg_path)
        async with app.state.lock:
            store, job = load(cfg, job_key)
            content = load_resume_content(cfg_path.parent / "resume_content.yaml")
            try:
                out = await asyncio.to_thread(generate_resume, job, content,
                                              cfg.paths.resumes_dir, tailorer=None)
            except Exception as e:  # noqa: BLE001 — shown to the user
                raise HTTPException(500, f"resume generation failed: {e}") from e
            job.resume_path = str(out.resolve())
            if job.status in (Status.NEW, Status.PENDING):
                job.status = Status.RESUME_ONLY
            store.save()
        return {"path": job.resume_path}
