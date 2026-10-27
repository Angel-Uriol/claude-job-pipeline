"""Tests for config.yaml loading and validation.

The shipped `config.yaml` should always validate — that's a release gate.
The pydantic schema should reject malformed input loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from job_pipeline.config import Config, load_config

# The shipped EXAMPLE profile config must always validate — release gate.
REPO_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_CONFIG = REPO_ROOT / "examples" / "profile" / "config.yaml"


# --- happy path -----------------------------------------------------------


def test_example_config_loads() -> None:
    cfg = load_config(SHIPPED_CONFIG)
    assert isinstance(cfg, Config)


def test_example_config_profile() -> None:
    cfg = load_config(SHIPPED_CONFIG)
    assert cfg.profile.email.endswith("@example.com")  # fictional, never a real person
    assert cfg.profile.needs_sponsorship is False
    assert "python" in cfg.profile.must_have_skills
    assert {f.tier for f in cfg.profile.target_functions} == {"A", "B", "C"}


def test_example_config_uses_claude_models_only() -> None:
    cfg = load_config(SHIPPED_CONFIG)
    for model in (cfg.scoring.pre_filter_model, cfg.scoring.deep_fit_model,
                  cfg.resume.model, cfg.llm.fast_model, cfg.llm.smart_model):
        assert model.startswith("claude-")


def test_example_config_scoring_thresholds() -> None:
    cfg = load_config(SHIPPED_CONFIG)
    assert cfg.scoring.thresholds.apply == 8.0
    assert cfg.scoring.thresholds.consider == 5.0


def test_example_config_jsearch_queries() -> None:
    cfg = load_config(SHIPPED_CONFIG)
    assert len(cfg.scrapers.jsearch) >= 1
    allowed_dates = {"all", "today", "3days", "week", "month"}
    for entry in cfg.scrapers.jsearch:
        assert entry.query.strip(), "every JSearch entry needs a non-empty query"
        assert 1 <= entry.num_pages <= 20
        assert entry.date_posted in allowed_dates


def test_minimal_config_gets_defaults(tmp_path: Path) -> None:
    """Only `profile` + `scrapers` are required; everything else defaults."""
    p = tmp_path / "c.yaml"
    p.write_text("profile:\n  name: A\n  email: a@example.com\nscrapers: {}\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.tracker.db_path == "data/tracker.db"
    assert cfg.filters.allow_remote is True
    assert cfg.scoring.deep_fit_model.startswith("claude-")


def test_default_path_is_profile_dir() -> None:
    from job_pipeline import paths

    # conftest points JOB_PIPELINE_PROFILE_DIR at examples/profile.
    assert paths.config_path() == SHIPPED_CONFIG
    assert isinstance(load_config(), Config)


# --- failure modes --------------------------------------------------------


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_load_rejects_missing_required_field(tmp_path: Path) -> None:
    """Pydantic should reject a config that's missing required sections."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("profile:\n  name: Test\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(bad)


def test_load_rejects_non_mapping(tmp_path: Path) -> None:
    """A YAML file that parses to a list (not a dict) should be rejected."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        load_config(bad)


def test_load_rejects_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("profile:\n  name: [unterminated\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_config(bad)


def test_thresholds_must_be_floats(tmp_path: Path) -> None:
    """A string in a float field should fail validation."""
    bad = tmp_path / "bad.yaml"
    # Build a minimal-but-otherwise-valid config with one bad value.
    bad_yaml = """
profile:
  name: Test
  email: test@example.com
  linkedin: https://example.com
  years_experience: 3
  target_levels: []
  target_functions: []
  must_have_skills: []
  preferred_skills: []
  excluded_titles: []
scrapers:
  greenhouse: []
  lever: []
  no_api: []
excluded_companies: []
dead_companies: []
scoring:
  pre_filter_model: x
  deep_fit_model: y
  thresholds:
    apply: "not a number"
    consider: 5.0
rate_limits:
  api_calls_per_minute: 30
  scraper_delay_seconds: 0.5
paths:
  jobs_json: a
  tracker_xlsx: b
  digests_dir: c
  resumes_dir: d
"""
    bad.write_text(bad_yaml, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(bad)
