"""Pre-filter — cheap structured extraction via a fast Claude model.

Input: one Job's title + location + (truncated) description.
Output: a `PreFilterExtraction` containing structured fields and a
        pass/skip verdict.

Cost target: a fraction of a cent per job on the fast (Haiku-class) model.

Two layers of skip logic:
1. The LLM's own verdict (it may decide "skip" based on the prompt's
   guidance).
2. `apply_hard_skip_rules` — a deterministic post-check independent
   of the LLM, so we never let through a Director role just because
   the model got chatty.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from ..config import Profile
from ..llm import ClaudeClient
from ..models import Job

log = logging.getLogger(__name__)


# JD text passed to the LLM is truncated to this many characters. The
# pre-filter rarely needs more — and tokenized cost scales with input
# length, so capping keeps the per-job cost predictable.
DESCRIPTION_TRUNCATE = 8000


# Fallback title tokens when no profile is passed (tests / old callers).
DEFAULT_EXCLUDED_TITLES = ("director", "principal", "staff", "vp", "head of")

# NOTE: "the JD centers on a skill the candidate lacks" is NOT a hard skip —
# the deep-fit bridgeability axis handles it (see prompts/deep_fit.txt).


# --- structured-output schema --------------------------------------------


class PreFilterExtraction(BaseModel):
    """The JSON shape the LLM must return.

    Pydantic field names match the prompt's instructions exactly; the
    schema is handed to Claude as a forced tool.
    """

    level: Literal["L3", "L4", "L5", "L6+", "unknown"]
    years_required: int | None = Field(
        default=None,
        description="Total YOE the JD asks for; null when unspecified.",
    )
    degree_required: Literal["BS", "MS", "PhD", "unknown"]
    visa_stance: Literal["sponsors", "no_sponsorship", "us_only_clearance", "unknown"]
    tech_stack: list[str]
    verdict: Literal["pass", "skip"]
    reason: str = Field(max_length=200)


# --- LLM client interface ------------------------------------------------


class PreFilterClient(Protocol):
    """Minimal interface every pre-filter LLM client must satisfy.

    Production: `ClaudePreFilterClient` wraps the shared Claude client.
    Tests: a small fake that returns canned `PreFilterExtraction`s.
    """

    def call(self, prompt: str) -> PreFilterExtraction: ...


class ClaudePreFilterClient:
    """Pre-filter via the shared ClaudeClient (API key or Claude Code login)."""

    def __init__(self, *, llm: ClaudeClient, model: str) -> None:
        self._llm = llm
        self.model = model

    def call(self, prompt: str) -> PreFilterExtraction:
        return self._llm.structured(
            prompt,
            PreFilterExtraction,
            model=self.model,
            max_tokens=800,
            tool_name="submit_pre_filter",
            tool_description="Submit the extracted fields and the pass/skip verdict.",
        )


# --- pre-filterer -------------------------------------------------------


class PreFilterer:
    """Wraps a client + prompt template. One per pipeline run."""

    def __init__(self, client: PreFilterClient, prompt_template: str) -> None:
        self.client = client
        self.prompt_template = prompt_template

    @classmethod
    def from_file(cls, client: PreFilterClient, prompt_path: Path) -> PreFilterer:
        return cls(client=client, prompt_template=prompt_path.read_text(encoding="utf-8"))

    def score(self, job: Job) -> PreFilterExtraction:
        prompt = (
            self.prompt_template.replace("{title}", job.title)
            .replace("{location}", job.location)
            .replace("{description}", job.description[:DESCRIPTION_TRUNCATE])
        )
        return self.client.call(prompt)


# --- hard skip rules ----------------------------------------------------


def apply_hard_skip_rules(
    job: Job, extraction: PreFilterExtraction, profile: Profile | None = None
) -> tuple[bool, str]:
    """Deterministic post-check on the LLM's extraction.

    Returns (should_skip, reason). Independent of the LLM's own verdict so
    a chatty model never sneaks a clearly-wrong role through.

    Rules (driven by the profile when given):
      - years_required above the profile's threshold   → too senior
      - PhD + Research/Applied Scientist title          → wrong function
      - clearance / no sponsorship, if sponsorship needed → can't be hired
      - title contains an excluded word (word match)    → wrong level
    """
    # Local import: prompts imports config, which is fine, but keeping the
    # scoring module import-light keeps tests fast.
    from ..prompts import max_years_required

    title_lower = job.title.lower()
    max_years = max_years_required(profile) if profile else 8
    excluded = profile.excluded_titles if profile else list(DEFAULT_EXCLUDED_TITLES)
    needs_sponsorship = profile.needs_sponsorship if profile else True

    if extraction.years_required is not None and extraction.years_required >= max_years:
        return True, f"requires {extraction.years_required}+ years experience"

    if extraction.degree_required == "PhD" and (
        "research scientist" in title_lower or "applied scientist" in title_lower
    ):
        return True, "PhD-required research scientist role"

    if extraction.visa_stance == "us_only_clearance":
        return True, "requires US citizenship or security clearance"

    if needs_sponsorship and extraction.visa_stance == "no_sponsorship":
        return True, "explicitly offers no visa sponsorship"

    for token in excluded:
        if token and re.search(rf"\b{re.escape(token.lower())}\b", title_lower):
            return True, f"title contains '{token}'"

    return False, ""
