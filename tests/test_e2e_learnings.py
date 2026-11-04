"""Regression tests for the issues found in the Nov 2026 end-to-end test run."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_pipeline.config import load_config
from job_pipeline.models import Job, Source
from job_pipeline.onboarding.interview import (
    FunctionDraft,
    InterviewState,
    ProfileDraft,
    clean_skills,
)
from tests.conftest import EXAMPLE_CONFIG


def _job(title: str, location: str = "San Francisco, CA", company: str = "acme",
         n: int = 1, **kw: Any) -> Job:
    return Job(company=company, job_id=str(n), title=title, source=Source.GREENHOUSE,
               url=f"u{n}", location=location, description="jd",
               discovered_at=datetime.now(UTC).isoformat(), **kw)


# --- skills hygiene (#4, #5, #13) --------------------------------------------


def test_clean_skills_drops_soft_skills_dupes_and_gaps() -> None:
    out = clean_skills(
        ["Python", "python", "Analytical", "C++", "PySpark", "Design skills", "Go"],
        ["C++ in production (academic only)", "Go in production"],
    )
    assert out == ["python", "pyspark"]


def test_clean_skills_word_boundaries() -> None:
    # "go" gap must not remove "django"; "c" gap must not remove "c++"... keep exact words.
    assert clean_skills(["django", "mongodb"], ["go"]) == ["django", "mongodb"]


# --- location rules (#6, #10) ---------------------------------------------


def test_remote_excluded_tokens_block_non_us_remote() -> None:
    from job_pipeline.scrapers.filters import passes_location_filter

    cfg = load_config(EXAMPLE_CONFIG)
    assert passes_location_filter("Remote - India", cfg) is True  # no rule yet
    cfg.filters.remote_excluded_tokens = ["india", "canada"]
    assert passes_location_filter("Remote - India", cfg) is False
    assert passes_location_filter("Remote, US", cfg) is True


def test_writer_sets_us_only_remote_and_search_location() -> None:
    from job_pipeline.onboarding.writer import build_config

    draft = ProfileDraft(
        name="A", email="a@example.com", location="Miami, FL",
        search_locations=["San Francisco Bay Area"], remote_country="US",
        allow_remote=True, location_tokens=["san francisco"],
        target_functions=[FunctionDraft(name="Data Engineer", tier="A")],
    )
    cfg = build_config(draft, [])
    assert "india" in cfg["filters"]["remote_excluded_tokens"]
    queries = [q["query"] for q in cfg["scrapers"]["jsearch"]]
    assert "Data Engineer in San Francisco Bay Area" in queries  # not Miami (#8)
    assert "remote_country" not in cfg["profile"]


def test_resume_location_overrides_header(tmp_path: Path) -> None:
    from job_pipeline.onboarding.writer import build_files

    state = InterviewState(draft=ProfileDraft(
        name="A", email="a@example.com", location="Miami, FL",
        resume_location="Bay Area, CA"))
    files = build_files(state)
    assert "location: Bay Area, CA" in files["resume_content.yaml"]


# --- market scan as a mini pipeline (#6, #11) ------------------------------------


def test_market_filter_applies_location_and_title_threshold() -> None:
    from job_pipeline.onboarding.market import filter_jobs

    draft = ProfileDraft(search_keywords=["data engineer"], location_tokens=["san francisco"],
                         allow_remote=True, remote_country="US")
    jobs = [
        _job("Data Engineer", "San Francisco, CA", n=1),
        _job("Data Engineer", "Amsterdam, Netherlands", n=2),
        _job("Data Engineer", "Remote - India", n=3),
        _job("Data Engineer", "Remote, US", n=4),
        _job("Data Center Controls Engineer", "San Francisco, CA", n=5),
    ]
    assert [j.url for j in filter_jobs(jobs, draft, exclude_urls=set())] == ["u1", "u4"]


def test_rate_jobs_orders_by_score_and_survives_llm_failure() -> None:
    from job_pipeline.llm import LLMError
    from job_pipeline.onboarding.market import Rating, Ratings, rate_jobs

    jobs = [_job("A", n=1), _job("B", n=2)]

    class LLM:
        auth_mode = "api_key"

        def structured(self, *a: Any, **k: Any) -> Ratings:
            return Ratings(ratings=[Rating(index=1, score=3, reason="meh"),
                                    Rating(index=2, score=9, reason="great")])

    rated = rate_jobs(jobs, ProfileDraft(), llm=LLM(), model="m")  # type: ignore[arg-type]
    assert [(j.url, s) for j, s, _r in rated] == [("u2", 9), ("u1", 3)]

    class Broken:
        auth_mode = "api_key"

        def structured(self, *a: Any, **k: Any) -> Ratings:
            raise LLMError("down")

    fallback = rate_jobs(jobs, ProfileDraft(), llm=Broken(), model="m")  # type: ignore[arg-type]
    assert [j.url for j, _s, _r in fallback] == ["u1", "u2"]


# --- scoring cap + rounding (#9, #14) ------------------------------------------


def test_cap_targets_prefers_target_companies_then_newest() -> None:
    from job_pipeline.runner import _cap_targets

    cfg = load_config(EXAMPLE_CONFIG)
    cfg.scoring.max_jobs_per_run = 2
    jobs = [
        _job("a", n=1, posted_date="2026-10-01"),
        _job("b", n=2, posted_date="2026-10-20"),
        _job("c", n=3, posted_date="2026-09-01", is_target_company=True),
    ]
    events: list[Any] = []
    out = _cap_targets(jobs, cfg, "pre-filter", events.append)
    assert [j.url for j in out] == ["u3", "u2"]
    assert "top 2 of 3" in events[0].message
    cfg.scoring.max_jobs_per_run = 0
    assert len(_cap_targets(jobs, cfg, "pre-filter", None)) == 3


def test_years_cutoff_is_tighter() -> None:
    from job_pipeline.prompts import max_years_required

    p = load_config(EXAMPLE_CONFIG).profile
    p.years_experience, p.max_years_required = 3, 0
    assert max_years_required(p) == 6  # 7+-year roles now hard-skip for a 3-yr candidate


# --- resume tailoring skills (#17) --------------------------------------------


def test_feature_skills_reorders_and_drops_irrelevant_never_adds() -> None:
    from job_pipeline.resume.tailor import feature_skills

    groups = [
        {"group": "Languages", "items": ["C++", "Python", "Verilog", "SQL"]},
        {"group": "Data Platform", "items": ["PySpark", "Airflow", "Tableau"]},
        {"group": "Other", "items": ["PCB Design"]},
    ]
    out = feature_skills(groups, ["PySpark", "Python", "SQL", "Airflow", "Kubernetes"])
    assert out == [
        {"group": "Data Platform", "items": ["PySpark", "Airflow"]},
        {"group": "Languages", "items": ["Python", "SQL"]},
    ]
    # Too few featured → reorder only, keep everything.
    kept = feature_skills(groups, ["SQL"])
    assert sum(len(g["items"]) for g in kept) == 8 and kept[0]["items"][0] == "SQL"
    assert feature_skills(groups, []) == groups


# --- UI summary + premium (#20, Angel's request) ------------------------------------


def test_result_summary_formats_counts() -> None:
    from job_pipeline.ui.server import _result_summary

    assert _result_summary({"exported": 6, "imported": 6, "skipped": {}}) == \
        " — exported 6, imported 6"
    assert _result_summary({"output": "x"}) == ""


def test_premium_search_queues_profile_tagged_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    import job_pipeline.chrome_queue as cq
    from job_pipeline.ui import create_app

    monkeypatch.setenv("JOB_PIPELINE_PROFILE_DIR", str(tmp_path / "p"))
    monkeypatch.setattr(cq, "_DEFAULT_BASE", tmp_path / "runs")
    state = InterviewState(companies=[{"name": "NVIDIA", "platform": None,
                                       "careers_url": "https://nvidia.example/jobs"}])
    state.save(tmp_path / "p" / ".interview.json")
    client = TestClient(create_app(config_path=tmp_path / "p" / "config.yaml"))
    assert client.post("/api/setup/premium", json={"companies": []}).status_code == 400
    body = client.post("/api/setup/premium", json={"companies": ["NVIDIA"]}).json()
    rec = body["queued"]
    assert rec["premium"] is True and rec["ad_hoc_companies"] == ["NVIDIA"]
    assert "nvidia.example" in rec["extra_notes"]
    assert "/loop" in body["watcher_command"]
    assert InterviewState.load(tmp_path / "p" / ".interview.json").companies[0]["premium"]
