"""Load and validate resume_content.yaml.

Schema is enforced via pydantic. Editing the YAML with a typo blows up
at load time, not silently in the rendered docx.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Header(BaseModel):
    name: str
    email: str
    phone: str = ""
    linkedin: str = ""
    location: str = ""


class Bullet(BaseModel):
    """One resume bullet. `tags` drives the family-classifier scoring."""

    id: str
    text: str
    tags: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    title: str
    location: str
    start: str
    end: str
    bullets: list[Bullet]


class Project(BaseModel):
    title: str
    stack: str = ""
    bullets: list[Bullet]


class EducationEntry(BaseModel):
    school: str
    degree: str
    location: str = ""
    start: str = ""
    end: str = ""
    honors: str = ""


class Skills(BaseModel):
    """Skill groups, surface-ordered by the family config."""

    languages: list[str] = Field(default_factory=list)
    data_platform: list[str] = Field(default_factory=list)
    cloud: list[str] = Field(default_factory=list)
    ml: list[str] = Field(default_factory=list)
    frontend: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)


class Additional(BaseModel):
    """The short "ADDITIONAL" block at the end of a resume — languages,
    certifications, academic background. Each field is one line."""

    languages: str = ""
    certifications: str = ""
    academic_background: str = ""


class ResumeContent(BaseModel):
    """Top-level shape of resume_content.yaml."""

    header: Header
    summaries: dict[str, str]
    experiences: dict[str, Experience]
    projects: dict[str, Project] = Field(default_factory=dict)
    education: list[EducationEntry]
    skills: Skills
    additional: Additional = Field(default_factory=Additional)


def load_resume_content(path: str | Path = "resume_content.yaml") -> ResumeContent:
    """Read and validate the resume content YAML.

    Raises pydantic.ValidationError on schema mismatch.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{p} did not parse as a YAML mapping")
    return ResumeContent(**raw)
