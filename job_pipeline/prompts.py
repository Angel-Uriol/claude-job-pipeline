"""Render the prompt templates in prompts/ from the user's profile.

Prompt files use two kinds of placeholders:

* ``{{ ... }}`` / ``{% ... %}`` — Jinja, filled ONCE per run from the profile
  (who the candidate is, what they want, their honest gaps).
* ``{title}``, ``{description}``, ... — single braces, filled PER JOB by the
  scoring / tailoring code with ``str.replace`` (so braces inside a job
  description can never break templating).

Jinja ignores single braces, so the two never collide. The candidate block
is built in one place (``candidate_block``) and reused by every prompt —
that's what keeps scoring, tailoring and the Chrome Claude prompts in sync.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from .config import Config, Profile
from .paths import PROMPTS_DIR

_ENV = Environment(keep_trailing_newline=True, undefined=StrictUndefined, autoescape=False)

_TIER_LABELS = {
    "A": "TIER A — strongest fit",
    "B": "TIER B — strong fit",
    "C": "TIER C — stretch / career pivot",
}


def tiers(profile: Profile) -> dict[str, list[str]]:
    """Target functions grouped by tier: {"A": ["ML Platform (notes)", ...], ...}."""
    out: dict[str, list[str]] = {"A": [], "B": [], "C": []}
    for f in profile.target_functions:
        out[f.tier].append(f"{f.name} ({f.notes})" if f.notes else f.name)
    return out


def tier_block(profile: Profile) -> str:
    lines: list[str] = []
    for tier, items in tiers(profile).items():
        if not items:
            continue
        lines.append(f"{_TIER_LABELS[tier]}:")
        lines.extend(f"  - {item}" for item in items)
    return "\n".join(lines) if lines else "  (no target functions configured)"


def max_years_required(profile: Profile) -> int:
    """Hard-skip threshold for 'years required' in a JD."""
    if profile.max_years_required > 0:
        return profile.max_years_required
    return max(5, profile.years_experience + 5)


def _bullets(items: list[str], empty: str = "(none)") -> str:
    return "\n".join(f"- {x}" for x in items) if items else empty


def candidate_block(profile: Profile) -> str:
    """The shared, human-readable candidate description used by every prompt."""
    p = profile
    auth = p.work_authorization or "not specified"
    if p.needs_sponsorship:
        auth += " — NEEDS visa sponsorship"
    parts = [
        f"Name: {p.name}",
        f"Headline: {p.headline}" if p.headline else "",
        f"Experience: ~{p.years_experience} years",
        f"Lives in: {p.location}" if p.location else "",
        f"Location rule: {p.location_preference}" if p.location_preference else "",
        f"Work authorization: {auth}",
        f"Target levels: {', '.join(p.target_levels) or 'not specified'}",
        f"Salary floor: {p.salary_floor}" if p.salary_floor else "",
        "",
        "Target functions:",
        tier_block(p),
        "",
        f"Must-have skills: {', '.join(p.must_have_skills) or '(none)'}",
        f"Core / preferred skills: {', '.join(p.preferred_skills) or '(none)'}",
        "",
        "Signature work and highlights:",
        _bullets(p.highlights),
        "",
        "Honest gaps (NOT production experience — never claim these):",
        _bullets(p.honest_gaps),
        "",
        "Deal-breakers:",
        _bullets(p.deal_breakers),
        f"Excluded title words: {', '.join(p.excluded_titles) or '(none)'}",
        f"Dream companies: {', '.join(p.dream_companies)}" if p.dream_companies else "",
    ]
    return "\n".join(x for x in parts if x is not None).strip()


def prompt_context(cfg: Config) -> dict[str, Any]:
    p = cfg.profile
    return {
        "p": p,
        "cfg": cfg,
        "candidate": candidate_block(p),
        "tiers": tier_block(p),
        "max_years": max_years_required(p),
        "skills": ", ".join([*p.must_have_skills, *p.preferred_skills]),
    }


def render_prompt_text(text: str, cfg: Config) -> str:
    return _ENV.from_string(text).render(**prompt_context(cfg))


def render_prompt_file(path: str | Path, cfg: Config) -> str:
    return render_prompt_text(Path(path).read_text(encoding="utf-8"), cfg)


def render_prompt(name: str, cfg: Config) -> str:
    """Render ``prompts/<name>`` for this profile."""
    return render_prompt_file(PROMPTS_DIR / name, cfg)
