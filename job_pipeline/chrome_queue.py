"""File-based queue for Chrome-Claude browser-search runs.

Why this exists
---------------
The "Run Chrome Claude" button in the web UI cannot drive the browser
itself — only a Claude Code agent session (which holds the Claude-in-Chrome
connection) can. So the button just *enqueues* a run here, and a separate
Claude Code "watcher loop" drains the queue: it reads each pending request,
drives Chrome per ``prompts/chrome_claude_runbook.md``, imports the results
with the existing ``cli import``, and marks the request done.

This module is the SINGLE source of truth for the queue:
  - the web backend imports ``ChromeQueue`` for the enqueue + status routes;
  - the watcher loop uses the ``python -m job_pipeline.chrome_queue`` CLI
    (``list`` / ``claim`` / ``finish`` / ``fail``) so both sides touch the
    files the same way.

On-disk layout (all under ``<repo>/data/chrome_runs/``)::

    pending/<id>.json   queued, or picked up (status: pending | running)
    done/<id>.json      finished (status: done | error), carries a summary

A request stays in ``pending/`` while it runs (status="running") so a crash
leaves it visible and recoverable; it only moves to ``done/`` when finished.
There is exactly one watcher loop, so ``claim_next`` does not need locking.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# This file lives at <repo>/job_pipeline/chrome_queue.py, so the repo root is
# two parents up. The queue lives beside jobs.json under data/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BASE = _REPO_ROOT / "data" / "chrome_runs"

VALID_SCOPES = ("selected", "new", "mix")

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ChromeQueue:
    """A tiny on-disk queue of Chrome-Claude run requests."""

    def __init__(self, base_dir: Path | str = _DEFAULT_BASE) -> None:
        self.base = Path(base_dir)
        self.pending_dir = self.base / "pending"
        self.done_dir = self.base / "done"

    # --- enqueue (web backend) ----------------------------------------

    def enqueue(
        self,
        *,
        scope: str,
        companies: list[str] | None = None,
        posted_within: int | None = None,
        score_after: bool = False,
        ad_hoc_companies: list[str] | None = None,
        extra_notes: str = "",
    ) -> dict[str, Any]:
        """Write a new pending request and return its record.

        `scope` is one of selected | new | mix. `companies` only matters
        when the scope includes "selected" — it narrows which no_api
        companies to scrape (empty = all of them). `posted_within` is a
        day window (None/<=0 = any age). `score_after` runs the scoring
        phases after import.
        """
        if scope not in VALID_SCOPES:
            raise ValueError(f"bad scope {scope!r}; expected one of {VALID_SCOPES}")
        if posted_within is not None and posted_within <= 0:
            posted_within = None

        self._ensure_dirs()
        rid = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        record: dict[str, Any] = {
            "id": rid,
            "scope": scope,
            "companies": list(companies or []),
            "ad_hoc_companies": list(ad_hoc_companies or []),
            "posted_within": posted_within,
            "score_after": bool(score_after),
            "extra_notes": extra_notes,
            "status": STATUS_PENDING,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "summary": None,
            "error": None,
        }
        self._write_pending(record)
        return record

    # --- status (web backend) -----------------------------------------

    def list_status(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first list of all requests (pending + done) for the UI."""
        self._ensure_dirs()
        records: list[dict[str, Any]] = []
        for directory in (self.pending_dir, self.done_dir):
            for path in directory.glob("*.json"):
                rec = self._read(path)
                if rec is not None:
                    records.append(rec)
        records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return records[:limit]

    # --- watcher-loop operations --------------------------------------

    def claim_next(self) -> dict[str, Any] | None:
        """Take the oldest pending request and mark it running.

        Returns the claimed record, or None if nothing is pending. Single
        watcher loop ⇒ no locking needed (request ids sort chronologically).
        """
        self._ensure_dirs()
        for path in sorted(self.pending_dir.glob("*.json"), key=lambda p: p.name):
            rec = self._read(path)
            if rec is not None and rec.get("status") == STATUS_PENDING:
                rec["status"] = STATUS_RUNNING
                rec["started_at"] = _now()
                self._write_pending(rec)
                return rec
        return None

    def finish(
        self, rid: str, *, summary: dict[str, Any] | str | None = None
    ) -> dict[str, Any]:
        """Mark a request done and move it to done/."""
        return self._complete(rid, status=STATUS_DONE, summary=summary, error=None)

    def fail(self, rid: str, *, error: str) -> dict[str, Any]:
        """Mark a request errored and move it to done/."""
        return self._complete(rid, status=STATUS_ERROR, summary=None, error=error)

    # --- internals ----------------------------------------------------

    def _complete(
        self,
        rid: str,
        *,
        status: str,
        summary: dict[str, Any] | str | None,
        error: str | None,
    ) -> dict[str, Any]:
        src = self.pending_dir / f"{rid}.json"
        if not src.exists():
            raise FileNotFoundError(f"no pending request {rid!r}")
        rec = self._read(src) or {}
        rec["status"] = status
        rec["finished_at"] = _now()
        if summary is not None:
            rec["summary"] = summary
        if error is not None:
            rec["error"] = error
        (self.done_dir / f"{rid}.json").write_text(
            json.dumps(rec, indent=2), encoding="utf-8"
        )
        src.unlink(missing_ok=True)
        return rec

    def _ensure_dirs(self) -> None:
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.done_dir.mkdir(parents=True, exist_ok=True)

    def _write_pending(self, record: dict[str, Any]) -> None:
        (self.pending_dir / f"{record['id']}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None


# --- CLI used by the watcher loop ---------------------------------------


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chrome_queue",
        description="Inspect / drain the Chrome-Claude run queue.",
    )
    parser.add_argument(
        "--base", default=str(_DEFAULT_BASE), help="queue base dir (default: data/chrome_runs)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")
    sub.add_parser("list", help="print all requests (pending + done) as JSON")
    sub.add_parser("claim", help="take the oldest pending request → running; print it (or null)")
    p_finish = sub.add_parser("finish", help="mark a request done")
    p_finish.add_argument("--id", required=True)
    p_finish.add_argument("--summary", default=None, help="JSON or plain text summary")
    p_fail = sub.add_parser("fail", help="mark a request errored")
    p_fail.add_argument("--id", required=True)
    p_fail.add_argument("--error", required=True)

    args = parser.parse_args(argv)
    q = ChromeQueue(args.base)

    if args.cmd == "list":
        print(json.dumps(q.list_status(), indent=2))
        return 0
    if args.cmd == "claim":
        rec = q.claim_next()
        print(json.dumps(rec, indent=2) if rec is not None else "null")
        return 0
    if args.cmd == "finish":
        summary: dict[str, Any] | str | None = args.summary
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except json.JSONDecodeError:
                pass  # keep as plain text
        print(json.dumps(q.finish(args.id, summary=summary), indent=2))
        return 0
    if args.cmd == "fail":
        print(json.dumps(q.fail(args.id, error=args.error), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_main())
