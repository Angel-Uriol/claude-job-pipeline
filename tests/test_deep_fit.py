"""Tests for the deep-fit stage.

We don't call Claude. Tests inject a `FakeDeepFitClient` that returns
canned `DeepFitResult` objects so the prompt formatting, validation,
and scoring math can all be exercised without a real API key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from job_pipeline.config import Profile, load_config
from job_pipeline.models import Job, Source
from job_pipeline.scoring.deep_fit import (
    DESCRIPTION_TRUNCATE,
    ClaudeDeepFitClient,
    DeepFitResult,
    DeepFitScores,
    DeepFitter,
    _format_extracted,
    _RawDeepFitResponse,
    _RawDeepFitScores,
)
from tests.conftest import EXAMPLE_CONFIG

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
CFG = load_config(EXAMPLE_CONFIG)


# --- fake client ----------------------------------------------------------


class FakeDeepFitClient:
    """Returns a canned result; records each prompt seen."""

    def __init__(self, response: DeepFitResult) -> None:
        self.response = response
        self.prompts_seen: list[str] = []

    def call(self, prompt: str) -> DeepFitResult:
        self.prompts_seen.append(prompt)
        return self.response


def _make_job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "company": "databricks",
        "job_id": "1",
        "title": "Software Engineer, ML Platform",
        "team": "ML",
        "source": Source.GREENHOUSE,
        "url": "u",
        "location": "San Francisco, CA",
        "discovered_at": datetime.now(UTC).isoformat(),
        "description": "Build ML data infrastructure with PySpark and Airflow.",
        "extracted": {
            "level": "L4",
            "years_required": 4,
            "tech_stack": ["python", "pyspark"],
            "verdict": "pass",
        },
    }
    defaults.update(overrides)
    return Job(**defaults)


def _good_scores() -> DeepFitScores:
    return DeepFitScores(stack=9.0, level=8.0, function=9.0, bridgeability=7.0, desirability=8.0)


def _good_result() -> DeepFitResult:
    return DeepFitResult(
        scores=_good_scores(),
        justification="Strong stack and function fit; level is a stretch but plausible.",
        honest_bridge_notes=None,
    )


# --- DeepFitScores validation --------------------------------------------


def test_score_below_zero_rejected() -> None:
    """Bounds are 0.0–10.0 (relaxed from the original 1.0–10.0 because
    Claude sometimes returns 0.x for very-bad-fit axes)."""
    with pytest.raises(ValidationError):
        DeepFitScores(stack=-0.5, level=8, function=8, bridgeability=8, desirability=8)


def test_score_above_max_rejected() -> None:
    with pytest.raises(ValidationError):
        DeepFitScores(stack=11, level=8, function=8, bridgeability=8, desirability=8)


def test_score_boundary_values_accepted() -> None:
    """1.0 and 10.0 are inclusive bounds — both must accept."""
    DeepFitScores(stack=1.0, level=10.0, function=1.0, bridgeability=10.0, desirability=1.0)


# --- DeepFitter prompt formatting -----------------------------------------


def test_deep_fitter_formats_prompt_with_job_fields() -> None:
    template = (
        "Co: {company}\nTitle: {title}\nLoc: {location}\n"
        "Extr: {extracted}\nJD: {description}"
    )
    client = FakeDeepFitClient(_good_result())
    fitter = DeepFitter(client, template)

    profile: Profile = CFG.profile
    fitter.score(_make_job(title="ML Eng", location="NYC", company="stripe"), profile)

    prompt = client.prompts_seen[0]
    assert "Co: stripe" in prompt
    assert "Title: ML Eng" in prompt
    assert "Loc: NYC" in prompt


def test_deep_fitter_includes_extracted_blob() -> None:
    template = "{extracted}"
    client = FakeDeepFitClient(_good_result())
    fitter = DeepFitter(client, template)
    fitter.score(_make_job(), CFG.profile)
    # The extracted JSON should be present in the prompt.
    prompt = client.prompts_seen[0]
    assert "L4" in prompt
    assert "pyspark" in prompt


def test_deep_fitter_handles_missing_extracted() -> None:
    """Jobs that hit deep-fit without pre-filter extraction must still work."""
    template = "{extracted}"
    client = FakeDeepFitClient(_good_result())
    fitter = DeepFitter(client, template)
    fitter.score(_make_job(extracted=None), CFG.profile)
    assert "no pre-filter" in client.prompts_seen[0].lower()


def test_deep_fitter_truncates_long_description() -> None:
    template = "{description}"
    client = FakeDeepFitClient(_good_result())
    fitter = DeepFitter(client, template)
    fitter.score(_make_job(description="x" * (DESCRIPTION_TRUNCATE * 2)), CFG.profile)
    assert len(client.prompts_seen[0]) == DESCRIPTION_TRUNCATE


def test_deep_fitter_loads_from_file() -> None:
    """The shipped prompt file should load and substitute placeholders."""
    client = FakeDeepFitClient(_good_result())
    fitter = DeepFitter.from_file(client, PROMPTS / "deep_fit.txt")
    fitter.score(_make_job(), CFG.profile)
    prompt = client.prompts_seen[0]
    assert "Software Engineer, ML Platform" in prompt
    assert "San Francisco, CA" in prompt
    assert "databricks" in prompt


# --- _format_extracted ---------------------------------------------------


def test_format_extracted_empty_dict() -> None:
    assert "no pre-filter" in _format_extracted({}).lower()


def test_format_extracted_none() -> None:
    assert "no pre-filter" in _format_extracted(None).lower()


def test_format_extracted_produces_json() -> None:
    out = _format_extracted({"level": "L4", "years": 3})
    assert '"level"' in out
    assert '"L4"' in out


# --- ClaudeDeepFitClient (structured output + clamp) ----------------------


def test_claude_deepfit_clamps_out_of_range_scores() -> None:
    """Claude can overshoot the 0-10 band; the client clamps before
    building the strict DeepFitResult."""
    loose = _RawDeepFitResponse(
        scores=_RawDeepFitScores(
            stack=12.0, level=-3.0, function=5.0, bridgeability=8.0, desirability=99.0
        ),
        justification="ok",
        honest_bridge_notes=None,
    )
    llm = MagicMock()
    llm.structured.return_value = loose
    client = ClaudeDeepFitClient(llm=llm, model="claude-sonnet-5-5")

    result = client.call("prompt")
    assert result.scores.stack == 10.0  # clamped down from 12
    assert result.scores.level == 0.0  # clamped up from -3
    assert result.scores.function == 5.0  # unchanged
    assert result.scores.desirability == 10.0  # clamped down from 99
    # Forced-tool name matches the prompt's instruction.
    assert llm.structured.call_args.kwargs["tool_name"] == "submit_fit_score"


def test_claude_deepfit_propagates_llm_errors() -> None:
    from job_pipeline.llm import LLMError

    llm = MagicMock()
    llm.structured.side_effect = LLMError("boom")
    client = ClaudeDeepFitClient(llm=llm, model="m")
    with pytest.raises(LLMError, match="boom"):
        client.call("prompt")
