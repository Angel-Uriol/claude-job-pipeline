"""Deep-fit — an LLM scores the job on the five-axis rubric.

Input:  one Job that passed pre-filter, plus the candidate's profile.
Output: a `DeepFitResult` containing per-axis scores, weighted fit
        score, verdict (apply/consider/skip), justification, and
        optional honest_bridge_notes.

Runs on Claude (Sonnet-class by default) with the schema handed over as
a forced tool. Axis scores are parsed into a constraint-free mirror, then
clamped into [0, 10] and re-validated into the strict `DeepFitResult`.

Cost target: around a cent per job that passes pre-filter.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..config import Profile
from ..llm import ClaudeClient
from ..models import Job
from .rubric import verdict_from_score, weighted_fit_score

log = logging.getLogger(__name__)


# Generous limit on JD text — deep-fit benefits from full context, and
# the cost is small (16K chars ≈ 4K tokens).
DESCRIPTION_TRUNCATE = 16000


# --- structured output ---------------------------------------------------


class DeepFitScores(BaseModel):
    """Per-axis 0.0–10.0 scores. Validation enforces the bounds.

    Lower bound is 0 (not 1) because Claude sometimes emits 0.x for
    very-bad-fit axes (e.g. function=0 when the role is pure research).
    Rejecting those would silently drop a legitimately-low-fit job.
    """

    stack: float = Field(ge=0.0, le=10.0)
    level: float = Field(ge=0.0, le=10.0)
    function: float = Field(ge=0.0, le=10.0)
    bridgeability: float = Field(ge=0.0, le=10.0)
    desirability: float = Field(ge=0.0, le=10.0)


class DeepFitResult(BaseModel):
    """What Claude returns. The weighted score and verdict are computed
    locally from `scores` — Claude doesn't need to do the arithmetic.

    `justification` is intentionally generous on length — Claude tends to
    overshoot 2-4 sentences, and rejecting valid scoring because the
    response is verbose is a much worse failure than storing a long string.
    """

    scores: DeepFitScores
    justification: str = Field(max_length=4000)
    honest_bridge_notes: str | None = Field(default=None, max_length=4000)


# --- LLM client interface ------------------------------------------------


class DeepFitClient(Protocol):
    """Tests can inject a fake without touching any SDK."""

    def call(self, prompt: str) -> DeepFitResult: ...


# --- tool schema (constraint-free) ----------------------------------------
#
# Claude occasionally emits an axis slightly outside [0, 10] (e.g. 10.5).
# Rejecting the whole score over that is worse than clamping it, so we
# parse into these bound-free mirrors, clamp, then build the strict result.


class _RawDeepFitScores(BaseModel):
    stack: float
    level: float
    function: float
    bridgeability: float
    desirability: float


class _RawDeepFitResponse(BaseModel):
    scores: _RawDeepFitScores
    justification: str
    honest_bridge_notes: str | None = None


def _clamp_unit_score(x: float) -> float:
    """Clamp a model-emitted axis score into the valid [0.0, 10.0] band."""
    return max(0.0, min(10.0, x))


class ClaudeDeepFitClient:
    """Deep-fit via the shared ClaudeClient (API key or Claude Code login)."""

    def __init__(self, *, llm: ClaudeClient, model: str) -> None:
        self._llm = llm
        self.model = model

    def call(self, prompt: str) -> DeepFitResult:
        parsed = self._llm.structured(
            prompt,
            _RawDeepFitResponse,
            model=self.model,
            max_tokens=1500,
            tool_name="submit_fit_score",
            tool_description="Submit the five axis scores, justification and bridge notes.",
        )
        scores = DeepFitScores(
            stack=_clamp_unit_score(parsed.scores.stack),
            level=_clamp_unit_score(parsed.scores.level),
            function=_clamp_unit_score(parsed.scores.function),
            bridgeability=_clamp_unit_score(parsed.scores.bridgeability),
            desirability=_clamp_unit_score(parsed.scores.desirability),
        )
        notes = parsed.honest_bridge_notes
        return DeepFitResult(
            scores=scores,
            justification=parsed.justification[:4000],
            honest_bridge_notes=(notes[:4000] if notes else None),
        )


# --- deep-fitter --------------------------------------------------------


class DeepFitter:
    """Wraps a client + prompt template. Renders profile/job into the prompt."""

    def __init__(self, client: DeepFitClient, prompt_template: str) -> None:
        self.client = client
        self.prompt_template = prompt_template

    @classmethod
    def from_file(cls, client: DeepFitClient, prompt_path: Path) -> DeepFitter:
        return cls(client=client, prompt_template=prompt_path.read_text(encoding="utf-8"))

    def score(self, job: Job, profile: Profile | None = None) -> DeepFitResult:
        """Score one job.

        The candidate block is already baked into `prompt_template` (it is
        rendered from the profile by `prompts.render_prompt`), so `profile`
        is accepted only for call-site compatibility. Per-job fields are
        substituted with `.replace` so braces in a JD never break templating.
        """
        _ = profile
        prompt = (
            self.prompt_template.replace("{company}", job.company)
            .replace("{title}", job.title)
            .replace("{location}", job.location)
            .replace("{extracted}", _format_extracted(job.extracted))
            .replace("{description}", job.description[:DESCRIPTION_TRUNCATE])
        )
        return self.client.call(prompt)


# --- helpers -------------------------------------------------------------


def _format_extracted(extracted: dict[str, Any] | None) -> str:
    """Format the pre-filter's extracted blob for inclusion in the prompt.

    JSON-pretty so Claude can easily read it. Empty/missing → a clear
    sentinel string instead of "None" or "{}".
    """
    if not extracted:
        return "(no pre-filter extraction available)"
    return json.dumps(extracted, indent=2, sort_keys=True)


def compute_fit(scores: DeepFitScores) -> float:
    """Convenience re-export — same as rubric.weighted_fit_score."""
    return weighted_fit_score(scores)


def derive_verdict(score: float, apply_threshold: float, consider_threshold: float) -> Any:
    """Convenience re-export — same as rubric.verdict_from_score."""
    return verdict_from_score(
        score, apply_threshold=apply_threshold, consider_threshold=consider_threshold
    )
