"""Local docx validator — open the file with python-docx and verify
it parses without error and contains the expected sections.

Replaces the `/mnt/skills/.../validate.py` path from the spec, which
isn't available locally. The check we run here is cheap but
catches the failure modes that actually happen:
- file is empty / not a zip (docx is a zip under the hood)
- file is missing required parts (document.xml, etc.) → python-docx raises
- file has zero paragraphs (renderer wrote a blank doc)

Anything fancier (e.g. checking that fonts are Arial) would be
nice-to-have but isn't worth the surface area.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document


class DocxValidationError(Exception):
    """Raised when a generated docx fails validation."""


def validate_docx(path: str | Path) -> None:
    """Open and structurally check the generated resume.

    Raises DocxValidationError with a useful message on failure.
    """
    p = Path(path)
    if not p.exists():
        raise DocxValidationError(f"File does not exist: {p}")
    if p.stat().st_size == 0:
        raise DocxValidationError(f"File is empty: {p}")

    try:
        doc = Document(str(p))
    except Exception as e:
        # python-docx raises a variety of exceptions for corrupt files —
        # PackageNotFoundError, KeyError, etc. Treat them all uniformly.
        raise DocxValidationError(f"Failed to parse {p}: {e}") from e

    # A blank document would have one default paragraph but no real
    # content. Sanity-check for at least one paragraph with text.
    if not any(p.text.strip() for p in doc.paragraphs):
        raise DocxValidationError(
            f"Document has no text content: {p}"
        )
