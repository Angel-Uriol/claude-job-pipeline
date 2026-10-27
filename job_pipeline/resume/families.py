"""Resume family classifier + per-family bullet config.

This clone ships a SINGLE generic family, `software_engineer`, built
entirely from the candidate's own parsed resume (see the onboarding
wizard and `parse_resume.py`). There are no fixed role clusters and no
hand-curated bullet library — every bullet and project the candidate
uploaded is included, in the order it appears in `resume_content.yaml`.

Why one family: we can't know a new user's background in advance, so
instead of maintaining N role-specific bullet sets we let the parsed
resume BE the content and (optionally) let the Claude tailorer reword it
per job. `classify_family` therefore always returns the one family.

To add more families later: add entries to `RESUME_FAMILIES` with the
title/stack signals and (optionally) a locked `required_bullet_ids` set,
and add a matching 3-line wrapper under `node/families/<name>.js`.
`select_bullets` supports both modes — an empty `required_bullet_ids`
means "include every bullet in YAML order"; a non-empty one locks the
bullet set and order (the multi-family behaviour).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Job


@dataclass(frozen=True)
class FamilyConfig:
    """One family's bullet-selection and layout rules."""

    name: str
    # Substrings (case-insensitive) in the JD title that boost this family.
    title_signals: tuple[str, ...] = ()
    # Tech-stack tokens (lowercase) that boost this family.
    stack_signals: tuple[str, ...] = ()
    # Bullets whose `tags` overlap with these are preferred — picked
    # first within the required set.
    preferred_bullet_tags: tuple[str, ...] = ()
    # Bullets that MUST appear, in this order. Empty ⇒ include ALL bullets
    # in the order they appear in the YAML (the single-family default).
    required_bullet_ids: tuple[str, ...] = ()
    # Whether to print the Projects section.
    include_projects: bool = True
    # Required project bullets in order. Empty ⇒ include ALL projects in
    # YAML order.
    required_project_ids: tuple[str, ...] = ()
    # Skill-group order on the page. Names match ResumeContent.skills attrs.
    skill_order: tuple[str, ...] = (
        "languages",
        "data_platform",
        "cloud",
        "ml",
        "frontend",
        "other",
    )
    # Section order on the page:
    # Summary → Experience → Projects → Education → Skills → Additional.
    section_order: tuple[str, ...] = (
        "summary",
        "experience",
        "projects",
        "education",
        "skills",
        "additional",
    )


# Registry. Ships with one generic family; `classify_family` returns it
# for every job. Add entries here (plus a node/families/<name>.js wrapper)
# to introduce role-specific resumes later.
RESUME_FAMILIES: dict[str, FamilyConfig] = {
    # Generic software-engineer resume. Empty required sets ⇒ every parsed
    # bullet/project is included in resume order. All layout (skill order,
    # section order) uses the FamilyConfig defaults above.
    "software_engineer": FamilyConfig(name="software_engineer"),
}


# --- classifier --------------------------------------------------------


def classify_family(job: Job) -> FamilyConfig:
    """Pick the best-fit family for one job. Returns the FamilyConfig.

    With the single generic family this always returns `software_engineer`.
    The scoring loop is kept for when more families are registered: each
    family gets 2 points per title-signal match and 1 per stack-signal
    match; highest wins, ties broken by registry order.
    """
    families = list(RESUME_FAMILIES.values())
    if len(families) == 1:
        return families[0]

    title_lower = job.title.lower()
    stack = _extracted_stack(job)

    best_family = families[0]  # registry-order default
    best_score = -1

    for family in families:
        score = 0
        for signal in family.title_signals:
            if signal in title_lower:
                score += 2
        for token in family.stack_signals:
            if token in stack:
                score += 1
        if score > best_score:
            best_score = score
            best_family = family

    return best_family


def _extracted_stack(job: Job) -> set[str]:
    """Pull the lowercased tech_stack tokens from the pre-filter blob."""
    if not job.extracted:
        return set()
    raw = job.extracted.get("tech_stack") or []
    return {str(t).lower() for t in raw if isinstance(t, str)}


# --- bullet selection --------------------------------------------------


def select_bullets(
    bullets: list[Any],   # list[Bullet] — Any avoids circular import
    family: FamilyConfig,
    *,
    max_bullets: int | None = None,
) -> list[Any]:
    """Pick which experience bullets to include for this family.

    Two modes:

      - **Generic family** (`required_bullet_ids` empty) — include EVERY
        bullet in the order it appears in the YAML. This is the path the
        single-family clone uses: the parsed resume IS the bullet list.
        `max_bullets`, if set, is a hard cap applied after ordering.

      - **Locked family** (`required_bullet_ids` non-empty) — required
        bullets first, IN FAMILY-CONFIG ORDER (always included in full),
        then optional tag-ranked padding up to `max_bullets` (a soft cap
        that never truncates the required set).
    """
    # Generic family: everything, in YAML order.
    if not family.required_bullet_ids:
        chosen_all = list(bullets)
        if max_bullets is not None:
            return chosen_all[:max_bullets]
        return chosen_all

    by_id = {b.id: b for b in bullets}

    chosen: list[Any] = []
    seen: set[str] = set()

    # 1. Required, preserving family order — always included in full.
    for bid in family.required_bullet_ids:
        if bid in by_id and bid not in seen:
            chosen.append(by_id[bid])
            seen.add(bid)

    # 2. Optional padding from tag-ranked extras (only when there's room).
    if max_bullets is not None and len(chosen) < max_bullets:
        preferred = set(family.preferred_bullet_tags)
        remaining = [b for b in bullets if b.id not in seen]
        remaining.sort(key=lambda b: -len(preferred.intersection(b.tags)))
        for b in remaining:
            if len(chosen) >= max_bullets:
                break
            if not preferred.intersection(b.tags):
                continue
            chosen.append(b)
            seen.add(b.id)

    return chosen


def select_project_bullets(
    project_bullets: list[Any],
    family: FamilyConfig,
) -> list[Any]:
    """Filter project bullets per family config.

    If the family doesn't include projects, returns empty. If it includes
    projects but has no locked `required_project_ids`, returns ALL project
    bullets in YAML order (the generic-family path). Otherwise returns the
    required project bullet IDs in order.
    """
    if not family.include_projects:
        return []

    # Generic family: all project bullets, in YAML order.
    if not family.required_project_ids:
        return list(project_bullets)

    by_id = {b.id: b for b in project_bullets}
    return [by_id[bid] for bid in family.required_project_ids if bid in by_id]
