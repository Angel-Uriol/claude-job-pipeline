"""Premium search, run BY THE APP: a Claude Code session that controls Chrome.

The app launches the user's own Claude Code CLI with `--chrome` (the Claude
in Chrome integration) through the Claude Agent SDK, and Claude browses each
chosen company's careers site like a person would. When it finishes, it
returns the roles it verified as JSON; we import them into the profile (with
the applied guard) and, if asked, score them — all in one click.

Safety rails (enforced by the CLI, not just the prompt):
* Only Chrome READ/NAVIGATE tools are allowed — no shell, no file writes, no
  form filling, no file uploads. Everything else is denied in print mode.
* The prompt forbids logging in, applying, submitting, or contacting anyone.
* Each run is bounded by `MAX_TURNS`.

Requirements on the user's machine (checked by `check_chrome`):
* Claude Code CLI logged in (this app's "Claude Code login" step).
* The Claude in Chrome extension installed, logged into the SAME Claude
  account, and attached to Claude Code. Chrome connects the extension to ONE
  app at a time; if Claude Desktop owns it, run `/chrome` → Reconnect in a
  Claude Code terminal (with Desktop quit).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .config import Config, Secrets, load_secrets
from .llm.base import extract_json
from .llm.cli_client import STRIP_ENV, cli_model_alias, resolve_claude_cli

log = logging.getLogger(__name__)

# Browsing tools only. (No form_input, file_upload, shortcuts, gif recording.)
CHROME_TOOLS = [
    "mcp__claude-in-chrome__tabs_context_mcp",
    "mcp__claude-in-chrome__tabs_create_mcp",
    "mcp__claude-in-chrome__tabs_close_mcp",
    "mcp__claude-in-chrome__navigate",
    "mcp__claude-in-chrome__get_page_text",
    "mcp__claude-in-chrome__read_page",
    "mcp__claude-in-chrome__find",
    "mcp__claude-in-chrome__computer",
    "mcp__claude-in-chrome__list_connected_browsers",
]
# ToolSearch lets the session load the (deferred) Chrome tool schemas.
ALLOWED_TOOLS = ["ToolSearch", *CHROME_TOOLS]
DENIED_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
                "mcp__claude-in-chrome__form_input", "mcp__claude-in-chrome__file_upload",
                "mcp__claude-in-chrome__javascript_tool"]
MAX_TURNS = 120
DEFAULT_MODEL = "claude-sonnet-5-5"

NOT_CONNECTED_HELP = (
    "Claude in Chrome isn't connected to Claude Code. Fix: (1) install the Claude in "
    "Chrome extension and log it into the same Claude account; (2) Chrome connects it "
    "to ONE app at a time — if you use Claude Desktop, quit it, open a terminal, run "
    "`claude`, then `/chrome` → Reconnect; (3) click Check again."
)

ProgressFn = Callable[[str], None]

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chrome-claude")


def _child_env(cli: str, secrets: Secrets) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env["PATH"] = str(Path(cli).parent) + os.pathsep + env.get("PATH", "")
    if secrets.claude_code_oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = secrets.claude_code_oauth_token
    return env


def _describe(tool: str, args: dict[str, Any]) -> str | None:
    """A friendly progress line for a Chrome tool call."""
    name = tool.rsplit("__", 1)[-1]
    if name == "navigate" and args.get("url"):
        return f"Visiting {str(args['url'])[:90]}"
    if name == "find" and args.get("query"):
        return f"Looking for “{str(args['query'])[:60]}” on the page"
    if name in ("get_page_text", "read_page"):
        return "Reading the page"
    if name == "tabs_create_mcp":
        return "Opening a new tab"
    return None


async def _session(
    prompt: str, *, cli: str, secrets: Secrets, model: str, max_turns: int,
    progress: ProgressFn | None,
) -> tuple[str, list[str], float | None]:
    """Run one Chrome-enabled session → (final text, tool names used, cost)."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        query,
    )

    options = ClaudeAgentOptions(
        model=cli_model_alias(model),
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DENIED_TOOLS,
        max_turns=max_turns,
        cli_path=cli,
        env=_child_env(cli, secrets),
        setting_sources=[],
        cwd=tempfile.gettempdir(),
        extra_args={"chrome": None},
    )
    texts: list[str] = []
    tools: list[str] = []
    result: str | None = None
    cost: float | None = None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock):
                    texts.append(b.text)
                elif isinstance(b, ToolUseBlock):
                    tools.append(b.name)
                    line = _describe(b.name, b.input or {})
                    if line and progress:
                        progress(line)
        elif isinstance(msg, ResultMessage):
            result = msg.result or result
            cost = getattr(msg, "total_cost_usd", None)
    return (result or "\n".join(texts)).strip(), tools, cost


def _run(coro: Any) -> Any:
    return _EXECUTOR.submit(asyncio.run, coro).result()


def _require_cli(secrets: Secrets) -> str:
    cli = resolve_claude_cli(secrets.claude_cli_path)
    if not cli:
        raise ChromeRunError("Claude Code CLI not found — install it on Setup step 1.")
    return cli


class ChromeRunError(RuntimeError):
    """User-facing problem launching or running the Chrome session."""


# --- connection check -------------------------------------------------------


def check_chrome(secrets: Secrets | None = None) -> dict[str, Any]:
    """Ask a tiny Chrome-enabled session whether the extension answers."""
    s = secrets or load_secrets()
    try:
        cli = _require_cli(s)
        text, tools, _cost = _run(_session(
            "Call the mcp__claude-in-chrome__tabs_context_mcp tool exactly once. Then reply "
            "with exactly CONNECTED if it returned tab information, or NOT_CONNECTED "
            "followed by its error message if it reported that the extension is not "
            "connected.",
            cli=cli, secrets=s, model="claude-haiku-5-5", max_turns=4, progress=None,
        ))
    except ChromeRunError as e:
        return {"connected": False, "detail": str(e)}
    except Exception as e:  # noqa: BLE001 - SDK/CLI errors → shown to the user
        return {"connected": False, "detail": f"Couldn't start Claude Code with Chrome: {e}"}
    ok = text.strip().upper().startswith("CONNECTED")
    used = any("tabs_context" in t for t in tools)
    if ok and used:
        return {"connected": True, "detail": "Claude in Chrome is connected."}
    return {"connected": False, "detail": NOT_CONNECTED_HELP}


# --- the premium search ---------------------------------------------------------

SEARCH_PROMPT = """You are doing a careful job search in the user's Chrome browser for
the candidate below. Search ONLY these companies' own careers sites:

{targets}

Candidate profile:
{profile}

How to work:
- Open each company's careers site (use the given URL, or find the official one),
  search/filter for roles matching the candidate's target roles, level and
  locations, and open each promising posting to read it.
- Keep up to {per_company} of the strongest real matches per company. Skip roles
  that break the candidate's hard rules (titles, years, location, sponsorship).
- Record each role's working apply URL and the FULL job description text.
{extra}
Hard rules — never break these:
- Read and navigate only. NEVER log in, create accounts, fill or submit forms,
  apply, upload files, accept anything, or contact anyone.
- Only visit the companies' own sites and their job-board hosts (e.g. Greenhouse,
  Lever, Ashby, Workday). Don't follow links to unrelated sites.
- Never invent roles, URLs or text. If a site won't load or blocks you, note it.

When done, reply with ONLY one JSON object (no markdown):
{{"jobs": [{{"company": "...", "job_id": "id or slug or null", "title": "...",
  "team": "... or null", "url": "https://...", "location": "...",
  "posted_date": "ISO date or null", "description": "full JD text"}}],
 "notes": "one line per company: what you searched and anything that blocked you"}}"""


def run_premium(
    record: dict[str, Any],
    cfg: Config,
    *,
    progress: ProgressFn | None = None,
    secrets: Secrets | None = None,
) -> dict[str, Any]:
    """Run one queued premium request → {"jobs": [...], "notes": str, "cost": float}."""
    from .prompts import render_prompt

    s = secrets or load_secrets()
    cli = _require_cli(s)
    names = [*record.get("ad_hoc_companies", []), *record.get("companies", [])]
    if not names:
        raise ChromeRunError("This premium search has no companies.")
    notes = record.get("extra_notes") or ""
    targets = "\n".join(f"- {n}" for n in names)
    prompt = SEARCH_PROMPT.format(
        targets=targets,
        profile=render_prompt("candidate_profile.txt", cfg),
        per_company=cfg.chrome_claude.max_roles_per_company,
        extra=f"- Extra notes from the request: {notes}\n" if notes else "",
    )
    if progress:
        progress(f"Launching Claude in Chrome for {', '.join(names)}…")
    text, tools, cost = _run(_session(
        prompt, cli=cli, secrets=s, model=cfg.llm.smart_model or DEFAULT_MODEL,
        max_turns=MAX_TURNS, progress=progress,
    ))
    if "not connected" in text.lower() and not any("navigate" in t for t in tools):
        raise ChromeRunError(NOT_CONNECTED_HELP)
    try:
        data = extract_json(text)
    except (ValueError, json.JSONDecodeError) as e:
        raise ChromeRunError(f"Claude didn't return results in the expected format: {e}") \
            from e
    jobs = [j for j in data.get("jobs") or [] if isinstance(j, dict)]
    return {"jobs": jobs, "notes": str(data.get("notes") or ""), "cost": cost,
            "pages_visited": sum(1 for t in tools if t.endswith("__navigate"))}


# --- one-click: run everything queued for this profile -------------------------


def run_pending_premium(
    cfg: Config,
    store: Any,
    *,
    profile: str | None,
    progress: ProgressFn | None = None,
    score: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Claim + run this profile's queued premium searches, import the results
    (skipping jobs already in the tracker), then score them via `score`.
    Returns counts for the run summary."""
    from .applied_guard import load_tracked_index
    from .chrome_queue import ChromeQueue
    from .importers import import_chrome_claude_jsonl

    queue = ChromeQueue()
    pending = queue.pending_for(profile)
    counts: dict[str, Any] = {"requests": 0, "found": 0, "imported": 0,
                              "already_tracked": 0, "failed": 0}
    if not pending:
        if progress:
            progress("No premium searches queued for this profile.")
        return counts
    tracked = load_tracked_index(cfg)
    out_dir = Path(cfg.paths.jobs_json).parent / "chrome_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    for rec in pending:
        if queue.claim(rec["id"]) is None:
            continue
        counts["requests"] += 1
        try:
            result = run_premium(rec, cfg, progress=progress)
        except ChromeRunError as e:
            if str(e) == NOT_CONNECTED_HELP:
                queue.release(rec["id"])  # keep it queued — it'll run after reconnecting
            else:
                queue.fail(rec["id"], error=str(e))
            counts["failed"] += 1
            counts["error"] = str(e)
            if progress:
                progress(f"Premium search failed: {e}")
            break  # same cause would fail the rest (e.g. Chrome not connected)
        jsonl = out_dir / f"{rec['id']}.jsonl"
        jsonl.write_text("\n".join(json.dumps(j, ensure_ascii=False) for j in result["jobs"]),
                         encoding="utf-8")
        stats = import_chrome_claude_jsonl(jsonl, store, tracked=tracked)
        store.save()
        counts["found"] += len(result["jobs"])
        counts["imported"] += stats.inserted
        counts["already_tracked"] += stats.already_tracked
        queue.finish(rec["id"], summary={
            "found": len(result["jobs"]), "imported": stats.inserted,
            "already_tracked": stats.already_tracked, "notes": result["notes"],
            "pages_visited": result["pages_visited"], "cost_usd": result["cost"],
        })
        if progress:
            progress(f"Found {len(result['jobs'])} roles, {stats.inserted} new. "
                     f"{result['notes'][:160]}")
    wants_score = any(r.get("score_after") for r in pending)
    if counts["imported"] and wants_score and score is not None:
        if progress:
            progress("Scoring the new roles…")
        counts["scored"] = score()
    return counts
