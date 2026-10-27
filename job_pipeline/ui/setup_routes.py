"""HTTP routes for the Setup page (connect Claude → resume → interview →
companies → review & save).

Kept out of server.py on purpose: it's a separate flow with its own state
(`profile/.interview.json`) and it must work BEFORE a profile exists, while
every server.py route assumes one. All heavy work (Claude calls, board
probes) runs in a worker thread so the event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, ValidationError

from .. import paths
from ..config import LLMSettings, load_config, load_secrets
from ..llm import ClaudeClient, LLMError, build_llm, llm_available, self_check
from ..llm.cli_client import describe_cli
from ..onboarding.companies import suggest_companies, verify_companies
from ..onboarding.env_file import mask, set_env_values
from ..onboarding.interview import (
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    InterviewState,
    prefill_from_resume,
    step,
)
from ..onboarding.resume_intake import parse_resume_file
from ..onboarding.writer import build_files, write_files

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class AuthBody(BaseModel):
    mode: Literal["api_key", "claude_code"]
    api_key: str = ""


class InterviewBody(BaseModel):
    message: str = ""


class CompaniesBody(BaseModel):
    companies: list[dict[str, Any]]


def _models() -> LLMSettings:
    if paths.is_configured():
        try:
            return load_config().llm
        except Exception:  # noqa: BLE001 - a broken config shouldn't block setup
            pass
    return LLMSettings()


def _llm() -> ClaudeClient:
    try:
        return build_llm()
    except LLMError as e:
        raise HTTPException(503, str(e)) from e


def _state_payload(state: InterviewState) -> dict[str, Any]:
    exps = (state.resume_content or {}).get("experiences") or {}
    return {
        "messages": [m.model_dump() for m in state.messages],
        "draft": state.draft.model_dump(exclude_none=True),
        "missing": [{"field": f, "label": REQUIRED_FIELDS[f]} for f in state.missing()],
        "required_total": len(REQUIRED_FIELDS),
        "optional": OPTIONAL_FIELDS,
        "done": state.done,
        "resume": {
            "parsed": state.resume_content is not None,
            "name": ((state.resume_content or {}).get("header") or {}).get("name"),
            "roles": len(exps),
            "bullets": sum(len(e.get("bullets") or []) for e in exps.values()),
        },
        "companies": state.companies,
    }


def register_setup_routes(app: FastAPI) -> None:
    """Attach the /api/setup/* routes to `app`."""

    @app.get("/api/setup/status")
    async def setup_status() -> dict[str, Any]:
        s = load_secrets()
        return {
            "configured": paths.is_configured(),
            "profile_dir": str(paths.profile_dir()),
            "auth": {
                "mode": s.auth_mode,
                "connected": llm_available(s),
                "api_key_preview": mask(s.anthropic_api_key),
                "cli": describe_cli(s.claude_cli_path),
            },
            "interview": _state_payload(InterviewState.load()),
        }

    @app.post("/api/setup/auth")
    async def setup_auth(body: AuthBody) -> dict[str, Any]:
        values: dict[str, str] = {"JOB_PIPELINE_AUTH_MODE": body.mode}
        key = body.api_key.strip()
        if body.mode == "api_key":
            if key:
                if not key.startswith("sk-ant-"):
                    raise HTTPException(400, "That doesn't look like an Anthropic API key "
                                             "(they start with sk-ant-).")
                values["ANTHROPIC_API_KEY"] = key
            elif not load_secrets().anthropic_api_key:
                raise HTTPException(400, "Paste an API key first.")
        set_env_values(values)
        return await asyncio.to_thread(self_check, _models().fast_model)

    @app.post("/api/setup/resume")
    async def setup_resume(file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
        suffix = Path(file.filename or "resume").suffix.lower()
        if suffix not in (".pdf", ".docx", ".txt", ".md"):
            raise HTTPException(400, "Upload a PDF, DOCX, or TXT resume.")
        llm = _llm()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / f"resume{suffix}"
            with dest.open("wb") as out:
                shutil.copyfileobj(file.file, out, length=1024 * 1024)
            if dest.stat().st_size > MAX_UPLOAD_BYTES:
                raise HTTPException(400, "That file is over 10 MB.")
            try:
                content = await asyncio.to_thread(
                    parse_resume_file, dest, llm=llm, model=_models().smart_model
                )
            except (ValueError, LLMError, ValidationError) as e:
                raise HTTPException(422, f"Couldn't parse that resume: {e}") from e
        state = InterviewState.load()
        prefill_from_resume(state, content)
        state.save()
        return _state_payload(state)

    @app.post("/api/setup/interview")
    async def setup_interview(body: InterviewBody) -> dict[str, Any]:
        llm = _llm()
        state = InterviewState.load()
        try:
            state = await asyncio.to_thread(
                step, state, body.message or None, llm=llm, model=_models().fast_model
            )
        except LLMError as e:
            raise HTTPException(502, str(e)) from e
        return _state_payload(state)

    @app.post("/api/setup/companies/suggest")
    async def setup_suggest() -> dict[str, Any]:
        llm = _llm()
        state = InterviewState.load()
        try:
            state.companies = await asyncio.to_thread(
                suggest_companies, state.draft, llm=llm, model=_models().smart_model
            )
        except LLMError as e:
            raise HTTPException(502, str(e)) from e
        state.save()
        return _state_payload(state)

    @app.post("/api/setup/companies/verify")
    async def setup_verify(body: CompaniesBody) -> dict[str, Any]:
        state = InterviewState.load()
        state.companies = await asyncio.to_thread(verify_companies, body.companies)
        state.save()
        return _state_payload(state)

    @app.get("/api/setup/preview")
    async def setup_preview() -> dict[str, Any]:
        state = InterviewState.load()
        try:
            return {"files": build_files(state)}
        except ValidationError as e:
            raise HTTPException(422, f"Profile isn't complete yet: {e}") from e

    @app.post("/api/setup/commit")
    async def setup_commit() -> dict[str, Any]:
        state = InterviewState.load()
        try:
            written = write_files(state)
        except ValidationError as e:
            raise HTTPException(422, f"Profile isn't complete yet: {e}") from e
        return {"written": [str(p) for p in written], "configured": True}

    @app.post("/api/setup/reset")
    async def setup_reset() -> dict[str, Any]:
        return _state_payload(InterviewState.reset())
