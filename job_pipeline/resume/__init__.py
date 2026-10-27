"""Resume generation — Python side.

The Python pipeline:
  1. Looks up a job by id.
  2. Classifies the right resume family from the job's extracted fields.
  3. Picks bullets from `resume_content.yaml` whose tags overlap with
     the JD's tech stack.
  4. Builds a JSON "spec" (which bullets, in what order, family name).
  5. Writes the spec to a tempfile and invokes `node render.js`.
  6. Validates the generated .docx (python-docx parse check).

The JS renderer in `resume/node/` consumes the spec and writes the
final .docx. Family-specific layout differences (section order, font
emphasis) live in the per-family JS modules under `node/families/`.
"""

from .content_loader import ResumeContent, load_resume_content
from .families import RESUME_FAMILIES, classify_family
from .generator import generate_resume
from .parse_resume import (
    ClaudeResumeParseClient,
    ParsedResume,
    extract_resume_text,
    parse_resume,
    write_resume_content,
)
from .spec_builder import build_resume_spec
from .tailor import ResumeTailorer, build_resume_tailorer
from .validator import validate_docx

__all__ = [
    "RESUME_FAMILIES",
    "ParsedResume",
    "ResumeContent",
    "ResumeTailorer",
    "ClaudeResumeParseClient",
    "build_resume_spec",
    "build_resume_tailorer",
    "classify_family",
    "extract_resume_text",
    "generate_resume",
    "load_resume_content",
    "parse_resume",
    "validate_docx",
    "write_resume_content",
]
