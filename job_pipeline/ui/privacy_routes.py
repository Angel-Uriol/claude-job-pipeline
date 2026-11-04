"""Privacy page backend: what's stored where, and "Delete all my data".

GET  /api/privacy             exactly what would be deleted
POST /api/privacy/delete-all  {"confirm": "DELETE"} — removes every profile
                              (resumes, jobs, tracker, chats), the shared data/
                              folder (usage ledger, queues) and the Claude / job
                              board keys in .env. Refused while a run is going,
                              and when the app is pinned to a custom folder.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .. import paths, profiles
from ..config import load_secrets

SECRET_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "JSEARCH_API_KEY")


class DeleteBody(BaseModel):
    confirm: str


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def inventory() -> dict[str, Any]:
    s = load_secrets()
    data_dir = paths.REPO_ROOT / "data"
    folders = [paths.profiles_root(), data_dir]
    legacy = paths.LEGACY_PROFILE_DIR
    if legacy.exists():
        folders.append(legacy)
    return {
        "profiles": [{"name": p["name"], "slug": p["slug"]} for p in profiles.list_profiles()],
        "folders": [{"path": str(f), "exists": f.exists(), "bytes": _size(f)} for f in folders],
        "keys": [k for k, v in (("ANTHROPIC_API_KEY", s.anthropic_api_key),
                                ("CLAUDE_CODE_OAUTH_TOKEN", s.claude_code_oauth_token),
                                ("JSEARCH_API_KEY", s.jsearch_api_key)) if v],
        "pinned": paths.pinned_profile_dir() is not None,
    }


def delete_everything() -> dict[str, Any]:
    from ..onboarding.env_file import set_env_values

    if paths.pinned_profile_dir() is not None:
        raise HTTPException(409, "This app is pinned to a custom profile folder "
                                 "(JOB_PIPELINE_PROFILE_DIR) — delete that folder yourself.")
    inv = inventory()
    removed: list[str] = []
    for f in inv["folders"]:
        p = Path(f["path"])
        if p.exists():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            removed.append(str(p))
    if paths.ENV_FILE.exists():
        set_env_values({k: "" for k in SECRET_KEYS})
    return {"removed": removed, "keys_cleared": inv["keys"]}


def register_privacy_routes(app: FastAPI) -> None:
    @app.get("/api/privacy")
    async def privacy() -> dict[str, Any]:
        return inventory()

    @app.post("/api/privacy/delete-all")
    async def delete_all(body: DeleteBody) -> dict[str, Any]:
        if body.confirm != "DELETE":
            raise HTTPException(400, 'Type DELETE (in capitals) to confirm.')
        if app.state.pipeline_state.running is not None:
            raise HTTPException(409, "A search is running — wait for it to finish first.")
        async with app.state.lock:
            return delete_everything()
