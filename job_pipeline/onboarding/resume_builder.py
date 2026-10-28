"""Draft a resume from the setup chat (for people without one).

When the user picks "No, let's build one together", round 1 collects their
industry, experience, projects and education. At the end of the chat we ask
Claude to turn exactly those facts into the standard resume structure — the
same `ParsedResume` shape the resume parser produces — so every later step
(tailoring, rendering) works unchanged.

Honesty: Claude may polish wording into resume bullets, but only from what the
user said. No invented employers, dates, tools or numbers.
"""

from __future__ import annotations

import json
from typing import Any

from ..llm import ClaudeClient, LLMError
from ..resume.content_loader import ResumeContent
from ..resume.parse_resume import ParsedResume, _parsed_to_content_dict
from .interview import InterviewState

BUILD_PROMPT = """Write a resume for this job seeker using ONLY the facts below.

Profile collected in our chat (JSON):
{draft}

The candidate's own words from the chat:
{answers}

Rules:
- One experience entry per job in experience_entries; turn each highlight into
  a crisp resume bullet (action verb + what + result). Keep every number they
  gave exactly; never add numbers, tools, employers, titles or dates they
  didn't mention. Leave a date empty if unknown.
- Projects from `projects`; education from `education`.
- summary: 2-3 honest sentences for the roles they're targeting.
- Skills: bucket their stated skills (languages, data_platform, cloud, ml,
  frontend, other). Nothing they didn't claim; skip anything listed in
  honest_gaps."""


def build_resume(state: InterviewState, *, llm: ClaudeClient, model: str) -> dict[str, Any]:
    """Return a validated ResumeContent dict drafted from the chat."""
    answers = "\n".join(f"- {m.text}" for m in state.messages if m.role == "user")
    parsed = llm.structured(
        BUILD_PROMPT.format(
            draft=json.dumps(state.draft.model_dump(exclude_none=True), indent=2,
                             ensure_ascii=False),
            answers=answers or "(none)",
        ),
        ParsedResume,
        model=model,
        max_tokens=6000,
        tool_name="submit_resume",
        tool_description="Submit the drafted resume.",
    )
    d = state.draft
    parsed.name = parsed.name or d.name or ""
    parsed.email = parsed.email or d.email or ""
    parsed.phone = parsed.phone or d.phone or ""
    parsed.linkedin = parsed.linkedin or d.linkedin or ""
    parsed.location = parsed.location or d.location or ""
    return ResumeContent(**_parsed_to_content_dict(parsed)).model_dump()


def finish(state: InterviewState, *, llm: ClaudeClient, model: str) -> InterviewState:
    """Wrap up a completed chat: draft the resume if needed, post the summary."""
    if state.phase != "done" or any(m.kind == "note" and m.text.startswith("All set")
                                    for m in state.messages):
        return state
    extra = ""
    if state.build_mode and state.resume_content is None:
        try:
            state.resume_content = build_resume(state, llm=llm, model=model)
            n = len(state.resume_content.get("experiences") or {})
            extra = (f" I also drafted your resume from what you told me ({n} roles) — "
                     "you can read and edit it on the review step.")
        except LLMError:
            extra = " I couldn't draft your resume automatically; you can fill it in on review."
    picked = sum(1 for c in state.companies if c.get("selected", True))
    state.say(
        "All set! Your profile is ready." + extra
        + (f" I've already lined up {picked} companies to search — next you can trim or add "
           "to that list." if picked else " Next, let's pick the companies to search."),
        kind="note",
    )
    state.save()
    return state
