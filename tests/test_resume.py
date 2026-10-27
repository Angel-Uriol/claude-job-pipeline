"""Tests for the resume pipeline (content_loader, families, spec_builder,
validator, generator orchestration).

This clone ships a SINGLE generic `software_engineer` family. Tests load a
stable fixture (tests/fixtures/resume_content.yaml, a fictional candidate)
rather than the runtime resume_content.yaml — so onboarding a real user,
which overwrites the runtime file, never breaks `make check`.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from job_pipeline.models import Job, Source
from job_pipeline.resume import (
    RESUME_FAMILIES,
    build_resume_spec,
    classify_family,
    load_resume_content,
)
from job_pipeline.resume.families import (
    FamilyConfig,
    select_bullets,
    select_project_bullets,
)
from job_pipeline.resume.generator import (
    ResumeGenerationError,
    _output_filename,
    _sanitize,
    generate_resume,
)
from job_pipeline.resume.tailor import (
    ResumeTailorer,
    TailoredBullet,
    TailoredResume,
    build_resume_tailorer,
)
from job_pipeline.resume.validator import DocxValidationError, validate_docx

# A minimal prompt template with the 4 placeholders tailor_spec fills.
_TAILOR_PROMPT = "{job_title} | {job_description} | {summary} | {bullets_json}"


class _FakeTailorClient:
    """Returns a canned TailoredResume; records prompts seen."""

    def __init__(self, tailored: TailoredResume) -> None:
        self.tailored = tailored
        self.prompts_seen: list[str] = []

    def tailor(self, prompt: str) -> TailoredResume:
        self.prompts_seen.append(prompt)
        return self.tailored


class _RaisingTailorClient:
    def tailor(self, prompt: str) -> TailoredResume:
        raise RuntimeError("tailoring boom")


REPO = Path(__file__).resolve().parent.parent
# Stable test fixture (NOT the runtime resume_content.yaml the wizard writes).
RESUME_CONTENT = REPO / "tests" / "fixtures" / "resume_content.yaml"

# The one family this clone ships.
FAMILY = "software_engineer"


def _make_job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "company": "databricks",
        "job_id": "1",
        "title": "Software Engineer, Backend",
        "team": "Platform",
        "source": Source.GREENHOUSE,
        "url": "u",
        "location": "San Francisco, CA",
        "discovered_at": datetime.now(UTC).isoformat(),
        "description": "...",
    }
    defaults.update(overrides)
    return Job(**defaults)


# --- content_loader -----------------------------------------------------


def test_shipped_resume_content_loads() -> None:
    """The fixture resume_content.yaml must validate and carry a
    software_engineer summary + at least one experience bullet."""
    content = load_resume_content(RESUME_CONTENT)
    assert content.header.name == "Jordan Rivera"
    assert FAMILY in content.summaries
    assert "platform_co" in content.experiences
    assert any(b.id == "b1" for b in content.experiences["platform_co"].bullets)


def test_resume_content_has_a_summary_for_every_family() -> None:
    content = load_resume_content(RESUME_CONTENT)
    for fname in RESUME_FAMILIES:
        assert fname in content.summaries, f"Missing summary for family {fname}"


def test_resume_content_no_github_link() -> None:
    """No GitHub URL on the resume header (LinkedIn is fine)."""
    content = load_resume_content(RESUME_CONTENT)
    blob = content.model_dump_json().lower()
    assert "github.com" not in blob
    assert content.header.linkedin and "github" not in content.header.linkedin.lower()


# --- classify_family ---------------------------------------------------


def test_classify_always_software_engineer() -> None:
    """With a single generic family, every job classifies to it —
    regardless of title or extracted stack."""
    for title, extracted in [
        ("ML Data Engineer", {"tech_stack": ["pyspark", "airflow"]}),
        ("Backend Software Engineer", {"tech_stack": ["go", "postgres"]}),
        ("Frontend Engineer", {"tech_stack": ["react", "typescript"]}),
        ("Site Reliability Engineer", None),
    ]:
        job = _make_job(title=title, extracted=extracted)
        assert classify_family(job).name == FAMILY


def test_classify_no_extraction_still_works() -> None:
    """A job that hasn't been pre-filtered yet should still classify."""
    job = _make_job(title="Software Engineer", extracted=None)
    assert classify_family(job).name == FAMILY


# --- select_bullets ----------------------------------------------------


def test_select_bullets_includes_all_in_yaml_order() -> None:
    """The generic family has no locked required set, so every bullet is
    returned in the order it appears in the YAML."""
    content = load_resume_content(RESUME_CONTENT)
    family = RESUME_FAMILIES[FAMILY]
    exp = content.experiences["platform_co"]
    bullets = select_bullets(list(exp.bullets), family)
    assert [b.id for b in bullets] == [b.id for b in exp.bullets]


def test_select_bullets_max_is_a_hard_cap_for_generic_family() -> None:
    """For the generic family, max_bullets caps the count after ordering."""
    content = load_resume_content(RESUME_CONTENT)
    family = RESUME_FAMILIES[FAMILY]
    exp = content.experiences["platform_co"]
    bullets = select_bullets(list(exp.bullets), family, max_bullets=2)
    assert len(bullets) == 2
    assert [b.id for b in bullets] == [b.id for b in exp.bullets][:2]


def test_select_bullets_locked_family_puts_required_first() -> None:
    """The locked-family code path (non-empty required_bullet_ids) still
    works — required bullets come first, in family order — so additional
    families can be added later without changing select_bullets."""
    content = load_resume_content(RESUME_CONTENT)
    exp = content.experiences["platform_co"]
    locked = FamilyConfig(name="locked_demo", required_bullet_ids=("b3", "b1"))
    bullets = select_bullets(list(exp.bullets), locked)
    assert [b.id for b in bullets] == ["b3", "b1"]


def test_select_project_bullets_returns_all_for_generic_family() -> None:
    """The generic family includes every project bullet in YAML order."""
    content = load_resume_content(RESUME_CONTENT)
    family = RESUME_FAMILIES[FAMILY]
    proj = content.projects["url_shortener"]
    chosen = select_project_bullets(list(proj.bullets), family)
    assert [b.id for b in chosen] == [b.id for b in proj.bullets]


# --- build_resume_spec -------------------------------------------------


def test_spec_includes_all_top_level_keys() -> None:
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()
    family = classify_family(job)
    spec = build_resume_spec(job, content, family)
    assert set(spec.keys()) >= {
        "family", "header", "summary", "experiences", "projects",
        "education", "skills", "job_context",
    }


def test_spec_summary_matches_family() -> None:
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()
    family = RESUME_FAMILIES[FAMILY]
    spec = build_resume_spec(job, content, family)
    assert spec["family"] == FAMILY
    assert spec["summary"] == content.summaries[FAMILY].strip()


def test_spec_is_json_serializable() -> None:
    """Whatever we put in the spec must round-trip through JSON for Node."""
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()
    spec = build_resume_spec(job, content, classify_family(job))
    json.dumps(spec)  # must not raise


def test_spec_includes_projects() -> None:
    """The generic family ships the candidate's projects."""
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()
    spec = build_resume_spec(job, content, RESUME_FAMILIES[FAMILY])
    assert len(spec["projects"]) > 0
    titles = " ".join(p["title"] for p in spec["projects"])
    assert "URL Shortener" in titles


def test_spec_skill_order_follows_family_and_drops_empty_groups() -> None:
    """Skills render in family.skill_order; empty buckets are dropped.

    The fixture has an empty `ml` bucket, so 'ML' must not appear, while
    Languages precedes Cloud per the default order."""
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()
    spec = build_resume_spec(job, content, RESUME_FAMILIES[FAMILY])
    groups = [g["group"] for g in spec["skills"]]
    assert "ML" not in groups  # empty bucket dropped
    assert groups.index("Languages") < groups.index("Cloud")


# --- generator helpers -------------------------------------------------


def test_sanitize_strips_special_chars() -> None:
    assert _sanitize("Foo & Bar / Baz") == "Foo_Bar_Baz"


def test_sanitize_handles_empty() -> None:
    assert _sanitize("") == "Unknown"
    assert _sanitize("!@#$%") == "Unknown"


def test_output_filename_pattern() -> None:
    """Filename derives the name prefix from the resume header, not a
    hardcoded candidate."""
    job = _make_job(company="Databricks")
    family = RESUME_FAMILIES[FAMILY]
    name = _output_filename(job, family, "Jordan Rivera")
    assert name == "Jordan_Rivera_Databricks_software_engineer.docx"


# --- generate_resume orchestration (mocked Node) -----------------------


def test_generate_resume_invokes_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify the subprocess gets called with `node render.js <spec> <output>`.

    We mock subprocess.run AND validate_docx so the test doesn't depend
    on Node or python-docx behavior."""
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()

    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        captured["args"] = args[0]
        captured["cwd"] = kwargs.get("cwd")
        # Touch the output file so validator's existence check passes,
        # though we'll also stub the validator below.
        out_path = Path(args[0][3])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"PK\x03\x04 mock docx")
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("job_pipeline.resume.generator.validate_docx", lambda p: None)

    out = generate_resume(job, content, tmp_path / "resumes")

    # The first arg of run() is the command list; element 0 must be node.
    assert captured["args"][0] == "node"
    assert captured["args"][1].endswith("render.js")
    # Filename prefix comes from the resume header name.
    assert out.name.startswith("Jordan_Rivera_")
    assert out.name.endswith(".docx")


def test_generate_resume_propagates_node_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()

    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "render.js: oops"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ResumeGenerationError, match="exited 1"):
        generate_resume(job, content, tmp_path / "resumes")


# --- validator ----------------------------------------------------------


def test_validator_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(DocxValidationError, match="does not exist"):
        validate_docx(tmp_path / "nope.docx")


def test_validator_rejects_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.docx"
    p.write_bytes(b"")
    with pytest.raises(DocxValidationError, match="empty"):
        validate_docx(p)


def test_validator_rejects_non_docx(tmp_path: Path) -> None:
    """A garbage file that's NOT a docx (zip) should fail."""
    p = tmp_path / "fake.docx"
    p.write_bytes(b"not a real docx - just some bytes")
    with pytest.raises(DocxValidationError, match="Failed to parse"):
        validate_docx(p)


# --- resume tailoring (Claude) ------------------------------------------


def test_tailor_spec_merges_by_id_and_replaces_summary() -> None:
    """tailor_spec replaces the summary and rewrites bullets by id; bullets
    Claude didn't return keep their original text; structure is preserved."""
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()
    family = classify_family(job)
    spec = build_resume_spec(job, content, family)

    first_exp = spec["experiences"][0]
    first_id = first_exp["bullets"][0]["id"]
    orig_first_text = first_exp["bullets"][0]["text"]

    tailored = TailoredResume(
        summary="Tailored summary that foregrounds this role.",
        bullets=[TailoredBullet(id=first_id, text="REWRITTEN first bullet.")],
    )
    tailorer = ResumeTailorer(_FakeTailorClient(tailored), _TAILOR_PROMPT)
    new_spec = tailorer.tailor_spec(spec, job)

    assert new_spec["summary"] == "Tailored summary that foregrounds this role."
    assert new_spec["experiences"][0]["bullets"][0]["text"] == "REWRITTEN first bullet."
    # A bullet Claude did NOT return keeps its original text.
    if len(first_exp["bullets"]) > 1:
        assert (
            new_spec["experiences"][0]["bullets"][1]["text"]
            == spec["experiences"][0]["bullets"][1]["text"]
        )
    # Original spec object is not mutated in place.
    assert spec["experiences"][0]["bullets"][0]["text"] == orig_first_text


def test_tailor_spec_passes_job_and_bullets_into_prompt() -> None:
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job(title="Senior Backend Engineer")
    family = classify_family(job)
    spec = build_resume_spec(job, content, family)
    client = _FakeTailorClient(TailoredResume(summary="x", bullets=[]))
    tailorer = ResumeTailorer(client, _TAILOR_PROMPT)
    tailorer.tailor_spec(spec, job)
    assert client.prompts_seen, "client.tailor should have been called"
    assert "Senior Backend Engineer" in client.prompts_seen[0]


def test_build_resume_tailorer_gating() -> None:
    from unittest.mock import MagicMock

    # Disabled → None.
    assert build_resume_tailorer(enabled=False, model="m", prompt_template=_TAILOR_PROMPT) is None
    # Claude not connected (conftest blanks the API key) → None, graceful fallback.
    assert build_resume_tailorer(enabled=True, model="m", prompt_template=_TAILOR_PROMPT) is None
    # Enabled + a client → a real tailorer.
    t = build_resume_tailorer(
        enabled=True, model="m", prompt_template=_TAILOR_PROMPT, llm=MagicMock()
    )
    assert isinstance(t, ResumeTailorer)


def test_generate_resume_applies_tailorer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When a tailorer is passed, the spec written to Node has the rewrites."""
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()
    family = classify_family(job)
    spec0 = build_resume_spec(job, content, family)
    first_id = spec0["experiences"][0]["bullets"][0]["id"]
    tailored = TailoredResume(
        summary="TAILORED SUMMARY",
        bullets=[TailoredBullet(id=first_id, text="TAILORED BULLET")],
    )
    tailorer = ResumeTailorer(_FakeTailorClient(tailored), _TAILOR_PROMPT)

    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        captured["spec"] = json.loads(Path(args[0][2]).read_text(encoding="utf-8"))
        out_path = Path(args[0][3])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"PK\x03\x04 mock docx")
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("job_pipeline.resume.generator.validate_docx", lambda p: None)

    generate_resume(job, content, tmp_path / "resumes", tailorer=tailorer)
    assert captured["spec"]["summary"] == "TAILORED SUMMARY"
    assert captured["spec"]["experiences"][0]["bullets"][0]["text"] == "TAILORED BULLET"


def test_generate_resume_tailoring_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If tailoring raises, generation falls back to the deterministic spec."""
    content = load_resume_content(RESUME_CONTENT)
    job = _make_job()
    family = classify_family(job)
    orig_summary = build_resume_spec(job, content, family)["summary"]
    tailorer = ResumeTailorer(_RaisingTailorClient(), _TAILOR_PROMPT)

    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> MagicMock:
        captured["spec"] = json.loads(Path(args[0][2]).read_text(encoding="utf-8"))
        out_path = Path(args[0][3])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"PK\x03\x04 mock docx")
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("job_pipeline.resume.generator.validate_docx", lambda p: None)

    out = generate_resume(job, content, tmp_path / "resumes", tailorer=tailorer)
    # Deterministic summary survived the tailoring failure.
    assert captured["spec"]["summary"] == orig_summary
    assert out.name.endswith(".docx")
