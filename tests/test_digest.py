"""Tests for the HTML digest builder.

We don't render-and-eyeball — instead we assert structural properties
of the output (section IDs present, jobs appear in the right bucket,
HTML-special characters are escaped). That's stable across CSS tweaks.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from job_pipeline.config import load_config
from job_pipeline.digest import build_digest_html, partition_jobs
from job_pipeline.digest.chrome_prompt_builder import build_chrome_prompts_for_config
from job_pipeline.digest.html_builder import group_by_company
from job_pipeline.models import Job, PreFilterVerdict, Source, Status, Verdict
from tests.conftest import EXAMPLE_CONFIG

REPO = Path(__file__).resolve().parent.parent
DIGEST_TEMPLATE = REPO / "prompts" / "digest.html.j2"
CHROME_TEMPLATE = REPO / "prompts" / "chrome_claude_template.txt"
CFG = load_config(EXAMPLE_CONFIG)


def _make_job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "company": "databricks",
        "job_id": "1",
        "title": "Software Engineer, ML Platform",
        "team": "ML",
        "source": Source.GREENHOUSE,
        "url": "https://example.com/job/1",
        "location": "San Francisco, CA",
        "discovered_at": datetime.now(UTC).isoformat(),
        "description": "Build stuff",
    }
    defaults.update(overrides)
    return Job(**defaults)


# --- partition_jobs ------------------------------------------------------


def test_partition_buckets_by_verdict() -> None:
    apply_job = _make_job(job_id="a", fit_score=8.5, fit_verdict=Verdict.APPLY)
    consider_job = _make_job(job_id="c", fit_score=6.5, fit_verdict=Verdict.CONSIDER)
    skipped_job = _make_job(job_id="s", fit_score=3.0, fit_verdict=Verdict.SKIP)
    applied_job = _make_job(job_id="app", status=Status.APPLIED, applied_at="2026-04-01")

    a, c, s, app = partition_jobs([apply_job, consider_job, skipped_job, applied_job])
    assert [j.job_id for j in a] == ["a"]
    assert [j.job_id for j in c] == ["c"]
    assert [j.job_id for j in s] == ["s"]
    assert [j.job_id for j in app] == ["app"]


def test_partition_applied_status_wins_over_score() -> None:
    """A job marked APPLIED stays in the applied bucket even if score qualifies."""
    j = _make_job(fit_score=9.0, fit_verdict=Verdict.APPLY, status=Status.APPLIED)
    a, _, _, app = partition_jobs([j])
    assert a == []
    assert app == [j]


def test_partition_skipped_status_lands_in_skipped() -> None:
    """A pre-filter-skipped job (status=SKIPPED, no fit_verdict yet) shows
    in the skipped section."""
    j = _make_job(
        pre_filter_verdict=PreFilterVerdict.SKIP,
        pre_filter_reason="requires 10+ YOE",
        status=Status.SKIPPED,
    )
    _, _, s, _ = partition_jobs([j])
    assert s == [j]


def test_partition_sorts_apply_by_descending_score() -> None:
    """Highest fit score first — the digest's headline should be the best fit."""
    j1 = _make_job(job_id="1", fit_score=8.5, fit_verdict=Verdict.APPLY)
    j2 = _make_job(job_id="2", fit_score=9.5, fit_verdict=Verdict.APPLY)
    j3 = _make_job(job_id="3", fit_score=8.0, fit_verdict=Verdict.APPLY)
    a, _, _, _ = partition_jobs([j1, j2, j3])
    assert [j.job_id for j in a] == ["2", "1", "3"]


# --- group_by_company ----------------------------------------------------


def test_group_by_company_preserves_first_seen_order() -> None:
    jobs = [
        _make_job(company="stripe", job_id="s1"),
        _make_job(company="databricks", job_id="d1"),
        _make_job(company="stripe", job_id="s2"),
    ]
    groups = group_by_company(jobs)
    assert [g[0] for g in groups] == ["stripe", "databricks"]
    assert [j.job_id for j in groups[0][1]] == ["s1", "s2"]


def test_group_by_company_empty_input() -> None:
    assert group_by_company([]) == []


# --- end-to-end render --------------------------------------------------


@pytest.fixture
def chrome_prompts() -> list[Any]:
    return list(build_chrome_prompts_for_config(CFG, template_path=CHROME_TEMPLATE))


def test_digest_renders_for_empty_store(chrome_prompts: list[Any]) -> None:
    """Even with zero jobs, the digest should render without error."""
    html = build_digest_html(
        CFG, [], chrome_prompts, template_path=DIGEST_TEMPLATE, today=date(2026, 5, 14)
    )
    assert "Job Digest" in html
    assert "2026-05-14" in html
    assert "0 apply" in html


def test_digest_includes_apply_job(chrome_prompts: list[Any]) -> None:
    job = _make_job(
        fit_score=8.5,
        fit_verdict=Verdict.APPLY,
        fit_justification="Strong stack overlap and right level.",
    )
    html = build_digest_html(CFG, [job], chrome_prompts, template_path=DIGEST_TEMPLATE)
    assert "Software Engineer, ML Platform" in html
    assert "8.5" in html
    assert "Strong stack overlap" in html
    # The apply URL should be linked.
    assert "https://example.com/job/1" in html


def test_digest_shows_bridge_notes_when_present(chrome_prompts: list[Any]) -> None:
    job = _make_job(
        fit_score=8.2,
        fit_verdict=Verdict.APPLY,
        honest_bridge_notes="No production Kafka — frame batch work as transferable.",
    )
    html = build_digest_html(CFG, [job], chrome_prompts, template_path=DIGEST_TEMPLATE)
    assert "Bridge" in html
    assert "No production Kafka" in html


def test_digest_marks_dead_companies(chrome_prompts: list[Any]) -> None:
    """Jobs from dead_companies should get the DEAD badge in the digest.

    The example config has an empty dead_companies list, so we inject one
    here to exercise the DEAD-badge code path.
    """
    # Mutate a copy of CFG's dead_companies for this test only.
    cfg_with_dead = CFG.model_copy(deep=True)
    cfg_with_dead.dead_companies = ["anthropic"]

    job = _make_job(company="anthropic", fit_score=8.5, fit_verdict=Verdict.APPLY)
    html = build_digest_html(
        cfg_with_dead, [job], chrome_prompts, template_path=DIGEST_TEMPLATE
    )
    assert "DEAD" in html


def test_digest_does_not_show_dead_badge_for_live_companies(
    chrome_prompts: list[Any],
) -> None:
    job = _make_job(company="databricks", fit_score=8.5, fit_verdict=Verdict.APPLY)
    html = build_digest_html(CFG, [job], chrome_prompts, template_path=DIGEST_TEMPLATE)
    # The DEAD badge `<span class="dead-badge">DEAD</span>` only appears
    # when the company is in dead_companies. The CSS rule `.dead-badge`
    # is always present in the embedded stylesheet — that's not the same
    # as the badge element being rendered. Check for the badge text.
    assert ">DEAD<" not in html


def test_digest_includes_chrome_prompts(chrome_prompts: list[Any]) -> None:
    html = build_digest_html(CFG, [], chrome_prompts, template_path=DIGEST_TEMPLATE)
    # Each no-API company should appear in the prompts section.
    for entry in CFG.scrapers.no_api:
        assert entry.name in html


def test_digest_escapes_html_in_jd(chrome_prompts: list[Any]) -> None:
    """A JD with <script> tags must not be rendered as live HTML."""
    job = _make_job(
        fit_score=8.5,
        fit_verdict=Verdict.APPLY,
        fit_justification="<script>alert(1)</script>",
    )
    html = build_digest_html(CFG, [job], chrome_prompts, template_path=DIGEST_TEMPLATE)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_digest_lists_skipped_in_collapsed_section(chrome_prompts: list[Any]) -> None:
    skipped = _make_job(
        company="oracle",
        title="Director of Data",
        status=Status.SKIPPED,
        pre_filter_verdict=PreFilterVerdict.SKIP,
        pre_filter_reason="title contains 'director'",
    )
    html = build_digest_html(CFG, [skipped], chrome_prompts, template_path=DIGEST_TEMPLATE)
    assert "<details>" in html
    assert "Director of Data" in html
    assert "director" in html  # reason


def test_digest_lists_applied_section(chrome_prompts: list[Any]) -> None:
    j = _make_job(
        company="stripe",
        status=Status.APPLIED,
        applied_at="2026-04-15",
    )
    html = build_digest_html(CFG, [j], chrome_prompts, template_path=DIGEST_TEMPLATE)
    assert "Already applied" in html
    assert "2026-04-15" in html


def test_digest_counts_in_summary_line(chrome_prompts: list[Any]) -> None:
    jobs = [
        _make_job(job_id="1", fit_score=8.5, fit_verdict=Verdict.APPLY),
        _make_job(job_id="2", fit_score=6.0, fit_verdict=Verdict.CONSIDER),
        _make_job(job_id="3", status=Status.SKIPPED),
        _make_job(job_id="4", status=Status.APPLIED),
    ]
    html = build_digest_html(CFG, jobs, chrome_prompts, template_path=DIGEST_TEMPLATE)
    assert "1 apply" in html
    assert "1 consider" in html
    assert "1 skipped" in html
    assert "1 already applied" in html


def test_digest_resume_command_uses_job_id(chrome_prompts: list[Any]) -> None:
    """The 'Generate resume' code snippet must include the job_id."""
    job = _make_job(job_id="abc123", fit_score=8.5, fit_verdict=Verdict.APPLY)
    html = build_digest_html(CFG, [job], chrome_prompts, template_path=DIGEST_TEMPLATE)
    assert "resume --job-id abc123" in html
