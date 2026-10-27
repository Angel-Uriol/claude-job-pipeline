"""Natural-language → Chrome Claude run spec, via a fast Claude model.

The Discover view's chat box sends free text ("search Stripe and Notion for
ML-infra roles posted this week") here. We ask the fast Claude model to turn
it into the SAME fields the Run Chrome Claude modal uses, then split the named
companies into ones already tracked (`config.scrapers.no_api`) vs ad-hoc ones
the watcher will find a careers page for. This only PROPOSES a run — the
user reviews + confirms in the modal.

Mirrors the structured-output pattern in `scoring/pre_filter.py`: an
injectable client Protocol so tests can feed canned results without an LLM.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from .config import Config
from .llm import ClaudeClient

log = logging.getLogger(__name__)

VALID_SCOPES = ("selected", "new", "mix")


# --- structured-output schema -------------------------------------------


class ParsedChromeRun(BaseModel):
    """The JSON shape Claude must return (nullable where 'unspecified')."""

    scope: Literal["selected", "new", "mix"]
    companies: list[str]  # every company the user named (tracked or not)
    posted_within_days: int | None  # 1 | 3 | 7 | 30, or null = any
    score_after: bool
    extra_notes: str  # extra role/level/skill/location specifics; "" if none


# --- client interface (mirrors PreFilterClient) -------------------------


class ChromeNLClient(Protocol):
    def call(self, *, system: str, user: str) -> ParsedChromeRun: ...


class ClaudeChromeNLClient:
    """Request parsing via the shared ClaudeClient."""

    def __init__(self, *, llm: ClaudeClient, model: str) -> None:
        self._llm = llm
        self.model = model

    def call(self, *, system: str, user: str) -> ParsedChromeRun:
        return self._llm.structured(
            user,
            ParsedChromeRun,
            model=self.model,
            system=system,
            max_tokens=600,
            tool_name="submit_run",
            tool_description="Submit the structured browser-search run.",
        )


# --- prompt + parse ------------------------------------------------------


SYSTEM_TEMPLATE = """You convert a job seeker's free-text request into a structured
"browser job search" run. Return ONLY the structured fields.

scope:
- "selected" — search specific companies the seeker already tracks.
- "new" — discovery: find roles at companies NOT already tracked.
- "mix" — both.
Pick "selected" when they name only tracked companies; "new" when they ask to
discover/find new companies or roles broadly; "mix" when both, or when unclear
but broad.

Tracked companies (use the EXACT lowercase name when the request matches one):
{known}

companies: every company the request names, lowercased (tracked or not — we
  sort out which afterward).
posted_within_days: 1 (today/24h), 3, 7 (this week), 30 (this month), or null
  when no recency is mentioned.
score_after: true unless they say not to score / just collect.
extra_notes: any role/level/skill/location emphasis to pass along (e.g.
  "prioritize Iceberg + Spark", "mid-level IC only", "remote") — "" if none."""


def build_system_prompt(cfg: Config) -> str:
    known = [e.name for e in cfg.scrapers.no_api if e.platform != "linkedin_search"]
    return SYSTEM_TEMPLATE.format(known=", ".join(known) if known else "(none)")


def parse_request(text: str, *, cfg: Config, client: ChromeNLClient) -> dict[str, Any]:
    """Parse `text` into a proposed run dict.

    Splits the model's `companies` into `companies` (matched to tracked
    `no_api` names) and `ad_hoc_companies` (named but not tracked — the
    watcher will find their careers page). Returns the same field names the
    /run endpoint + modal use.
    """
    parsed = client.call(system=build_system_prompt(cfg), user=text.strip())

    known_lower = {
        e.name.lower(): e.name
        for e in cfg.scrapers.no_api
        if e.platform != "linkedin_search"
    }
    companies: list[str] = []
    ad_hoc: list[str] = []
    seen: set[str] = set()
    for raw in parsed.companies:
        name = raw.strip()
        key = name.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        if key in known_lower:
            companies.append(known_lower[key])
        else:
            ad_hoc.append(name)

    posted_within = parsed.posted_within_days
    if posted_within is not None and posted_within <= 0:
        posted_within = None
    scope = parsed.scope if parsed.scope in VALID_SCOPES else "mix"

    return {
        "scope": scope,
        "companies": companies,
        "ad_hoc_companies": ad_hoc,
        "posted_within": posted_within,
        "score_after": bool(parsed.score_after),
        "extra_notes": parsed.extra_notes.strip(),
    }
