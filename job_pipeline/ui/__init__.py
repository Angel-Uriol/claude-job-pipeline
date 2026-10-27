"""Local web UI for the pipeline.

`server.create_app()` returns the FastAPI app — used by the `serve`
CLI command and by tests.

The app exposes:
- a single HTML page at `/`
- JSON endpoints under `/api/...`
- an SSE stream at `/api/events` for live pipeline progress

All operations run in-process; one pipeline phase at a time. Bind to
127.0.0.1 only.
"""

from .events import EventBus, PipelineState
from .server import create_app

__all__ = ["EventBus", "PipelineState", "create_app"]
