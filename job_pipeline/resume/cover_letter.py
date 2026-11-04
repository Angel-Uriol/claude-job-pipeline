"""Honest, evidence-backed cover letters.

Claude writes three short paragraphs for one job using ONLY facts from the
candidate's resume and profile highlights. Each paragraph cites the evidence
ids it used; unknown ids are rejected (same check as Compare/Interview prep)
and any number that isn't in the evidence is flagged "check before sending".
The letter is rendered to .docx by node/cover_letter.js in the resume style.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..config import Config
from ..llm.base import ClaudeClient, LLMError
from ..llm.usage import tagged
from ..models import Job
from ..research.service import candidate_evidence, unbacked_figures, validate_references
from .content_loader import ResumeContent
from .generator import _sanitize
from .validator import validate_docx

_NODE_DIR = Path(__file__).resolve().parent / "node"
_SCRIPT = _NODE_DIR / "cover_letter.js"
DESCRIPTION_CHARS = 6000


class LetterParagraph(BaseModel):
    text: str = Field(description="One body paragraph, 2–4 sentences.")
    evidence_ids: list[str] = Field(
        default_factory=list, description="Ids from the EVIDENCE list this paragraph relies on."
    )


class CoverLetter(BaseModel):
    greeting: str = Field(description='e.g. "Dear Acme hiring team,"')
    paragraphs: list[LetterParagraph] = Field(description="Exactly 3 body paragraphs.")
    closing: str = Field(description="One-line sign-off before the name.")


class CoverLetterResult(BaseModel):
    path: str
    warnings: list[str] = Field(default_factory=list)  # "check before sending"


def build_prompt(template: str, job: Job, evidence: dict[str, str]) -> str:
    lines = "\n".join(f"- {k}: {v}" for k, v in evidence.items()) or "(none)"
    return (
        template.replace("{company}", job.company)
        .replace("{job_title}", job.title)
        .replace("{job_description}", (job.description or "")[:DESCRIPTION_CHARS])
        .replace("{evidence}", lines)
    )


def check_letter(letter: CoverLetter, evidence: dict[str, str], cfg: Config) -> list[str]:
    """Raise on made-up citations; return plain warnings for unbacked numbers."""
    if not letter.paragraphs:
        raise LLMError("The cover letter came back empty. Please retry.")
    validate_references(letter.model_dump(), set(), set(evidence))
    text = " ".join(p.text for p in letter.paragraphs)
    figures = unbacked_figures(text, [], evidence, cfg)
    return [f'"{f}" isn\'t on your resume — check it before sending.' for f in figures]


@tagged("cover-letter")
def generate_cover_letter(
    job: Job,
    content: ResumeContent,
    cfg: Config,
    profile_dir: Path,
    *,
    llm: ClaudeClient,
    template: str,
    output_dir: str | Path,
    node_executable: str = "node",
) -> CoverLetterResult:
    evidence = candidate_evidence(cfg, profile_dir)
    if not evidence:
        raise LLMError("Your resume has no bullet points yet — add some under My resume first.")
    letter = llm.structured(
        build_prompt(template, job, evidence),
        CoverLetter,
        model=cfg.resume.model,
        max_tokens=2000,
        tool_name="submit_cover_letter",
        tool_description="Submit the cover letter. Use only the given evidence; cite ids.",
    )
    warnings = check_letter(letter, evidence, cfg)

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{_sanitize(content.header.name)}_{_sanitize(job.company)}_Cover_Letter.docx"
    spec: dict[str, Any] = {
        "header": content.header.model_dump(),
        "date": date.today().strftime("%B %-d, %Y"),
        "company": job.company,
        "title": job.title,
        "greeting": letter.greeting.strip(),
        "paragraphs": [p.text.strip() for p in letter.paragraphs],
        "closing": letter.closing.strip(),
    }
    render_letter(spec, out, node_executable=node_executable)
    # Keep the text next to the .docx (for the print page and later review).
    out.with_suffix(".json").write_text(
        json.dumps({**spec, "warnings": warnings,
                    "evidence_ids": [p.evidence_ids for p in letter.paragraphs]},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    return CoverLetterResult(path=str(out), warnings=warnings)


def render_letter(spec: dict[str, Any], out: Path, *, node_executable: str = "node") -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
        spec_path = Path(f.name)
    try:
        r = subprocess.run([node_executable, str(_SCRIPT), str(spec_path), str(out)],
                           capture_output=True, text=True, cwd=str(_NODE_DIR),
                           timeout=60, check=False)
        if r.returncode != 0:
            raise LLMError(f"Couldn't create the cover letter file: {r.stderr.strip()[:300]}")
        validate_docx(out)
    finally:
        spec_path.unlink(missing_ok=True)
