"""The LLM-run setup questionnaire.

Claude interviews the job seeker, one question at a time, until it has every
field the pipeline needs (the ``profile:`` section of config.yaml plus the
location tokens the scrape filter uses). Each turn:

  1. We send Claude the current draft, which required fields are still
     missing, a digest of the parsed resume, and the recent conversation.
  2. Claude returns ``{updates, next_question, done}`` as structured output.
  3. We merge ``updates`` into the draft in code, and decide ``done`` in code
     too — the model can say "done", but we keep asking until every required
     field is filled. So a chatty model can't end setup early.

State lives in ``InterviewState`` and is saved to ``profile/.interview.json``
after every turn, so the web page or terminal can resume where it stopped.
The same engine powers both the Setup page and ``job-pipeline setup``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .. import paths
from ..llm import ClaudeClient

# How many past messages Claude sees each turn (the draft carries the rest).
HISTORY_WINDOW = 16


# --- the draft profile ---------------------------------------------------


class FunctionDraft(BaseModel):
    name: str
    tier: Literal["A", "B", "C"] = "A"
    notes: str = ""


class ProfileDraft(BaseModel):
    """Everything the questionnaire collects. Mirrors `config.Profile`, plus
    the pipeline-side location fields. All optional while in progress."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    location: str | None = None
    headline: str | None = None
    years_experience: int | None = None
    work_authorization: str | None = None
    needs_sponsorship: bool | None = None
    target_levels: list[str] | None = None
    target_functions: list[FunctionDraft] | None = None
    must_have_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    excluded_titles: list[str] | None = None
    location_preference: str | None = None
    location_tokens: list[str] | None = None
    allow_remote: bool | None = None
    salary_floor: str | None = None
    highlights: list[str] | None = None
    honest_gaps: list[str] | None = None
    deal_breakers: list[str] | None = None
    dream_companies: list[str] | None = None
    excluded_companies: list[str] | None = None


# Field -> the plain-English thing we still need (shown to Claude + the UI).
REQUIRED_FIELDS: dict[str, str] = {
    "name": "full name",
    "email": "email for the resume header",
    "years_experience": "years of professional experience",
    "target_functions": "kinds of roles they want, ranked by fit",
    "target_levels": "seniority levels they're targeting",
    "location_preference": "where they're willing to work (cities / remote)",
    "location_tokens": "location match words derived from their answer",
    "allow_remote": "whether fully-remote roles are OK",
    "work_authorization": "work authorization",
    "needs_sponsorship": "whether they need visa sponsorship",
    "preferred_skills": "core skills / tech stack",
    "honest_gaps": "skills they do NOT have in production (can be 'none')",
}

# Nice to have — Claude asks about these after the required ones, briefly.
OPTIONAL_FIELDS: dict[str, str] = {
    "excluded_titles": "title words that mean 'too senior / wrong role'",
    "salary_floor": "minimum acceptable compensation",
    "highlights": "2-5 proudest, quantified accomplishments",
    "deal_breakers": "anything that rules a job out",
    "dream_companies": "companies they'd be most excited about",
    "excluded_companies": "companies to never show (current employer, etc.)",
}


def missing_required(draft: ProfileDraft) -> list[str]:
    out: list[str] = []
    for f in REQUIRED_FIELDS:
        v = getattr(draft, f)
        if v is None or (isinstance(v, (list, str)) and not v and f != "honest_gaps"):
            out.append(f)
    return out


# --- what Claude returns each turn --------------------------------------


class InterviewTurn(BaseModel):
    updates: ProfileDraft = Field(
        default_factory=ProfileDraft,
        description="ONLY fields the user's latest answer settles. Lists are full replacements.",
    )
    next_question: str = Field(
        description="The next single question to ask (or a short wrap-up when done)."
    )
    done: bool = Field(description="True when nothing important is left to ask.")


# --- persisted state ------------------------------------------------------


class Message(BaseModel):
    role: Literal["assistant", "user"]
    text: str


class InterviewState(BaseModel):
    draft: ProfileDraft = Field(default_factory=ProfileDraft)
    messages: list[Message] = Field(default_factory=list)
    done: bool = False
    # Parsed resume (ResumeContent as a dict) once uploaded.
    resume_content: dict[str, Any] | None = None
    # Company step: suggestions [{name, tier, why}] and board probe results.
    companies: list[dict[str, Any]] = Field(default_factory=list)

    def missing(self) -> list[str]:
        return missing_required(self.draft)

    def save(self, path: Path | None = None) -> None:
        p = path or paths.interview_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> InterviewState:
        p = path or paths.interview_state_path()
        if not p.exists():
            return cls()
        return cls.model_validate_json(p.read_text(encoding="utf-8"))

    @classmethod
    def reset(cls, path: Path | None = None) -> InterviewState:
        p = path or paths.interview_state_path()
        p.unlink(missing_ok=True)
        return cls()


# --- prompts ------------------------------------------------------------

SYSTEM_PROMPT = """You are a warm, efficient career coach running a short intake
interview for a job-search tool. Your job is to fill in the candidate's
profile so the tool can find and score jobs for them.

Rules:
- Ask ONE clear question at a time (you may group 2 tightly related things,
  e.g. "work authorization and whether you need sponsorship").
- Prefer what the resume already says; confirm it briefly instead of asking
  from scratch.
- Ask about REQUIRED fields first, then a few optional ones, then finish.
- Extract structured values from the user's answers into `updates`. Only put
  fields the latest answer actually settles. For list fields send the full
  updated list.
- target_functions: name each kind of role and give it a tier — A = best fit,
  B = good fit, C = stretch / career pivot.
- location_tokens: lowercase substrings that would appear in a matching job
  location string. Include city names, metro names, state names and state
  abbreviations written like ", tx". Do NOT include "remote" here — set
  allow_remote instead.
- excluded_titles: lowercase single words or short phrases (e.g. "director",
  "principal", "intern").
- HONESTY: never invent experience, skills, metrics or credentials. If the
  user is unsure, record only what they actually said. honest_gaps can be
  an empty list if they say they have none.
- Keep questions short and friendly. No lectures, no markdown headings.
- Set done=true only when the required fields are filled and you've asked
  about the optional ones (skips are fine). Then next_question is a one-line
  wrap-up telling them the next step is choosing target companies."""


def _resume_digest(content: dict[str, Any] | None) -> str:
    """Compact resume summary for the interviewer (not the full YAML)."""
    if not content:
        return "(no resume uploaded)"
    h = content.get("header", {})
    lines = [
        f"Name: {h.get('name', '')}  Email: {h.get('email', '')}  "
        f"Location: {h.get('location', '')}",
        f"Summary: {next(iter((content.get('summaries') or {}).values()), '')}",
    ]
    for exp in (content.get("experiences") or {}).values():
        lines.append(
            f"- {exp.get('title')} @ {exp.get('company')} ({exp.get('start')} – {exp.get('end')})"
        )
        for b in (exp.get("bullets") or [])[:4]:
            lines.append(f"    • {b.get('text', '')}")
    skills = content.get("skills") or {}
    flat = [s for group in skills.values() for s in (group or [])]
    if flat:
        lines.append(f"Skills: {', '.join(flat)}")
    for ed in content.get("education") or []:
        lines.append(f"Education: {ed.get('degree')} — {ed.get('school')} ({ed.get('end')})")
    return "\n".join(lines)


def _turn_prompt(state: InterviewState) -> str:
    missing = state.missing()
    missing_txt = "\n".join(f"- {f}: {REQUIRED_FIELDS[f]}" for f in missing) or "(none)"
    optional_txt = "\n".join(
        f"- {f}: {desc}" for f, desc in OPTIONAL_FIELDS.items()
        if getattr(state.draft, f) is None
    ) or "(none)"
    history = state.messages[-HISTORY_WINDOW:]
    convo = "\n".join(f"{m.role.upper()}: {m.text}" for m in history) or "(interview not started)"
    draft = state.draft.model_dump(exclude_none=True)
    return (
        "# Resume digest\n"
        f"{_resume_digest(state.resume_content)}\n\n"
        "# Profile draft so far\n"
        f"{json.dumps(draft, indent=2, ensure_ascii=False)}\n\n"
        "# Required fields still missing\n"
        f"{missing_txt}\n\n"
        "# Optional fields not asked yet\n"
        f"{optional_txt}\n\n"
        "# Conversation so far\n"
        f"{convo}\n\n"
        "Extract updates from the user's latest answer (if any) and ask the next question."
    )


# --- engine ---------------------------------------------------------------


def prefill_from_resume(state: InterviewState, content: dict[str, Any]) -> None:
    """Seed the draft with header facts from a parsed resume (no LLM call)."""
    state.resume_content = content
    h = content.get("header") or {}
    d = state.draft
    d.name = d.name or (h.get("name") or None)
    d.email = d.email or (h.get("email") or None)
    d.phone = d.phone or (h.get("phone") or None)
    d.linkedin = d.linkedin or (h.get("linkedin") or None)
    d.location = d.location or (h.get("location") or None)
    if d.preferred_skills is None:
        skills = [s for g in (content.get("skills") or {}).values() for s in (g or [])]
        d.preferred_skills = [s.lower() for s in skills][:25] or None


def _merge(draft: ProfileDraft, updates: ProfileDraft) -> None:
    """Copy every field the turn actually set (None = untouched)."""
    for field_name in updates.model_fields_set:
        value = getattr(updates, field_name)
        if value is not None:
            setattr(draft, field_name, value)


def step(
    state: InterviewState,
    user_text: str | None,
    *,
    llm: ClaudeClient,
    model: str,
) -> InterviewState:
    """Run one interview turn and return the updated state (also saved).

    `user_text=None` (or empty) asks the opening / next question without
    recording a user message — used to start or resume the interview.
    """
    if user_text and user_text.strip():
        state.messages.append(Message(role="user", text=user_text.strip()))

    turn = llm.structured(
        _turn_prompt(state),
        InterviewTurn,
        model=model,
        system=SYSTEM_PROMPT,
        max_tokens=1500,
        tool_name="submit_turn",
        tool_description="Submit extracted profile updates and the next question.",
    )
    _merge(state.draft, turn.updates)

    missing = state.missing()
    state.done = bool(turn.done and not missing)
    question = turn.next_question.strip()
    if turn.done and missing:
        # The model wanted to stop early — keep going on what's missing.
        question = f"Before we wrap up — could you tell me your {REQUIRED_FIELDS[missing[0]]}?"
    state.messages.append(Message(role="assistant", text=question))
    state.save()
    return state


def draft_to_profile_dict(draft: ProfileDraft) -> dict[str, Any]:
    """Draft -> the `profile:` mapping of config.yaml (pipeline-only fields removed)."""
    d = draft.model_dump(exclude_none=True)
    for k in ("location_tokens", "allow_remote", "excluded_companies"):
        d.pop(k, None)
    d.setdefault("name", "")
    d.setdefault("email", "")
    return d
