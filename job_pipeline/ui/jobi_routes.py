"""HTTP routes for Jobi, the floating helper chat.

GET  /api/jobi                          history, recent change cards, model
POST /api/jobi/chat                     {message, page} → streamed NDJSON events
POST /api/jobi/proposals/{id}/apply     save a proposed change (or run it)
POST /api/jobi/proposals/{id}/dismiss
POST /api/jobi/reset                    start a new chat

Claude runs in a worker thread; its events are relayed line by line
(`application/x-ndjson`), because EventSource can't POST.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import paths
from ..config import load_secrets
from ..demo import is_demo
from ..llm.base import LLMError

#: Card kinds that write profile files (refused while a search is running).
WRITES = {"update_profile", "update_resume", "set_companies", "update_search_settings"}


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    page: str = "/"


def register_jobi_routes(app: FastAPI) -> None:
    # Imported here: assistant/ reuses ui.edit_routes, and ui/__init__ imports
    # server.py (which imports this module) — a top-level import would cycle.
    from ..assistant import agent, proposals
    from ..assistant.tools import ToolContext

    app.state.jobi_busy = False

    def config_file() -> Path:
        return Path(app.state.config_path or paths.config_path()).resolve()

    def pipeline() -> dict[str, Any]:
        st = app.state.pipeline_state
        return {"running": st.running, "last_run_phase": st.last_run_phase,
                "last_run_at": st.last_run_at, "last_run_error": st.last_run_error}

    @app.get("/api/jobi")
    async def jobi_state() -> dict[str, Any]:
        cards = [p.model_dump() for p in proposals.load_all()[-30:]]
        return {"history": agent.load_history()[-60:], "cards": cards,
                "model": agent.model_name(), "demo": is_demo(),
                "auth_mode": load_secrets().auth_mode, "busy": app.state.jobi_busy}

    @app.post("/api/jobi/reset")
    async def jobi_reset() -> dict[str, Any]:
        if app.state.jobi_busy:
            raise HTTPException(409, "Jobi is still answering.")
        agent.reset_history()
        return {"ok": True}

    @app.post("/api/jobi/chat")
    async def jobi_chat(body: ChatBody) -> StreamingResponse:
        if app.state.jobi_busy:
            raise HTTPException(409, "Jobi is still answering the last message.")
        app.state.jobi_busy = True
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        ctx = ToolContext(config_path=config_file(), pipeline=pipeline())

        def work() -> None:
            try:
                agent.run_turn(body.message.strip(), page=body.page, ctx=ctx, emit=emit)
            except LLMError as e:
                agent.append_history("assistant", f"⚠️ {e}")
                emit({"type": "error", "text": str(e)})
            except Exception as e:  # noqa: BLE001 - never leave the chat hanging
                emit({"type": "error", "text": f"Something went wrong: {e}"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        async def stream() -> AsyncIterator[bytes]:
            task = asyncio.create_task(asyncio.to_thread(work))
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield (json.dumps(event, default=str) + "\n").encode()
                await task
            finally:
                app.state.jobi_busy = False

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/jobi/proposals/{pid}/apply")
    async def jobi_apply(pid: str) -> dict[str, Any]:
        try:
            card = proposals.get(pid)
        except proposals.ProposalError as e:
            raise HTTPException(404, str(e)) from e
        if card.kind in WRITES and app.state.pipeline_state.running is not None:
            raise HTTPException(409, "A search is running — apply this when it finishes.")
        if card.kind in ("start_search", "start_premium_search") and \
                app.state.pipeline_state.running is not None:
            raise HTTPException(409, f"Already running: {app.state.pipeline_state.running}.")
        try:
            async with app.state.lock:
                out = await asyncio.to_thread(proposals.apply, pid, config_file())
        except proposals.ProposalError as e:
            agent.append_history("note", f"Couldn't apply “{card.title}”: {e}")
            raise HTTPException(400, str(e)) from e
        note = f"Applied: {card.title} — {out.get('message', '')}"
        if out.get("log"):
            note += f"\nOutput:\n{str(out['log'])[-1500:]}"
        agent.append_history("note", note)
        return {**out, "card": proposals.get(pid).model_dump()}

    @app.post("/api/jobi/proposals/{pid}/dismiss")
    async def jobi_dismiss(pid: str) -> dict[str, Any]:
        try:
            proposals.dismiss(pid)
        except proposals.ProposalError as e:
            raise HTTPException(404, str(e)) from e
        agent.append_history("note", f"Dismissed: {proposals.get(pid).title}")
        return {"ok": True}
