"""HTTP routes for profiles: list, create, switch, rename, delete, export, import.

Kept out of server.py like setup_routes.py. Switching is refused while a
pipeline run is in progress, so a run never writes into the wrong person's
history.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .. import profiles
from ..config import LLMSettings
from ..llm import LLMError, build_llm


class NameBody(BaseModel):
    name: str


P = ParamSpec("P")
T = TypeVar("T")


def _guard(fn: Callable[P, T], *args: P.args, **kw: P.kwargs) -> T:
    try:
        return fn(*args, **kw)
    except (profiles.ProfileError, ValueError) as e:
        raise HTTPException(400, str(e)) from e


def register_profile_routes(app: FastAPI) -> None:
    def _not_running() -> None:
        state = getattr(app.state, "pipeline_state", None)
        if state is not None and state.running:
            raise HTTPException(409, "A pipeline run is in progress — wait for it to finish.")

    @app.get("/api/profiles")
    async def list_profiles() -> dict[str, Any]:
        profiles.ensure_active()
        items = await asyncio.to_thread(profiles.list_profiles)
        return {
            "profiles": items,
            "active": next((p["slug"] for p in items if p["active"]), None),
            "switching_enabled": profiles.switching_enabled(),
        }

    @app.post("/api/profiles")
    async def create_profile(body: NameBody) -> dict[str, Any]:
        _not_running()
        return _guard(profiles.create, body.name)

    @app.post("/api/profiles/demo")
    async def create_demo_profile() -> dict[str, Any]:
        """Made-up demo data so people can explore before setting anything up."""
        from ..demo import create_demo

        _not_running()
        return await asyncio.to_thread(_guard, create_demo)

    @app.post("/api/profiles/{slug}/activate")
    async def activate_profile(slug: str) -> dict[str, Any]:
        _not_running()
        return _guard(profiles.activate, slug)

    @app.patch("/api/profiles/{slug}")
    async def rename_profile(slug: str, body: NameBody) -> dict[str, Any]:
        return _guard(profiles.rename, slug, body.name)

    @app.delete("/api/profiles/{slug}")
    async def delete_profile(slug: str) -> dict[str, Any]:
        _not_running()
        _guard(profiles.delete, slug)
        return {"deleted": slug}

    @app.get("/api/profiles/{slug}/export")
    async def export_profile(slug: str, history: bool = True) -> Response:
        bundle = await asyncio.to_thread(
            _guard, profiles.export_profile, slug, include_history=history
        )
        filename = f"{profiles.slugify(bundle['name'])}-profile.json"
        return Response(
            content=json.dumps(bundle, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/profiles/import")
    async def import_profile(
        file: UploadFile = File(...),  # noqa: B008
        name: str = Form(""),
    ) -> dict[str, Any]:
        _not_running()
        raw = await file.read(profiles.MAX_IMPORT_BYTES + 1)
        if len(raw) > profiles.MAX_IMPORT_BYTES:
            raise HTTPException(400, "That file is over 20 MB.")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise HTTPException(400, f"That isn't valid JSON: {e}") from e
        if profiles.is_bundle(data):
            result = await asyncio.to_thread(_guard, profiles.import_bundle, data,
                                             name=name or None)
            result["mode"] = "restore"
            return dict(result)
        # Not one of our exports: let Claude read it and pre-fill a new profile.
        try:
            llm = build_llm()
        except LLMError as e:
            raise HTTPException(503, f"Importing a free-form JSON file needs Claude: {e}") from e
        try:
            result = await asyncio.to_thread(
                profiles.smart_import, data, llm=llm, model=LLMSettings().smart_model,
                name=name or None,
            )
        except LLMError as e:
            raise HTTPException(502, str(e)) from e
        result["mode"] = "smart"
        return dict(result)
