"""Event bus + pipeline state — the cross-thread coordination layer.

`EventBus` is a tiny fan-out pubsub: each SSE client subscribes and
gets its own `asyncio.Queue`. The pipeline phases run in a worker
thread (via `asyncio.to_thread`) and emit `ProgressEvent`s; the bus
forwards them to every subscriber queue.

Why a custom bus instead of an asyncio.Queue per subscriber?
- The pipeline emits from a non-async thread; we need a thread-safe
  publish API.
- Multiple browser tabs can be open → multiple subscribers must each
  see every event.

`PipelineState` tracks which phase is currently running (so we can
return 409 on a second concurrent start) and when the last run ended.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..runner import ProgressEvent

log = logging.getLogger(__name__)


# --- pipeline state -----------------------------------------------------


@dataclass
class PipelineState:
    """Mutable state shared across UI requests.

    `running` is the phase name (`scrape`, `pre-filter`, `deep-fit`, ...
    or `daily`) while a task is in progress; `None` when idle. Used by
    `/api/run/<phase>` to 409 a concurrent start and by `/api/state` to
    show the UI which button to disable.
    """

    running: str | None = None
    last_run_at: str | None = None       # ISO 8601
    last_run_phase: str | None = None
    last_run_result: dict[str, Any] = field(default_factory=dict)
    last_run_error: str | None = None

    def start(self, phase: str) -> None:
        self.running = phase
        self.last_run_error = None

    def finish(self, phase: str, result: dict[str, Any]) -> None:
        self.running = None
        self.last_run_at = datetime.now(UTC).isoformat()
        self.last_run_phase = phase
        self.last_run_result = result

    def fail(self, phase: str, error: str) -> None:
        self.running = None
        self.last_run_at = datetime.now(UTC).isoformat()
        self.last_run_phase = phase
        self.last_run_error = error


# --- event bus ---------------------------------------------------------


# Max events to buffer per subscriber before we drop. 256 covers a full
# scrape + pre-filter run with headroom.
_QUEUE_MAXSIZE = 256


class EventBus:
    """Fan-out pubsub for ProgressEvents. Thread-safe `publish_threadsafe`."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[ProgressEvent]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called by the server at startup to capture the event loop —
        publishers from worker threads need it for call_soon_threadsafe."""
        self._loop = loop

    async def subscribe(self) -> asyncio.Queue[ProgressEvent]:
        """Register a new subscriber; returns its queue."""
        q: asyncio.Queue[ProgressEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[ProgressEvent]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def publish_threadsafe(self, event: ProgressEvent) -> None:
        """Publish from any thread. Safe to call from the pipeline worker."""
        if self._loop is None:
            log.warning("EventBus.publish_threadsafe called before bind_loop")
            return
        self._loop.call_soon_threadsafe(self._publish_local, event)

    def _publish_local(self, event: ProgressEvent) -> None:
        """Runs in the event loop thread — direct enqueue is safe."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop rather than block the producer.
                log.debug("Dropping event for slow subscriber: %s", event)


# --- helpers -----------------------------------------------------------


def event_to_dict(e: ProgressEvent) -> dict[str, Any]:
    """Serialize a ProgressEvent for the SSE wire format."""
    return asdict(e)


# Type alias used by `server.py` so the runner-task helper can stay typed.
PipelineCoro = Callable[[], Awaitable[dict[str, Any]]]
