"""Setup step 3: pick target companies and find their job boards.

Claude suggests companies that fit the finished profile; the user prunes or
adds; then `ats.find_boards` checks which ones have a scrapeable public
board. Companies with a board become scrapers; the rest go under `no_api`
so the digest can generate a Chrome Claude prompt for them.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..llm import ClaudeClient
from .ats import find_boards
from .interview import ProfileDraft


class CompanySuggestion(BaseModel):
    name: str
    tier: int = Field(ge=1, le=3, description="1 = dream fit, 2 = strong, 3 = worth a look")
    why: str = Field(default="", description="One short line on why it fits.")
    careers_url: str = Field(default="", description="Public careers page URL if known, else ''.")
    aliases: list[str] = Field(default_factory=list)


class CompanySuggestions(BaseModel):
    companies: list[CompanySuggestion]


SUGGEST_PROMPT = """Suggest {n} employers this job seeker should target.

Candidate profile:
{profile}

Guidelines:
- Real companies that actively hire for the candidate's target functions and
  levels, in or remote-friendly to their locations.
{sponsorship}- Mix well-known employers with strong mid-size / growth companies.
- Always include their dream companies if any: {dream}
- Never include these: {excluded}
- tier 1 = dream / best fit, 2 = strong fit, 3 = worth a look.
- careers_url: the company's public careers page if you are confident of it,
  otherwise an empty string. Never guess a URL.
- aliases: other names a job board might use (e.g. "Meta" -> ["Facebook"])."""


def suggest_companies(
    draft: ProfileDraft, *, llm: ClaudeClient, model: str, n: int = 30
) -> list[dict[str, Any]]:
    """Ask Claude for target companies. Returns plain dicts for the UI."""
    sponsorship = (
        "- The candidate needs visa sponsorship: prefer employers known to sponsor.\n"
        if draft.needs_sponsorship
        else ""
    )
    prompt = SUGGEST_PROMPT.format(
        n=n,
        profile=json.dumps(draft.model_dump(exclude_none=True), indent=2, ensure_ascii=False),
        sponsorship=sponsorship,
        dream=", ".join(draft.dream_companies or []) or "(none given)",
        excluded=", ".join(draft.excluded_companies or []) or "(none)",
    )
    out = llm.structured(
        prompt,
        CompanySuggestions,
        model=model,
        max_tokens=4000,
        tool_name="submit_companies",
        tool_description="Submit the list of suggested target companies.",
    )
    excluded = {c.lower() for c in (draft.excluded_companies or [])}
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for c in out.companies:
        key = c.name.strip().lower()
        if not key or key in seen or key in excluded:
            continue
        seen.add(key)
        rows.append({**c.model_dump(), "selected": True})
    return rows


def verify_companies(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach `platform` / `slug` / `jobs` to each selected company dict."""
    selected = [c for c in companies if c.get("selected", True)]
    results = find_boards((c["name"], c.get("aliases") or []) for c in selected)
    by_name = {r.name: r for r in results}
    out: list[dict[str, Any]] = []
    for c in companies:
        r = by_name.get(c["name"])
        if r is not None:
            out.append({**c, "platform": r.platform, "slug": r.slug, "jobs": r.jobs})
        else:
            out.append(c)
    return out
