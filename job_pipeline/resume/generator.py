"""Resume generation orchestrator.

Glue between Python (which decides what goes in) and Node (which draws
the page). Steps:
  1. Look up the job by ID in the store.
  2. Classify the family.
  3. Build the JSON spec.
  4. Invoke `node render.js <spec-json> <output-docx>`.
  5. Validate the produced .docx with python-docx.
  6. Stamp `job.resume_path` on the Job so the digest and tracker link to it.

Errors at any step raise — we don't want to half-write a Job's
resume_path and leave the user with a missing file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..models import Job
from .content_loader import ResumeContent
from .families import FamilyConfig, classify_family
from .spec_builder import build_resume_spec
from .tailor import ResumeTailorer
from .validator import DocxValidationError, validate_docx

log = logging.getLogger(__name__)


# Resolve the Node project relative to this file. The renderer lives at
# `job_pipeline/resume/node/render.js`.
_NODE_DIR = Path(__file__).resolve().parent / "node"
_RENDER_SCRIPT = _NODE_DIR / "render.js"


class ResumeGenerationError(Exception):
    """Raised on any failure during resume generation."""


def generate_resume(
    job: Job,
    content: ResumeContent,
    output_dir: str | Path,
    *,
    family: FamilyConfig | None = None,
    node_executable: str = "node",
    tailorer: ResumeTailorer | None = None,
) -> Path:
    """Generate one tailored resume and return the output path.

    Args:
        job: The target Job (must have an id or dedup-able title).
        content: Loaded resume content YAML.
        output_dir: Where to write `<Name>_<Company>_<Family>.docx`.
        family: Override the classifier's pick; default uses classify_family().
        node_executable: Path to `node`. Override for tests.
        tailorer: Optional Claude-backed tailorer. When provided, the spec's
            summary + experience bullets are honestly reworded toward this
            job before rendering. On any tailoring error we log and fall
            back to the deterministic spec — a failed rewrite never blocks
            the resume.
    """
    if not _RENDER_SCRIPT.exists():
        raise ResumeGenerationError(
            f"Node renderer missing at {_RENDER_SCRIPT}. "
            "Run `cd job_pipeline/resume/node && npm install`."
        )

    family = family or classify_family(job)
    spec = build_resume_spec(job, content, family)

    if tailorer is not None:
        try:
            spec = tailorer.tailor_spec(spec, job)
            log.info("Tailored resume spec for %s / %s via Claude", job.company, job.title)
        except Exception as e:  # noqa: BLE001 — tailoring is best-effort
            log.warning(
                "Resume tailoring failed for %s / %s (%s); using deterministic spec.",
                job.company,
                job.title,
                e,
            )

    # Resolve to absolute — the subprocess runs with cwd=node/ so a
    # relative path would land inside the node/ tree instead of
    # outputs/resumes/.
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / _output_filename(job, family, content.header.name)

    # Write the spec to a tempfile and pass its path — keeps stdin
    # simple and lets the user inspect the spec if rendering goes weird.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="resume-spec-", delete=False, encoding="utf-8"
    ) as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
        spec_path = Path(f.name)

    try:
        log.info(
            "Rendering resume for %s / %s (family=%s)",
            job.company,
            job.title,
            family.name,
        )
        result = subprocess.run(
            [node_executable, str(_RENDER_SCRIPT), str(spec_path), str(output_path)],
            capture_output=True,
            text=True,
            cwd=str(_NODE_DIR),  # so `require('docx')` resolves node_modules
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise ResumeGenerationError(
                f"Node renderer exited {result.returncode}.\n"
                f"stderr: {result.stderr.strip()}\n"
                f"stdout: {result.stdout.strip()}"
            )

        try:
            validate_docx(output_path)
        except DocxValidationError as e:
            raise ResumeGenerationError(f"Generated docx failed validation: {e}") from e

    finally:
        # Best effort — leave the spec only if rendering actually failed
        # so the user can debug. Otherwise clean up.
        if os.environ.get("KEEP_RESUME_SPEC") != "1":
            spec_path.unlink(missing_ok=True)

    return output_path


def _output_filename(job: Job, family: FamilyConfig, candidate_name: str) -> str:
    """`<Candidate_Name>_<Company>_<Family>.docx` with safe-character
    sanitization. The candidate name comes from the resume header, so the
    filename matches whoever's resume was parsed at onboarding."""
    name = _sanitize(candidate_name)
    company = _sanitize(job.company)
    return f"{name}_{company}_{family.name}.docx"


def _sanitize(s: str) -> str:
    """Strip everything not in [A-Za-z0-9_-] and collapse runs to a single _."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")
    return cleaned or "Unknown"
