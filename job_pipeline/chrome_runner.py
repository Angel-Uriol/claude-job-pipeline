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
* The Claude Code CLI with a FULL Claude sign-in (`claude auth login`, the
  Setup page's "Log in with Claude"). Claude Code reaches the extension
  through Anthropic's browser bridge (wss://bridge.claudeusercontent.com),
  which rejects the inference-only `claude setup-token` token with
  "OAuth token forbidden (403) — missing scope". Verified 2026-11-04; it
  works while Claude Desktop is open too.
* The Claude in Chrome extension installed and signed into the SAME Claude
  account, with Chrome open.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .config import Config, Secrets, load_secrets
from .llm.base import extract_json
from .llm.cli_client import STRIP_ENV, cli_model_alias, resolve_claude_cli
from .llm.usage import tagged

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
    "Claude in Chrome didn't answer. Fix: (1) install the Claude in Chrome extension "
    "(claude.ai/chrome) in Google Chrome; (2) click its icon once and sign in with the "
    "same Claude account this app uses; (3) keep Chrome open and click Test again."
)
# The cause we hit in practice: the quick `claude setup-token` token only
# allows model calls; Anthropic's browser bridge rejects it with
# "OAuth token forbidden (403) — missing scope". A full sign-in fixes it.
NEEDS_LOGIN_HELP = (
    "Chrome searches need a full Claude sign-in. The quick sign-in token can score "
    "jobs but isn't allowed to use Chrome. Fix: Setup → Connect Claude → click "
    "“Sign in for Chrome” (approve it in the browser), then Test again."
)
#: error codes on ChromeRunError / check results
CODE_NOT_CONNECTED = "chrome_not_connected"
CODE_NEEDS_LOGIN = "chrome_needs_login"
CODE_NO_CLI = "claude_cli_missing"

ProgressFn = Callable[[str], None]

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chrome-claude")


def _child_env(cli: str, secrets: Secrets) -> dict[str, str]:
    """Env for a Chrome-enabled CLI session.

    Uses the CLI's own full sign-in when there is one: the setup token
    (CLAUDE_CODE_OAUTH_TOKEN) would take precedence over it, and the
    browser bridge rejects that token. Without a full sign-in we still pass
    the token, so the check can tell the user exactly what's missing.
    """
    from .llm.cli_client import cli_account

    env = {k: v for k, v in os.environ.items()
           if k not in (*STRIP_ENV, "CLAUDE_CODE_OAUTH_TOKEN")}
    env["PATH"] = str(Path(cli).parent) + os.pathsep + env.get("PATH", "")
    if secrets.claude_code_oauth_token and not cli_account(cli)["logged_in"]:
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


#: Sent (in the same session) when a search runs out of steps, so the roles
#: it already verified aren't thrown away.
WRAP_UP_PROMPT = (
    "You've run out of browsing steps. Stop browsing now. Reply with ONLY the JSON "
    "object described earlier, containing every role you already verified (with its "
    "URL and the description text you read), and say in `notes` where you stopped."
)


async def _session(
    prompt: str, *, cli: str, secrets: Secrets, model: str, max_turns: int,
    progress: ProgressFn | None, debug_file: Path | None = None,
    resume: str | None = None, salvage: bool = False,
) -> tuple[str, list[str], float | None]:
    """Run one Chrome-enabled session → (final text, tool names used, cost).

    With `salvage`, a session that hits `max_turns` is resumed once with
    WRAP_UP_PROMPT (no tools) so it returns what it found so far.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolUseBlock,
        query,
    )

    options = ClaudeAgentOptions(
        model=cli_model_alias(model),
        allowed_tools=[] if resume else ALLOWED_TOOLS,
        disallowed_tools=DENIED_TOOLS + (CHROME_TOOLS if resume else []),
        max_turns=max_turns,
        cli_path=cli,
        env=_child_env(cli, secrets),
        setting_sources=[],
        cwd=tempfile.gettempdir(),
        resume=resume,
        extra_args={"chrome": None,
                    **({"debug-file": str(debug_file)} if debug_file else {})},
    )
    texts: list[str] = []
    tools: list[str] = []
    result: str | None = None
    cost: float | None = None
    session_id: str | None = None
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, SystemMessage):
                session_id = str((msg.data or {}).get("session_id") or "") or session_id
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
                session_id = msg.session_id or session_id
                result = msg.result or result
                cost = getattr(msg, "total_cost_usd", None)
                from .llm.usage import record_cli_result

                record_cli_result(model, msg)
    except Exception as e:
        out_of_steps = "maximum number of turns" in str(e).lower()
        if not (salvage and out_of_steps and session_id):
            raise
        if progress:
            progress("Used all browsing steps — collecting the roles found so far…")
        text, _, more = await _session(
            WRAP_UP_PROMPT, cli=cli, secrets=secrets, model=model, max_turns=2,
            progress=None, resume=session_id)
        return text, tools, (cost or 0.0) + (more or 0.0)
    return (result or "\n".join(texts)).strip(), tools, cost


def _run(coro: Any) -> Any:
    ctx = contextvars.copy_context()  # keep the usage_feature tag
    return _EXECUTOR.submit(ctx.run, asyncio.run, coro).result()


def _require_cli(secrets: Secrets) -> str:
    cli = resolve_claude_cli(secrets.claude_cli_path)
    if not cli:
        raise ChromeRunError("Claude Code CLI not found — install it on Setup step 1.",
                             CODE_NO_CLI)
    return cli


class ChromeRunError(RuntimeError):
    """User-facing problem launching or running the Chrome session."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


# --- readiness (no Claude call) -------------------------------------------------

#: The Claude in Chrome extension's Chrome Web Store id.
EXTENSION_ID = "fcoeoabgfenejglbffodgkkbkcdhcgfn"
EXTENSION_URL = "https://claude.ai/chrome"


def _chrome_profile_roots() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Google" / "Chrome"]
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return [local / "Google" / "Chrome" / "User Data"]
    return [home / ".config" / "google-chrome"]


def extension_installed() -> bool | None:
    """Is the Claude in Chrome extension in any local Chrome profile?
    None when Chrome's folder isn't found (unknown, not "no")."""
    roots = [r for r in _chrome_profile_roots() if r.exists()]
    if not roots:
        return None
    for root in roots:
        for profile in root.iterdir():
            if (profile / "Extensions" / EXTENSION_ID).exists():
                return True
    return False


def chrome_readiness(secrets: Secrets | None = None) -> dict[str, Any]:
    """The three things Chrome searches need, for the Setup checklist."""
    from .llm.cli_client import cli_account, resolve_claude_cli

    s = secrets or load_secrets()
    cli = resolve_claude_cli(s.claude_cli_path)
    account = cli_account(s.claude_cli_path) if cli else {"logged_in": False, "email": ""}
    return {
        "cli_found": bool(cli),
        "signed_in": bool(account["logged_in"]),
        "account": account.get("email", ""),
        "plan": account.get("plan", ""),
        "extension_installed": extension_installed(),
        "extension_url": EXTENSION_URL,
    }


# --- connection check -------------------------------------------------------


def diagnose_debug_log(text: str) -> str:
    """Claude Code's debug log → the cause code ("" = the extension answered).

    The log is the source of truth: the model's own reading of the tool
    result is unreliable (it took "This session has no tab group of its
    own" to mean "not connected").
    """
    if "OAuth token forbidden" in text or "missing scope" in text:
        return CODE_NEEDS_LOGIN
    answered = "Tool call completed: tabs_context_mcp" in text or (
        "Selected Chrome extension" in text and '"type":"tool_result"' in text)
    return "" if answered else CODE_NOT_CONNECTED


def check_chrome(secrets: Secrets | None = None) -> dict[str, Any]:
    """Ask a tiny Chrome-enabled session whether the extension answers.

    Returns {connected, detail, code, account}. The cause comes from Claude
    Code's own debug log (bridge errors), not from guessing at the reply.
    """
    from .llm.cli_client import cli_account

    s = secrets or load_secrets()
    try:
        cli = _require_cli(s)
    except ChromeRunError as e:
        return {"connected": False, "detail": str(e), "code": e.code, "account": ""}
    account = cli_account(cli)
    who = account["email"] if account["logged_in"] else ""
    with tempfile.TemporaryDirectory(prefix="jp-chrome-") as tmp:
        log_file = Path(tmp) / "debug.log"
        try:
            text, tools, _cost = _run(_session(
                "Call the mcp__claude-in-chrome__tabs_context_mcp tool exactly once. If its "
                "result says the browser extension is not connected, reply NOT_CONNECTED "
                "followed by that message. Otherwise reply exactly CONNECTED.",
                cli=cli, secrets=s, model="claude-haiku-5-5", max_turns=4, progress=None,
                debug_file=log_file,
            ))
        except Exception as e:  # noqa: BLE001 - SDK/CLI errors → shown to the user
            return {"connected": False, "code": CODE_NOT_CONNECTED, "account": who,
                    "detail": f"Couldn't start Claude Code with Chrome: {e}"}
        log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() \
            else ""
    used = any("tabs_context" in t for t in tools)
    if log:
        code = diagnose_debug_log(log)
        ok = used and not code
    else:  # no debug log (older CLI): fall back to the model's reply
        code = ""
        ok = used and text.strip().upper().startswith("CONNECTED")
    if ok:
        return {"connected": True, "code": "", "account": who,
                "detail": "Claude in Chrome is connected."
                + (f" Signed in as {who}." if who else "")}
    if not account["logged_in"]:
        code = CODE_NEEDS_LOGIN  # the setup token can never reach Chrome
    code = code or CODE_NOT_CONNECTED
    return {"connected": False, "code": code, "account": who,
            "detail": NEEDS_LOGIN_HELP if code == CODE_NEEDS_LOGIN else NOT_CONNECTED_HELP}


# --- the premium search ---------------------------------------------------------

SEARCH_PROMPT = """You are doing a careful job search in the user's Chrome browser for
the candidate below. Search ONLY these companies' own careers sites:

{targets}
{job_reads}
Candidate profile:
{profile}

How to work (you have about {budget} browser actions in total — be efficient):
- Open each company's careers site (use the given URL, or find the official one),
  search/filter for roles matching the candidate's target roles, level and
  locations, and open each promising posting to read it.
- Prefer search over paging: put keywords in the site's search box or URL (Workday
  sites, *.myworkdayjobs.com, accept `?q=data%20engineer`). Don't page through more
  than 3 result pages per search; try a sharper search instead.
- Open postings directly by their URL and read them with get_page_text (one call)
  instead of clicking and scrolling.
- Once you have the strongest matches — or you've used about {stop_at} actions —
  stop browsing and reply with the JSON. A partial answer beats none.
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


@tagged("premium-search")
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
    from .llm.base import LLMError
    from .llm.budget import check

    try:
        check(s)  # monthly budget in "stop" mode
    except LLMError as e:
        raise ChromeRunError(str(e)) from e
    cli = _require_cli(s)
    # Quick connection test first (~10s on Haiku): if Chrome isn't reachable,
    # stop now instead of spending a full browsing session on Sonnet.
    if progress:
        progress("Checking that Claude in Chrome is connected…")
    pre = check_chrome(s)
    if not pre.get("connected"):
        raise ChromeRunError(str(pre.get("detail") or NOT_CONNECTED_HELP),
                             str(pre.get("code") or CODE_NOT_CONNECTED))
    names = [*record.get("ad_hoc_companies", []), *record.get("companies", [])]
    urls = [u for u in record.get("job_urls") or [] if u]
    if not names and not urls:
        raise ChromeRunError("This premium search has no companies or postings.")
    notes = record.get("extra_notes") or ""
    targets = "\n".join(f"- {n}" for n in names) or "- (none — only the postings below)"
    job_reads = (
        "\nAlso open each of these specific postings and capture its FULL description "
        "(include them in `jobs` even if they're already known):\n"
        + "\n".join(f"- {u}" for u in urls) + "\n"
    ) if urls else ""
    prompt = SEARCH_PROMPT.format(
        targets=targets,
        job_reads=job_reads,
        profile=render_prompt("candidate_profile.txt", cfg),
        per_company=cfg.chrome_claude.max_roles_per_company,
        budget=MAX_TURNS - 10, stop_at=MAX_TURNS - 30,
        extra=f"- Extra notes from the request: {notes}\n" if notes else "",
    )
    if progress:
        what = ", ".join(names) or f"{len(urls)} posting(s)"
        progress(f"Launching Claude in Chrome for {what}…")
    text, tools, cost = _run(_session(
        prompt, cli=cli, secrets=s, model=cfg.llm.smart_model or DEFAULT_MODEL,
        max_turns=MAX_TURNS, progress=progress, salvage=True,
    ))
    if "not connected" in text.lower() and not any("navigate" in t for t in tools):
        raise ChromeRunError(NOT_CONNECTED_HELP, CODE_NOT_CONNECTED)
    try:
        data = extract_json(text)
    except (ValueError, json.JSONDecodeError) as e:
        raise ChromeRunError(f"Claude didn't return results in the expected format: {e}") \
            from e
    jobs = [j for j in data.get("jobs") or [] if isinstance(j, dict)]
    return {"jobs": jobs, "notes": str(data.get("notes") or ""), "cost": cost,
            "pages_visited": sum(1 for t in tools if t.endswith("__navigate"))}


def _plan_used() -> float | None:
    from .plan_usage import get_plan_usage

    u = get_plan_usage(fresh=True)
    window = u.get("five_hour") if u.get("available") else None
    return float(window["used_pct"]) if window else None


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or after < before:
        return None  # unknown, or the 5-hour window reset mid-run
    return round(after - before, 1)


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
        before = _plan_used()
        try:
            result = run_premium(rec, cfg, progress=progress)
        except Exception as exc:  # noqa: BLE001 - never leave a request stuck "running"
            if isinstance(exc, ChromeRunError):
                err = exc
            else:
                log.exception("premium search failed")
                err = ChromeRunError(f"The Chrome search stopped unexpectedly: {exc}")
            if err.code in (CODE_NOT_CONNECTED, CODE_NEEDS_LOGIN):
                queue.release(rec["id"])  # keep it queued — it'll run after reconnecting
            else:
                queue.fail(rec["id"], error=str(err))
            counts["failed"] += 1
            counts["error"] = str(err)
            if err.code:
                counts["error_code"] = err.code
            if progress:
                progress(f"Premium search failed: {err}")
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
            # How much of the 5-hour plan window this run used (if readable).
            "plan_pct_used": _delta(before, _plan_used()),
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
