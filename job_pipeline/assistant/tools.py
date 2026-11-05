"""The tools Jobi (Claude) can call.

Three kinds:
* READ: look at the profile, resume, companies, settings, jobs, spending
  and recent runs (instant, nothing changes).
* DIAGNOSE: the same checks as `job-pipeline doctor` plus an optional
  Chrome connection test, and a hand-off prompt for a new Claude Code chat.
* PROPOSE: create a change card (proposals.py). Nothing is saved until the
  user clicks Apply.

Each tool is a plain function taking a pydantic input. agent.py wraps them
for both auth modes, so the API-key and Claude Code paths behave the same.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .. import paths
from . import commands, proposals
from .proposals import ProposalError, ResumeEdit

MAX_RESULT_CHARS = 12_000


@dataclass
class ToolContext:
    """What a tool call can see about the running app."""

    config_path: Path
    pipeline: dict[str, Any] = field(default_factory=dict)  # running / last error
    page: str = ""
    on_proposal: Callable[[proposals.Proposal], None] | None = None
    on_status: Callable[[str], None] | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: type[BaseModel]
    fn: Callable[[Any, ToolContext], dict[str, Any]]
    status: str  # plain progress line shown while it runs


# --- inputs --------------------------------------------------------------------


class Empty(BaseModel):
    pass


class JobsIn(BaseModel):
    limit: int = Field(10, ge=1, le=30)
    verdict: Literal["apply", "consider", "skip", "any"] = "any"


class DiagnoseIn(BaseModel):
    chrome: bool = Field(False, description="Also test the Claude in Chrome connection "
                         "(~10-20s, starts a tiny Claude Code session).")


class HandoffIn(BaseModel):
    problem: str = Field(description="What went wrong and what you already tried, in 1-3 lines.")
    goal: str = Field("", description="What the user wants done, e.g. 'run the Chrome search "
                      "for NVIDIA and Snowflake'.")


class ProfileChangeIn(BaseModel):
    changes: dict[str, Any] = Field(description="Profile fields → new values (full new value "
                                    "for lists).")
    reason: str = Field(description="One line: why, in the user's words.")


class ResumeEditIn(BaseModel):
    edits: list[ResumeEdit]
    reason: str


class CompanyAdd(BaseModel):
    name: str
    careers_url: str = ""
    tier: int = Field(2, ge=1, le=3, description="1 dream, 2 strong interest, 3 worth a look")


class CompaniesIn(BaseModel):
    add: list[CompanyAdd] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list, description="Company names to remove")
    reason: str


class SettingsIn(BaseModel):
    changes: dict[str, Any] = Field(description="Setting → new value. Allowed: " + "; ".join(
        f"{k} ({v})" for k, v in proposals.SETTINGS.items()))
    reason: str


class ReasonIn(BaseModel):
    reason: str


class PremiumIn(BaseModel):
    companies: list[str] = Field(description="Company names to search on their own careers "
                                 "sites (max 10).")
    reason: str


class CommandIn(BaseModel):
    command: Literal["doctor", "install_renderer", "uv_sync", "claude_version", "chrome_check"]
    reason: str


# --- helpers -----------------------------------------------------------------------


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return data if isinstance(data, dict) else {}


def _card(p: proposals.Proposal, ctx: ToolContext) -> dict[str, Any]:
    if ctx.on_proposal:
        ctx.on_proposal(p)
    return {"proposed": True, "card_id": p.id, "warnings": p.warnings,
            "note": "Shown to the user as a card. It is NOT saved until they click Apply."}


# --- read tools ----------------------------------------------------------------------


def get_profile(_: Empty, ctx: ToolContext) -> dict[str, Any]:
    return {"profile": _yaml(ctx.config_path).get("profile") or {}}


def get_resume(_: Empty, ctx: ToolContext) -> dict[str, Any]:
    path = ctx.config_path.parent / "resume_content.yaml"
    if not path.exists():
        return {"error": "No resume yet — the user should finish Setup."}
    return {"resume": _yaml(path)}


def get_companies(_: Empty, ctx: ToolContext) -> dict[str, Any]:
    raw = _yaml(ctx.config_path)
    sc = raw.get("scrapers") or {}
    targets = _yaml(ctx.config_path.parent / "target_companies.yaml").get("companies") or []
    return {
        "job_boards": {k: [c.get("name") for c in sc.get(k) or []]
                       for k in ("greenhouse", "lever", "ashby")},
        "no_api_careers_sites": [{"name": c.get("name"), "url": c.get("careers_url")}
                                 for c in sc.get("no_api") or []],
        "target_companies": [{"name": c.get("name"), "tier": c.get("tier")} for c in targets],
        "excluded_companies": raw.get("excluded_companies") or [],
    }


def get_search_settings(_: Empty, ctx: ToolContext) -> dict[str, Any]:
    raw = _yaml(ctx.config_path)
    return {k: {"value": proposals._get_path(raw, k), "meaning": v}
            for k, v in proposals.SETTINGS.items()}


def list_jobs(args: JobsIn, ctx: ToolContext) -> dict[str, Any]:
    from ..config import load_config
    from ..store import JobStore

    cfg = load_config(ctx.config_path)
    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()
    jobs = store.jobs
    counts: dict[str, int] = {}
    for j in jobs:
        st = str(getattr(j.status, "value", j.status))
        counts[st] = counts.get(st, 0) + 1
    pick = [j for j in jobs if args.verdict == "any" or (
        j.fit_verdict is not None and str(getattr(j.fit_verdict, "value", j.fit_verdict))
        == args.verdict)]
    pick.sort(key=lambda j: j.fit_score or 0, reverse=True)
    return {
        "total": len(jobs), "by_status": counts,
        "top": [{"key": j.job_id, "title": j.title, "company": j.company,
                 "location": j.location, "score_out_of_10": j.fit_score,
                 "verdict": str(getattr(j.fit_verdict, "value", j.fit_verdict) or ""),
                 "status": str(getattr(j.status, "value", j.status))}
                for j in pick[: args.limit]],
    }


def get_spending(_: Empty, ctx: ToolContext) -> dict[str, Any]:
    from ..llm.budget import status

    return status()


def recent_runs(_: Empty, ctx: ToolContext) -> dict[str, Any]:
    from ..chrome_queue import ChromeQueue

    runs = []
    for r in ChromeQueue().list_status(limit=6):
        runs.append({k: r.get(k) for k in ("id", "status", "created_at", "finished_at",
                                           "ad_hoc_companies", "companies", "error",
                                           "error_code", "profile", "summary")})
    return {"pipeline": ctx.pipeline, "chrome_searches": runs}


def diagnose(args: DiagnoseIn, ctx: ToolContext) -> dict[str, Any]:
    from ..config import load_secrets
    from ..onboarding.doctor import CHECKS
    from ..onboarding.system import system_checks

    secrets = load_secrets()
    checks = []
    for fn in CHECKS:
        try:
            label, ok, detail = fn()
        except Exception as e:  # noqa: BLE001 - a check crashing is itself a finding
            label, ok, detail = fn.__name__, False, f"check crashed: {e}"
        checks.append({"check": label, "ok": ok, "detail": detail})
    out: dict[str, Any] = {
        "auth_mode": secrets.auth_mode,
        "doctor": checks,
        "system": system_checks(secrets.claude_cli_path),
    }
    if args.chrome:
        if ctx.on_status:
            ctx.on_status("Testing the Chrome connection…")
        out["chrome"] = commands.run_command("chrome_check")
    return out


def handoff_prompt(args: HandoffIn, ctx: ToolContext) -> dict[str, Any]:
    """A ready-to-paste message for a NEW Claude Code Desktop chat."""
    text = build_handoff(args.problem, args.goal, ctx)
    return {"handoff": text, "note": "Shown to the user with a Copy button."}


def build_handoff(problem: str, goal: str, ctx: ToolContext) -> str:
    from ..chrome_queue import ChromeQueue

    slug = "(pinned folder)" if paths.pinned_profile_dir() else paths.active_slug()
    errors = []
    for r in ChromeQueue().list_status(limit=4):
        if r.get("error"):
            who = ", ".join([*(r.get("ad_hoc_companies") or []), *(r.get("companies") or [])])
            errors.append(f"- run {r.get('id')} ({who or 'postings'}): {str(r['error'])[:300]}")
    pending = [r for r in ChromeQueue().list_status(limit=20) if r.get("status") == "pending"]
    lines = [
        f"I'm using the claude-job-pipeline app in `{paths.REPO_ROOT}` (active profile: "
        f"`{slug}`, files in `profiles/{slug}/`).",
        "",
        f"Problem: {problem}",
    ]
    if goal:
        lines += [f"What I want: {goal}"]
    if ctx.pipeline.get("last_run_error"):
        lines += [f"Last app run error: {str(ctx.pipeline['last_run_error'])[:300]}"]
    if errors:
        lines += ["", "Recent Chrome search errors:", *errors]
    lines += [
        "",
        "Please:",
        "1. Read `README.md` and `prompts/chrome_claude_runbook.md` in that folder.",
        "2. Make sure the Claude in Chrome extension is connected to THIS chat (run "
        "`/chrome` if needed).",
    ]
    if pending:
        lines += [f"3. Run the {len(pending)} waiting Chrome search request(s) in "
                  "`data/chrome_runs/pending/` by following the runbook, using "
                  f"`--profile {slug}` for every command."]
    else:
        lines += ["3. Help me fix the problem above. If it's about Chrome searches, follow "
                  "the runbook to search the companies' careers sites."]
    lines += [
        "",
        "Rules: browse read-only — never log in, apply, fill forms or contact anyone. "
        "Don't invent jobs. Don't edit my resume or profile without asking. Don't commit "
        "or push anything to git.",
    ]
    return "\n".join(lines)


# --- propose tools ---------------------------------------------------------------------


def _wrap(fn: Callable[[], proposals.Proposal], ctx: ToolContext) -> dict[str, Any]:
    try:
        return _card(fn(), ctx)
    except ProposalError as e:
        return {"error": str(e)}


def propose_profile_change(a: ProfileChangeIn, ctx: ToolContext) -> dict[str, Any]:
    return _wrap(lambda: proposals.propose_profile(a.changes, a.reason, ctx.config_path), ctx)


def propose_resume_edit(a: ResumeEditIn, ctx: ToolContext) -> dict[str, Any]:
    return _wrap(lambda: proposals.propose_resume(a.edits, a.reason, ctx.config_path), ctx)


def propose_company_changes(a: CompaniesIn, ctx: ToolContext) -> dict[str, Any]:
    if ctx.on_status and a.add:
        ctx.on_status("Looking up their job boards…")
    return _wrap(lambda: proposals.propose_companies(
        [c.model_dump() for c in a.add], a.remove, a.reason, ctx.config_path), ctx)


def propose_search_settings(a: SettingsIn, ctx: ToolContext) -> dict[str, Any]:
    return _wrap(lambda: proposals.propose_settings(a.changes, a.reason, ctx.config_path), ctx)


def propose_start_search(a: ReasonIn, ctx: ToolContext) -> dict[str, Any]:
    if ctx.pipeline.get("running"):
        return {"error": f"A search is already running ({ctx.pipeline['running']})."}
    return _wrap(lambda: proposals.propose_action("start_search", "Find new jobs", a.reason),
                 ctx)


def propose_premium_search(a: PremiumIn, ctx: ToolContext) -> dict[str, Any]:
    return _wrap(lambda: proposals.propose_action(
        "start_premium_search", "Chrome search: " + ", ".join(a.companies[:4]), a.reason,
        {"companies": a.companies}), ctx)


def propose_clear_chrome_queue(a: ReasonIn, ctx: ToolContext) -> dict[str, Any]:
    return _wrap(lambda: proposals.propose_action(
        "clear_chrome_queue", "Clear stuck Chrome searches", a.reason), ctx)


def propose_command(a: CommandIn, ctx: ToolContext) -> dict[str, Any]:
    return _wrap(lambda: proposals.propose_action("run_command", a.command, a.reason,
                                                  {"command": a.command}), ctx)


TOOLS: list[Tool] = [
    Tool("get_profile", "Read the user's profile (who they are, target roles, skills, "
         "locations, salary, highlights, honest gaps).", Empty, get_profile,
         "Reading your profile…"),
    Tool("get_resume", "Read the user's resume content, including bullet ids and "
         "experience/project keys (needed for resume edits).", Empty, get_resume,
         "Reading your resume…"),
    Tool("get_companies", "Read which companies are searched (job boards, careers sites), "
         "target tiers and excluded companies.", Empty, get_companies,
         "Checking your companies…"),
    Tool("get_search_settings", "Read search settings (job age, locations, remote, cost cap, "
         "open-web queries).", Empty, get_search_settings, "Checking search settings…"),
    Tool("list_jobs", "List found jobs: totals by status and the top matches by score.",
         JobsIn, list_jobs, "Looking at your jobs…"),
    Tool("get_spending", "This month's Claude spending, by feature, and the budget.",
         Empty, get_spending, "Checking spending…"),
    Tool("recent_runs", "The app's current/last run and the last Chrome (premium) searches "
         "with their errors.", Empty, recent_runs, "Checking recent searches…"),
    Tool("diagnose", "Run the app's health checks (Python, Node, resume renderer, UI, "
         "Claude login, profile, resume) and optionally test the Chrome connection.",
         DiagnoseIn, diagnose, "Running health checks…"),
    Tool("handoff_prompt", "Write a ready-to-paste message for a NEW Claude Code Desktop "
         "chat that can fix the problem or run the Chrome search with full context. Use "
         "when you can't fix it from here.", HandoffIn, handoff_prompt,
         "Writing a hand-off for Claude Code…"),
    Tool("propose_profile_change", "Propose changing profile fields. Shown as a card; saved "
         "only if the user clicks Apply.", ProfileChangeIn, propose_profile_change,
         "Preparing a profile change…"),
    Tool("propose_resume_edit", "Propose resume edits (summary, bullets, skills). Use the "
         "user's own facts only — never invent skills, numbers or experience.",
         ResumeEditIn, propose_resume_edit, "Preparing a resume edit…"),
    Tool("propose_company_changes", "Propose adding/removing companies. Job boards are looked "
         "up automatically.", CompaniesIn, propose_company_changes,
         "Preparing a company change…"),
    Tool("propose_search_settings", "Propose changing search settings.", SettingsIn,
         propose_search_settings, "Preparing a settings change…"),
    Tool("propose_start_search", "Propose running 'Find new jobs' (scrape + score new jobs).",
         ReasonIn, propose_start_search, "Preparing a search…"),
    Tool("propose_premium_search", "Propose a premium Chrome search of specific companies' "
         "careers sites (needs Claude in Chrome connected to Claude Code).", PremiumIn,
         propose_premium_search, "Preparing a Chrome search…"),
    Tool("propose_clear_chrome_queue", "Propose clearing stuck/waiting Chrome searches for "
         "this profile.", ReasonIn, propose_clear_chrome_queue, "Preparing a cleanup…"),
    Tool("propose_command", "Propose running one allowlisted maintenance command: "
         + commands.describe(), CommandIn, propose_command, "Preparing a fix…"),
]
BY_NAME = {t.name: t for t in TOOLS}


def call(name: str, args: dict[str, Any], ctx: ToolContext) -> tuple[str, bool]:
    """Validate + run one tool → (JSON result text, is_error)."""
    tool = BY_NAME.get(name)
    if tool is None:
        return json.dumps({"error": f"unknown tool {name!r}"}), True
    try:
        parsed = tool.schema(**(args or {}))
    except Exception as e:  # noqa: BLE001 - bad input → tell Claude, let it retry
        return json.dumps({"error": f"invalid input: {e}"}), True
    if ctx.on_status:
        ctx.on_status(tool.status)
    try:
        result = tool.fn(parsed, ctx)
    except Exception as e:  # noqa: BLE001 - surface to Claude instead of crashing the chat
        return json.dumps({"error": f"{type(e).__name__}: {e}"}), True
    text = json.dumps(result, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + " …(truncated)"
    return text, "error" in result
