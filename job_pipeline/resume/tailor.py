"""Claude-powered resume tailoring — honest reframing only.

Given a resume spec (built deterministically by `spec_builder`) plus the
target job's title + description, Claude rewrites:
  - the SUMMARY, to foreground what this specific role cares about, and
  - the EXPERIENCE bullets, to mirror the JD's language and ordering.

HARD honesty rule (enforced in the prompt + reinforced here): Claude may
only REPHRASE and REORDER facts already present in the supplied text. It
must preserve every metric/number verbatim and must never invent tools,
skills, employers, or claims. Bullet IDs are preserved so we can merge
the rewrites back precisely. If tailoring fails for any reason, the
caller falls back to the deterministic spec (see generator.py).

The client is injected (Protocol) so tests never touch the real SDK.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..llm import ClaudeClient, LLMError, build_llm
from ..models import Job

log = logging.getLogger(__name__)


# JD text handed to Claude is truncated — tailoring needs the gist, not
# every benefit bullet, and shorter input keeps cost/latency down.
DESCRIPTION_TRUNCATE = 12000


# --- structured output ---------------------------------------------------


class TailoredBullet(BaseModel):
    """One rewritten bullet, keyed by the original bullet id."""

    id: str
    text: str = Field(max_length=600)


class TailoredResume(BaseModel):
    """What Claude returns: a reworded summary + per-id bullet rewrites."""

    summary: str = Field(max_length=2000)
    bullets: list[TailoredBullet]


# --- LLM client interface ------------------------------------------------


class ResumeTailorClient(Protocol):
    """Tests inject a fake; production uses ClaudeResumeTailorClient."""

    def tailor(self, prompt: str) -> TailoredResume: ...


class ClaudeResumeTailorClient:
    """Tailoring via the shared ClaudeClient (API key or Claude Code login)."""

    def __init__(self, *, llm: ClaudeClient, model: str) -> None:
        self._llm = llm
        self.model = model

    def tailor(self, prompt: str) -> TailoredResume:
        return self._llm.structured(
            prompt,
            TailoredResume,
            model=self.model,
            max_tokens=3000,
            tool_name="submit_tailored_resume",
            tool_description=(
                "Submit the honestly-reframed resume. Rephrase and reorder ONLY; "
                "preserve every number verbatim; never invent skills or claims. "
                "Return the SAME bullet ids you were given."
            ),
        )


# --- tailorer ------------------------------------------------------------


class ResumeTailorer:
    """Wraps a client + prompt template; tailors a render spec in place.

    Only `summary` and the experience bullets' `text` are rewritten — ids,
    structure, projects, education, skills, and header are untouched.
    """

    def __init__(self, client: ResumeTailorClient, prompt_template: str) -> None:
        self.client = client
        self.prompt_template = prompt_template

    @classmethod
    def from_file(cls, client: ResumeTailorClient, prompt_path: Path) -> ResumeTailorer:
        return cls(client=client, prompt_template=prompt_path.read_text(encoding="utf-8"))

    def tailor_spec(self, spec: dict[str, Any], job: Job) -> dict[str, Any]:
        """Return a NEW spec with the summary + experience bullets reworded.

        Raises on client failure — the caller (generate_resume) catches and
        falls back to the original deterministic spec.
        """
        # Gather the current experience bullets (id + text) across all
        # experience entries so Claude sees the full set to rephrase.
        current_bullets: list[dict[str, str]] = []
        for exp in spec.get("experiences", []):
            for b in exp.get("bullets", []):
                current_bullets.append({"id": b["id"], "text": b["text"]})

        prompt = (
            self.prompt_template.replace("{job_title}", job.title)
            .replace("{job_description}", (job.description or "")[:DESCRIPTION_TRUNCATE])
            .replace("{summary}", str(spec.get("summary", "")))
            .replace("{bullets_json}", json.dumps(current_bullets, indent=2, ensure_ascii=False))
        )
        tailored = self.client.tailor(prompt)

        rewrites = {b.id: b.text for b in tailored.bullets}

        new_spec = dict(spec)  # shallow copy; we replace summary + experiences
        if tailored.summary.strip():
            new_spec["summary"] = tailored.summary.strip()

        new_experiences: list[dict[str, Any]] = []
        for exp in spec.get("experiences", []):
            new_bullets = [
                {**b, "text": rewrites.get(b["id"], b["text"])}
                for b in exp.get("bullets", [])
            ]
            new_experiences.append({**exp, "bullets": new_bullets})
        new_spec["experiences"] = new_experiences
        return new_spec


# --- factory -------------------------------------------------------------


def build_resume_tailorer(
    *,
    enabled: bool,
    model: str,
    prompt_template: str,
    llm: ClaudeClient | None = None,
) -> ResumeTailorer | None:
    """Build a tailorer if enabled AND Claude is reachable; else None.

    Returning None makes resume generation fall back to the deterministic
    template — a graceful skip, never an abort.
    """
    if not enabled:
        return None
    if llm is None:
        try:
            llm = build_llm()
        except LLMError as e:
            log.warning("Resume tailoring disabled: %s", e)
            return None
    client = ClaudeResumeTailorClient(llm=llm, model=model)
    return ResumeTailorer(client=client, prompt_template=prompt_template)
