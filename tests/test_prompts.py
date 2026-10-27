"""Prompt templates render from the profile — no hardcoded candidate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_pipeline.config import load_config
from job_pipeline.models import Job, Source
from job_pipeline.paths import PROMPTS_DIR
from job_pipeline.prompts import candidate_block, max_years_required, render_prompt
from job_pipeline.scoring.pre_filter import PreFilterExtraction, apply_hard_skip_rules
from tests.conftest import EXAMPLE_CONFIG

CFG = load_config(EXAMPLE_CONFIG)
TEMPLATES = ["pre_filter.txt", "deep_fit.txt", "candidate_profile.txt", "resume_tailor.txt"]


@pytest.mark.parametrize("name", TEMPLATES)
def test_templates_render_with_profile(name: str) -> None:
    out = render_prompt(name, CFG)
    assert "{{" not in out and "{%" not in out
    if name != "resume_tailor.txt":
        assert "Jordan Rivera" in out


def test_per_job_placeholders_survive_rendering() -> None:
    out = render_prompt("deep_fit.txt", CFG)
    for ph in ("{company}", "{title}", "{location}", "{extracted}", "{description}"):
        assert ph in out


@pytest.mark.parametrize("path", sorted(PROMPTS_DIR.glob("*")))
def test_no_personal_data_in_shipped_prompts(path: object) -> None:
    text = path.read_text(encoding="utf-8").lower()  # type: ignore[attr-defined]
    # Prompts must describe the candidate only through template variables.
    for needle in ("@gmail.com", "linkedin.com/in/", "/users/", "years at "):
        assert needle not in text


def test_sponsorship_rules_follow_profile() -> None:
    cfg = CFG.model_copy(deep=True)
    assert "no visa sponsorship" not in render_prompt("pre_filter.txt", cfg).lower()
    cfg.profile.needs_sponsorship = True
    cfg.profile.work_authorization = "H-1B"
    out = render_prompt("pre_filter.txt", cfg)
    assert "no visa sponsorship" in out.lower()
    assert "NEEDS visa sponsorship" in candidate_block(cfg.profile)


def test_max_years_auto_and_override() -> None:
    p = CFG.profile.model_copy()
    p.years_experience, p.max_years_required = 2, 0
    assert max_years_required(p) == 7
    p.years_experience = 0
    assert max_years_required(p) == 5
    p.max_years_required = 12
    assert max_years_required(p) == 12


def _job(title: str) -> Job:
    return Job(company="x", job_id="1", title=title, source=Source.GREENHOUSE, url="u",
               location="Remote", description="jd", discovered_at=datetime.now(UTC).isoformat())


def _extraction(**kw: object) -> PreFilterExtraction:
    base: dict[str, object] = dict(level="L4", years_required=3, degree_required="BS",
                                   visa_stance="unknown", tech_stack=[], verdict="pass",
                                   reason="ok")
    base.update(kw)
    return PreFilterExtraction(**base)  # type: ignore[arg-type]


def test_hard_skip_uses_profile_titles_and_sponsorship() -> None:
    p = CFG.profile.model_copy(deep=True)
    assert apply_hard_skip_rules(_job("Engineering Manager"), _extraction(), p)[0] is True
    assert apply_hard_skip_rules(_job("Staffing Analyst"), _extraction(), p)[0] is False
    no_sponsor = _extraction(visa_stance="no_sponsorship")
    assert apply_hard_skip_rules(_job("Engineer"), no_sponsor, p)[0] is False  # citizen
    p.needs_sponsorship = True
    assert apply_hard_skip_rules(_job("Engineer"), no_sponsor, p)[0] is True
    assert apply_hard_skip_rules(_job("Engineer"), _extraction(years_required=9), p)[0] is True
