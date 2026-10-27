"""Build the JSON spec that the Node renderer consumes.

A "spec" is the fully-resolved content for one resume: header, summary,
experiences with selected bullets, projects, education, and skills in
the right order. Everything the renderer needs to draw the page is in
this single JSON object.

Keeping the spec a plain dict (no pydantic) means the Node side doesn't
have to mirror a schema — it just consumes keys. The spec shape is the
contract; tests pin it.
"""

from __future__ import annotations

from typing import Any

from ..models import Job
from .content_loader import ResumeContent
from .families import FamilyConfig, select_bullets, select_project_bullets


def build_resume_spec(
    job: Job,
    content: ResumeContent,
    family: FamilyConfig,
) -> dict[str, Any]:
    """Assemble the complete render spec for one (job, family).

    Returns a JSON-serializable dict. The Node renderer at render.js
    is the only consumer — its expectations and this function's output
    are coupled. When you add a new field here, update render.js too.
    """
    # Summary — prefer this family's summary; fall back to the first
    # available summary (family-name-agnostic) so a mismatched key never
    # ships an empty header.
    summary = content.summaries.get(family.name) or next(
        iter(content.summaries.values()), ""
    )

    # Experiences. Usually several; the structure supports
    # adding past roles. Each experience gets its family-filtered
    # bullets baked in.
    experiences_out: list[dict[str, Any]] = []
    for _key, exp in content.experiences.items():
        bullets = select_bullets(list(exp.bullets), family)
        experiences_out.append(
            {
                "company": exp.company,
                "title": exp.title,
                "location": exp.location,
                "start": exp.start,
                "end": exp.end,
                "bullets": [{"id": b.id, "text": b.text} for b in bullets],
            }
        )

    # Projects — only when the family wants them.
    projects_out: list[dict[str, Any]] = []
    if family.include_projects:
        for _key, project in content.projects.items():
            bullets = select_project_bullets(list(project.bullets), family)
            if not bullets:
                continue
            projects_out.append(
                {
                    "title": project.title,
                    "stack": project.stack,
                    "bullets": [{"id": b.id, "text": b.text} for b in bullets],
                }
            )

    # Skills — ordered per family. Empty groups are dropped so the
    # rendered "Skills" line doesn't show empty categories.
    skills_dict = content.skills.model_dump()
    skills_out: list[dict[str, Any]] = []
    for group_name in family.skill_order:
        items = skills_dict.get(group_name) or []
        if not items:
            continue
        skills_out.append({"group": _humanize(group_name), "items": items})

    return {
        "family": family.name,
        # Section order from the family config so the JS renderer doesn't
        # need to hardcode it per-module — change order from Python.
        "section_order": list(family.section_order),
        "header": content.header.model_dump(),
        "summary": summary.strip(),
        "experiences": experiences_out,
        "projects": projects_out,
        "education": [e.model_dump() for e in content.education],
        "skills": skills_out,
        "additional": content.additional.model_dump(),
        # Context for the renderer to use in the output filename.
        "job_context": {
            "company": job.company,
            "title": job.title,
            "job_id": job.job_id or "",
        },
    }


def _humanize(group_key: str) -> str:
    """Turn `data_platform` into `Data Platform` for resume display.

    Hand-rolled mapping where the default title-casing isn't quite right
    (e.g. `ml` should be `ML`, not `Ml`).
    """
    overrides = {
        "ml": "ML",
        "frontend": "Frontend",
        "data_platform": "Data Platform",
    }
    return overrides.get(group_key, group_key.replace("_", " ").title())
