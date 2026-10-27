"""Tests for the onboarding resume parser (parse_resume.py).

No network: a fake client returns a canned ParsedResume, and we assert the
conversion into a schema-valid ResumeContent (stable bullet IDs, single
software_engineer summary, round-trips through YAML and load_resume_content).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_pipeline.resume import load_resume_content
from job_pipeline.resume.parse_resume import (
    ParsedResume,
    ResumeParseClient,
    _ParsedBullet,
    _ParsedExperience,
    _ParsedProject,
    _ParsedSkills,
    extract_resume_text,
    parse_resume,
    write_resume_content,
)

_PROMPT = "Parse this:\n{resume_text}\n"


class _FakeParseClient:
    """Returns a canned ParsedResume; records the prompt it was given."""

    def __init__(self, parsed: ParsedResume) -> None:
        self.parsed = parsed
        self.prompts_seen: list[str] = []

    def parse(self, prompt: str) -> ParsedResume:
        self.prompts_seen.append(prompt)
        return self.parsed


def _sample_parsed() -> ParsedResume:
    return ParsedResume(
        name="Sam Taylor",
        email="sam.taylor@example.com",
        phone="(555) 111-2222",
        linkedin="https://www.linkedin.com/in/sam-taylor-example/",
        location="Oakland, CA",
        summary="Backend engineer with 5 years building distributed services.",
        experiences=[
            _ParsedExperience(
                company="Acme Corp",
                title="Software Engineer",
                location="San Francisco, CA",
                start="2021",
                end="Present",
                bullets=[
                    _ParsedBullet(text="Built Go services handling 3M requests/day."),
                    _ParsedBullet(text="Cut p95 latency by 40%."),
                ],
            ),
            _ParsedExperience(
                company="Acme Corp",  # duplicate company → key must not collide
                title="Junior Engineer",
                location="Remote",
                start="2019",
                end="2021",
                bullets=[_ParsedBullet(text="Maintained the billing service.")],
            ),
        ],
        projects=[
            _ParsedProject(
                title="Side Project",
                stack="Personal",
                bullets=[_ParsedBullet(text="A CLI tool in Rust.")],
            )
        ],
        skills=_ParsedSkills(languages=["Go", "Python"], cloud=["AWS", "Docker"]),
        languages_spoken="English",
    )


def test_parse_resume_produces_valid_content() -> None:
    client: ResumeParseClient = _FakeParseClient(_sample_parsed())
    content = parse_resume("raw resume text with a { brace", client, _PROMPT)
    # Single generic family summary present.
    assert "software_engineer" in content.summaries
    assert content.header.name == "Sam Taylor"
    assert content.header.email == "sam.taylor@example.com"
    # Two experiences, duplicate company keys deduped.
    assert len(content.experiences) == 2
    # Skills carried across.
    assert "Go" in content.skills.languages


def test_parse_resume_assigns_unique_sequential_bullet_ids() -> None:
    client = _FakeParseClient(_sample_parsed())
    content = parse_resume("text", client, _PROMPT)
    exp_ids = [b.id for exp in content.experiences.values() for b in exp.bullets]
    # 3 experience bullets total → b1, b2, b3, all unique.
    assert exp_ids == sorted(set(exp_ids), key=exp_ids.index)
    assert set(exp_ids) == {"b1", "b2", "b3"}
    proj_ids = [b.id for proj in content.projects.values() for b in proj.bullets]
    assert proj_ids == ["p1"]


def test_parse_resume_braces_do_not_break_templating() -> None:
    """Resume text containing { or } must not raise (we use .replace)."""
    client = _FakeParseClient(_sample_parsed())
    content = parse_resume("salary {range} and { unmatched", client, _PROMPT)
    assert content.header.name == "Sam Taylor"
    # The resume text was injected into the prompt verbatim.
    assert "salary {range}" in client.prompts_seen[0]


def test_write_resume_content_round_trips(tmp_path: Path) -> None:
    client = _FakeParseClient(_sample_parsed())
    content = parse_resume("text", client, _PROMPT)
    out = tmp_path / "resume_content.yaml"
    write_resume_content(content, out)
    assert out.exists()
    # Re-loading the written YAML must validate and match.
    reloaded = load_resume_content(out)
    assert reloaded.header.name == "Sam Taylor"
    assert "software_engineer" in reloaded.summaries


def test_extract_resume_text_txt(tmp_path: Path) -> None:
    p = tmp_path / "resume.txt"
    p.write_text("Jane Doe\nSoftware Engineer\n", encoding="utf-8")
    assert "Jane Doe" in extract_resume_text(p)


def test_extract_resume_text_rejects_unknown(tmp_path: Path) -> None:
    p = tmp_path / "resume.rtf"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported resume type"):
        extract_resume_text(p)
