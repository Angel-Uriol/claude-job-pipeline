"""Setup step 2: parse an uploaded resume with Claude."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..llm import ClaudeClient
from ..paths import PROMPTS_DIR
from ..resume.parse_resume import ClaudeResumeParseClient, extract_resume_text, parse_resume


def parse_resume_file(path: str | Path, *, llm: ClaudeClient, model: str) -> dict[str, Any]:
    """PDF / DOCX / TXT -> validated ResumeContent as a plain dict.

    Raises ValueError for unreadable or empty files (shown to the user).
    """
    text = extract_resume_text(path)
    if len(text.strip()) < 200:
        raise ValueError(
            "Couldn't read much text from that file (is it a scanned image?). "
            "Try a text-based PDF, a DOCX, or paste it into a .txt file."
        )
    template = (PROMPTS_DIR / "resume_parse.txt").read_text(encoding="utf-8")
    client = ClaudeResumeParseClient(llm=llm, model=model)
    return parse_resume(text, client, template).model_dump()
