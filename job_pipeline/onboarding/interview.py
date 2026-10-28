"""The LLM-run setup chat: resume → three rounds of questions → profile.

The chat moves through phases (``InterviewState.phase``):

    start ──► upload ──┐                     (have a resume → drop it in)
      └────────────────┴► round1 ──► market1 ──► round2 ──► market2 ──► round3 ──► done
                          About you   live jobs   What the    live jobs   Sharpen
                                                  market asks

* **start** — "Do you have a resume?" (no LLM call). No resume → Claude also
  collects industry, experience, projects and education, and drafts a resume
  from them at the end (resume_builder.py).
* **round1** — the basics: target roles (tiered), level, locations, work
  authorization, skills, honest gaps.
* **market1 / market2** — real postings are scanned (market.py) and the
  requirements they share are summarized for the next round.
* **round2** — "Several postings I found require X — have you used it?"
* **round3** — a second, sharper scan → final adjustments.

Each turn Claude returns ``{updates, next_question, options, multi_select,
done}``. Code — not the model — decides when a round ends: round 1 needs every
required field; rounds 2 and 3 are capped at a few questions. Questions are
mostly multiple choice (``options``), with free text always allowed.

State is saved to ``profile/.interview.json`` after every turn so the page
or terminal can resume. The same engine powers the web chat and
``job-pipeline setup``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .. import paths
from ..llm import ClaudeClient

# How many past messages Claude sees each turn (the draft carries the rest).
HISTORY_WINDOW = 16

Phase = Literal["start", "upload", "round1", "market1", "round2", "market2", "round3", "done"]

#: Which of the three rounds a phase belongs to (for the progress header).
ROUND_OF: dict[str, int] = {
    "start": 1, "upload": 1, "round1": 1, "market1": 2, "round2": 2,
    "market2": 3, "round3": 3, "done": 3,
}
ROUND_NAMES = {1: "About you", 2: "What the market asks", 3: "Sharpen"}
# Max questions Claude may ask in the market-driven rounds.
ROUND_LIMITS = {"round2": 5, "round3": 4}
# Safety valve: round 1 ends after this many questions even if a field is
# still missing (it can be edited on the review screen).
ROUND1_MAX = 18

START_TEXT = (
    "Hi! I'm your job-search co-pilot. I'll ask a few quick questions, look at real "
    "openings while we talk, and set everything up for you.\n\n"
    "First things first — do you have a resume you can share?"
)
START_OPTIONS = ["Yes, I'll upload it", "No, let's build one together"]
UPLOAD_TEXT = (
    "Perfect — drop it here (PDF, DOCX or TXT). "
    "I'll read it and only ask about what's missing."
)
UPLOAD_OPTIONS = ["Actually, I don't have one"]


# --- the draft profile ---------------------------------------------------


class FunctionDraft(BaseModel):
    name: str
    tier: Literal["A", "B", "C"] = "A"
    notes: str = ""


class ExperienceDraft(BaseModel):
    company: str
    title: str
    location: str = ""
    start: str = ""
    end: str = ""
    highlights: list[str] = Field(default_factory=list)


class ProjectDraft(BaseModel):
    name: str
    description: str = ""
    stack: str = ""


class EducationDraft(BaseModel):
    school: str
    degree: str = ""
    end: str = ""


class ProfileDraft(BaseModel):
    """Everything the chat collects. Mirrors `config.Profile`, plus
    pipeline-side fields (location tokens, search keywords) and — when the
    user has no resume — the raw material for drafting one."""

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    location: str | None = None
    headline: str | None = None
    industry: str | None = None
    years_experience: int | None = None
    work_authorization: str | None = None
    needs_sponsorship: bool | None = None
    target_levels: list[str] | None = None
    target_functions: list[FunctionDraft] | None = None
    search_keywords: list[str] | None = None
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
    # Resume-building material (no-resume path).
    experience_entries: list[ExperienceDraft] | None = None
    projects: list[ProjectDraft] | None = None
    education: list[EducationDraft] | None = None


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

# Extra essentials when we're building the resume from the chat.
BUILD_FIELDS: dict[str, str] = {
    "industry": "industry / field they work in",
    "experience_entries": "work experience (company, title, dates, what they did) — or 'none'",
    "projects": "projects worth showing (school, personal, freelance) — or 'none'",
    "education": "education",
}

# Nice to have — asked briefly after the required ones.
OPTIONAL_FIELDS: dict[str, str] = {
    "excluded_titles": "title words that mean 'too senior / wrong role'",
    "salary_floor": "minimum acceptable compensation",
    "highlights": "2-5 proudest, quantified accomplishments",
    "deal_breakers": "anything that rules a job out",
    "dream_companies": "companies they'd be most excited about",
    "excluded_companies": "companies to never show (current employer, etc.)",
}

# Fields where an empty list is a valid, deliberate answer.
_EMPTY_OK = {"honest_gaps", "experience_entries", "projects", "education"}


def missing_required(draft: ProfileDraft, build_mode: bool = False) -> list[str]:
    fields = {**REQUIRED_FIELDS, **(BUILD_FIELDS if build_mode else {})}
    out: list[str] = []
    for f in fields:
        v = getattr(draft, f)
        if v is None or (isinstance(v, (list, str)) and not v and f not in _EMPTY_OK):
            out.append(f)
    return out


def field_label(f: str) -> str:
    return {**REQUIRED_FIELDS, **BUILD_FIELDS}.get(f, f)


# --- what Claude returns each turn --------------------------------------


class InterviewTurn(BaseModel):
    updates: ProfileDraft = Field(
        default_factory=ProfileDraft,
        description="ONLY fields the user's latest answer settles. Lists are full replacements.",
    )
    next_question: str = Field(
        description="The next single question (or a one-line wrap-up when done)."
    )
    options: list[str] = Field(
        default_factory=list,
        description="2-6 short answer choices for the question. Empty only for "
        "truly open questions (name, email, a description).",
    )
    multi_select: bool = Field(default=False, description="True if several options may apply.")
    done: bool = Field(description="True when nothing important is left to ask in this round.")


# --- persisted state ------------------------------------------------------


class Message(BaseModel):
    role: Literal["assistant", "user"]
    text: str
    options: list[str] = Field(default_factory=list)
    multi: bool = False
    # text | upload (show a drop zone) | market (job cards) | note (status line)
    kind: Literal["text", "upload", "market", "note"] = "text"
    jobs: list[dict[str, Any]] = Field(default_factory=list)


class InterviewState(BaseModel):
    draft: ProfileDraft = Field(default_factory=ProfileDraft)
    messages: list[Message] = Field(default_factory=list)
    phase: Phase = "start"
    resume_mode: Literal["unknown", "upload", "build"] = "unknown"
    done: bool = False
    # Parsed (or chat-built) resume as a ResumeContent dict.
    resume_content: dict[str, Any] | None = None
    # Company step: [{name, tier, why, selected, platform?, slug?, jobs?}]
    companies: list[dict[str, Any]] = Field(default_factory=list)
    # One entry per market scan: {round, summary, jobs, requirements}.
    market: list[dict[str, Any]] = Field(default_factory=list)
    # Questions Claude has asked in the current phase (caps rounds 2-3).
    round_questions: int = 0

    @property
    def build_mode(self) -> bool:
        return self.resume_mode == "build"

    @property
    def round(self) -> int:
        return ROUND_OF[self.phase]

    def missing(self) -> list[str]:
        return missing_required(self.draft, self.build_mode)

    def say(self, text: str, **kw: Any) -> None:
        self.messages.append(Message(role="assistant", text=text, **kw))

    def save(self, path: Path | None = None) -> None:
        p = path or paths.interview_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> InterviewState:
        p = path or paths.interview_state_path()
        if not p.exists():
            return cls()
        try:
            return cls.model_validate_json(p.read_text(encoding="utf-8"))
        except ValueError:
            return cls()  # older/corrupt state file → start fresh

    @classmethod
    def reset(cls, path: Path | None = None) -> InterviewState:
        p = path or paths.interview_state_path()
        p.unlink(missing_ok=True)
        return cls()


# --- prompts ------------------------------------------------------------

SYSTEM_PROMPT = """You are a warm, upbeat career co-pilot running a short intake chat
for a job-search tool. You fill in the candidate's profile so the tool can find
and score real jobs for them.

Style:
- ONE short question at a time (you may pair 2 tightly related things).
- Prefer MULTIPLE CHOICE: give 2-6 short `options` (a few words each) that
  cover the likely answers. The user can always type their own answer, so
  don't add an "Other" option. Use free text (empty options) only for
  things like name, email, or "describe what you did".
- Set multi_select=true when several options can apply (skills, locations).
- Friendly and concise. No markdown headings, no lists in questions.
- Confirm what the resume already says instead of asking from scratch.

Extraction:
- Put into `updates` ONLY fields the latest answer settles. Lists are full
  replacements — send the whole updated list.
- target_functions: each kind of role with a tier — A best fit, B good fit,
  C stretch / career pivot.
- search_keywords: 3-6 job-title phrases to search for (e.g. "data engineer",
  "analytics engineer"), derived from target_functions.
- location_tokens: lowercase substrings that appear in matching job location
  strings (cities, metro names, state names, abbreviations like ", tx"). Never
  "remote" — set allow_remote instead.
- excluded_titles: lowercase words like "director", "intern".
- HONESTY: never invent experience, skills, metrics or credentials. Record
  only what the user actually said. honest_gaps may be an empty list."""

ROUND_GUIDE = {
    "round1": """ROUND 1 — About you. Cover the required fields first, then briefly
ask about a couple of optional ones. Set done=true when the required list is
empty and you've touched the optional ones (skips are fine); then
next_question is a one-line wrap-up saying you'll now look at real openings.""",
    "round1_build": """The candidate has NO resume, so we'll draft one together.
Early on, ask their industry, then work experience (company, title, rough
dates, 1-3 things they did — with numbers if they have them), notable
projects, and education. Store them in experience_entries / projects /
education. 'None' is a fine answer (send an empty list).""",
    "round2": """ROUND 2 — What the market asks. Below are requirements from REAL
postings we just scanned. Ask about the important ones the profile doesn't
already confirm, like: "3 of the 8 roles I found ask for Kubernetes — have you
used it?" with options such as "Yes, in production" / "Some — projects or
coursework" / "Not yet". Map answers: production → preferred_skills (and a
highlight if they give a concrete example); some → preferred_skills plus a
note in honest_gaps that it's not production; not yet → honest_gaps.
Set done=true once the key gaps are covered.""",
    "round3": """ROUND 3 — Sharpen. Using the second scan below, fine-tune: are the
target levels and titles right given what's out there, any title words to
exclude, salary floor vs. what postings show, deal-breakers, and which kinds
of roles to prioritize (adjust target_functions tiers / search_keywords).
Set done=true when you're confident; next_question is a one-line wrap-up.""",
}


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


def _market_digest(state: InterviewState) -> str:
    if not state.market:
        return ""
    m = state.market[-1]
    reqs = "\n".join(
        f"- {r.get('skill')}: in {r.get('count')} of {len(m.get('jobs') or [])} postings"
        f"{' (already in profile)' if r.get('confirmed') else ''}"
        for r in m.get("requirements") or []
    )
    jobs = "\n".join(f"- {j.get('title')} @ {j.get('company')}" for j in m.get("jobs") or [])
    return f"# Live postings just scanned\n{jobs}\n\n# Common requirements\n{reqs}\n\n"


def _turn_prompt(state: InterviewState) -> str:
    missing = state.missing()
    missing_txt = "\n".join(f"- {f}: {field_label(f)}" for f in missing) or "(none)"
    optional_txt = "\n".join(
        f"- {f}: {desc}" for f, desc in OPTIONAL_FIELDS.items()
        if getattr(state.draft, f) is None
    ) or "(none)"
    history = [m for m in state.messages if m.kind != "note"][-HISTORY_WINDOW:]
    convo = "\n".join(
        f"{m.role.upper()}: {m.text}"
        + (f"  [options: {' | '.join(m.options)}]" if m.options else "")
        for m in history
    ) or "(chat not started)"
    guide = ROUND_GUIDE[state.phase]
    if state.phase == "round1" and state.build_mode:
        guide += "\n\n" + ROUND_GUIDE["round1_build"]
    draft = state.draft.model_dump(exclude_none=True)
    return (
        f"{guide}\n\n"
        f"{_market_digest(state) if state.phase != 'round1' else ''}"
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


def start(state: InterviewState) -> InterviewState:
    """Post the opening question if the chat hasn't started. No LLM call."""
    if not state.messages:
        state.say(START_TEXT, options=START_OPTIONS)
        state.save()
    return state


def prefill_from_resume(state: InterviewState, content: dict[str, Any]) -> None:
    """Seed the draft with header facts from a parsed resume (no LLM call)."""
    state.resume_content = content
    state.resume_mode = "upload"
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


def resume_received(state: InterviewState, content: dict[str, Any]) -> None:
    """Upload path: prefill, post a friendly note, move to round 1."""
    prefill_from_resume(state, content)
    exps = content.get("experiences") or {}
    n_bullets = sum(len(e.get("bullets") or []) for e in exps.values())
    name = ((content.get("header") or {}).get("name") or "").split(" ")[0]
    state.say(
        f"Got it{', ' + name if name else ''} — I read {len(exps)} roles and {n_bullets} "
        "bullet points. I'll only ask about what your resume doesn't already say.",
        kind="note",
    )
    state.phase = "round1"
    state.round_questions = 0


def _merge(draft: ProfileDraft, updates: ProfileDraft) -> None:
    """Copy every field the turn actually set (None = untouched)."""
    for field_name in updates.model_fields_set:
        value = getattr(updates, field_name)
        if value is not None:
            setattr(draft, field_name, value)


def _wants_upload(text: str) -> bool:
    t = text.lower()
    if re.search(r"\b(no|nope|don't|dont|do not|without|build)\b", t):
        return False
    return bool(re.search(r"\b(yes|yeah|yep|sure|upload|have)\b", t))


def _ask(state: InterviewState, *, llm: ClaudeClient, model: str) -> InterviewTurn:
    turn = llm.structured(
        _turn_prompt(state),
        InterviewTurn,
        model=model,
        system=SYSTEM_PROMPT,
        max_tokens=2000,
        tool_name="submit_turn",
        tool_description="Submit extracted profile updates and the next question.",
    )
    _merge(state.draft, turn.updates)
    return turn


def _end_round(state: InterviewState, turn: InterviewTurn) -> bool:
    """Code-side decision: is the current round finished?"""
    if state.phase == "round1":
        if state.round_questions >= ROUND1_MAX:
            return True
        return turn.done and not state.missing()
    limit = ROUND_LIMITS.get(state.phase, 5)
    return turn.done or state.round_questions >= limit


def step(
    state: InterviewState,
    user_text: str | None,
    *,
    llm: ClaudeClient,
    model: str,
) -> InterviewState:
    """Advance the chat by one user answer and return the new state (saved).

    `user_text=None`/empty asks the next question without recording a user
    message (used to start a round or resume). During `market*` phases this
    is a no-op: the caller runs `market.run_market` instead.
    """
    start(state)
    text = (user_text or "").strip()
    if text:
        state.messages.append(Message(role="user", text=text))

    if state.phase == "start":
        if not text:
            return state
        if _wants_upload(text):
            state.phase, state.resume_mode = "upload", "upload"
            state.say(UPLOAD_TEXT, kind="upload", options=UPLOAD_OPTIONS)
            state.save()
            return state
        state.phase, state.resume_mode = "round1", "build"
        text = ""  # the answer was the resume choice, not a profile fact

    if state.phase == "upload":
        if not text:
            return state
        state.phase, state.resume_mode = "round1", "build"

    if state.phase not in ("round1", "round2", "round3"):
        state.save()
        return state

    turn = _ask(state, llm=llm, model=model)
    if _end_round(state, turn):
        _advance_after_round(state, turn.next_question)
        if state.done:
            from .resume_builder import finish

            finish(state, llm=llm, model=model)
    else:
        state.round_questions += 1
        missing = state.missing()
        question = turn.next_question.strip()
        options = turn.options[:6]
        if turn.done and missing:
            # The model wanted to stop early — keep going on what's missing.
            question = f"Before we move on — could you tell me your {field_label(missing[0])}?"
            options = []
        state.say(question, options=options, multi=turn.multi_select and bool(options))
    state.save()
    return state


def _advance_after_round(state: InterviewState, wrap_up: str) -> None:
    state.round_questions = 0
    if state.phase == "round1":
        state.phase = "market1"
        state.say(wrap_up.strip() or "Great — that's the basics.", kind="note")
    elif state.phase == "round2":
        state.phase = "market2"
        state.say(wrap_up.strip() or "Thanks — that helps a lot.", kind="note")
    else:
        state.phase = "done"
        state.done = True


def draft_to_profile_dict(draft: ProfileDraft) -> dict[str, Any]:
    """Draft -> the `profile:` mapping of config.yaml (pipeline-only fields removed)."""
    d = draft.model_dump(exclude_none=True)
    for k in ("location_tokens", "allow_remote", "excluded_companies", "industry",
              "search_keywords", "experience_entries", "projects", "education"):
        d.pop(k, None)
    d.setdefault("name", "")
    d.setdefault("email", "")
    return d
