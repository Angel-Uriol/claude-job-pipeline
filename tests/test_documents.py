"""Cover letters, resume sidecars (print spec + 'what Claude changed')."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from job_pipeline.config import load_config
from job_pipeline.llm.base import LLMError
from job_pipeline.research.service import candidate_evidence
from job_pipeline.resume.content_loader import load_resume_content
from job_pipeline.resume.cover_letter import (
    CoverLetter,
    LetterParagraph,
    check_letter,
    generate_cover_letter,
)
from job_pipeline.resume.generator import diff_specs, sidecar, write_sidecars
from tests.conftest import EXAMPLE_CONFIG, EXAMPLE_PROFILE
from tests.test_ui_server import _make_job

NODE_READY = shutil.which("node") is not None and (
    Path(__file__).resolve().parent.parent / "job_pipeline/resume/node/node_modules/docx"
).exists()


def _evidence() -> dict[str, str]:
    return candidate_evidence(load_config(EXAMPLE_CONFIG), EXAMPLE_PROFILE)


def _letter(ids: list[str], text: str = "I built Python APIs.") -> CoverLetter:
    return CoverLetter(greeting="Dear Acme hiring team,",
                       paragraphs=[LetterParagraph(text=text, evidence_ids=ids)] * 3,
                       closing="Thank you for your time.")


def test_letter_with_made_up_evidence_is_rejected() -> None:
    with pytest.raises(LLMError, match="unsupported citation"):
        check_letter(_letter(["experience/nowhere/b99"]), _evidence(), load_config(EXAMPLE_CONFIG))


def test_numbers_not_on_the_resume_are_flagged() -> None:
    ev = _evidence()
    first = next(iter(ev))
    warnings = check_letter(_letter([first], "I grew revenue by 937% last year."), ev,
                            load_config(EXAMPLE_CONFIG))
    assert warnings and "937%" in warnings[0]


def test_prompt_contains_only_evidence_and_job() -> None:
    from job_pipeline.resume.cover_letter import build_prompt

    job = _make_job(company="Acme", title="Backend Engineer", description="Build APIs.")
    prompt = build_prompt("{company}|{job_title}|{job_description}|{evidence}", job,
                          {"highlight-1": "Shipped X"})
    assert prompt == "Acme|Backend Engineer|Build APIs.|- highlight-1: Shipped X"


@pytest.mark.skipif(not NODE_READY, reason="needs node + docx installed")
def test_generate_cover_letter_writes_docx_and_json(tmp_path: Path) -> None:
    ev = _evidence()
    ids = list(ev)[:2]

    class FakeLLM:
        auth_mode = "api_key"

        def structured(self, prompt: str, schema: type[Any], **kw: Any) -> Any:
            assert "Acme" in prompt and ids[0] in prompt
            return _letter(ids)

        def text(self, prompt: str, **kw: Any) -> str:
            return ""

    cfg = load_config(EXAMPLE_CONFIG)
    result = generate_cover_letter(
        _make_job(company="Acme"), load_resume_content(EXAMPLE_PROFILE / "resume_content.yaml"),
        cfg, EXAMPLE_PROFILE, llm=FakeLLM(), template="{company} {evidence}",  # type: ignore[arg-type]
        output_dir=tmp_path)
    out = Path(result.path)
    assert out.exists() and out.suffix == ".docx"
    saved = json.loads(out.with_suffix(".json").read_text())
    assert saved["paragraphs"][0] == "I built Python APIs." and saved["warnings"] == []


def test_changes_sidecar_shows_rewording_and_number_drift(tmp_path: Path) -> None:
    original = {"summary": "Old", "skills": [{"items": ["Python", "Go"]}],
                "experiences": [{"company": "Acme", "bullets": [
                    {"id": "b1", "text": "Cut latency 40%"}, {"id": "b2", "text": "Wrote docs"}]}]}
    tailored = {"summary": "New", "skills": [{"items": ["Go", "Python"]}],
                "experiences": [{"company": "Acme", "bullets": [
                    {"id": "b1", "text": "Reduced API latency by 45%"},
                    {"id": "b2", "text": "Wrote docs"}]}]}
    d = diff_specs(original, tailored)
    assert d["summary"]["changed"] is True
    b1, b2 = d["bullets"]
    assert b1["changed"] and b1["numbers_kept"] is False
    assert not b2["changed"] and b2["numbers_kept"]

    docx = tmp_path / "Jo_Acme_SWE.docx"
    write_sidecars(docx, tailored, original)
    assert sidecar(docx, "changes").exists() and sidecar(docx, "spec").exists()
    write_sidecars(docx, original, None)  # re-render with original wording
    assert not sidecar(docx, "changes").exists()
