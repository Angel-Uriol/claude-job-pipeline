"""Skill-list hygiene shared by the setup chat and the resume renderer."""

from __future__ import annotations

# Words that show up in resume skill sections but aren't skills a job
# description would ask for — kept out of scoring and the Skills section.
SOFT_SKILLS = frozenset({
    "analytical", "design skills", "communication", "teamwork", "leadership",
    "problem solving", "problem-solving", "detail oriented", "detail-oriented",
    "time management", "critical thinking", "creativity", "collaboration",
})


def is_soft_skill(name: str) -> bool:
    return name.strip().lower() in SOFT_SKILLS
