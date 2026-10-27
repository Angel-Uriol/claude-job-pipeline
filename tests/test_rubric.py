"""Tests for the scoring rubric — pure math, no LLM calls."""

from __future__ import annotations

import pytest

from job_pipeline.models import Verdict
from job_pipeline.scoring import WEIGHTS, verdict_from_score, weighted_fit_score
from job_pipeline.scoring.deep_fit import DeepFitScores

# --- weights --------------------------------------------------------------


def test_weights_sum_to_one() -> None:
    """If this fails, the weighted score isn't on a clean 1-10 scale."""
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_weights_match_spec() -> None:
    """Weights from spec Section 6 — don't drift without intent."""
    assert WEIGHTS == {
        "stack": 0.30,
        "level": 0.25,
        "function": 0.20,
        "bridgeability": 0.15,
        "desirability": 0.10,
    }


# --- weighted_fit_score ---------------------------------------------------


def _scores(
    stack: float, level: float, function: float, bridge: float, desire: float
) -> DeepFitScores:
    return DeepFitScores(
        stack=stack, level=level, function=function, bridgeability=bridge, desirability=desire
    )


def test_weighted_max_is_10() -> None:
    s = _scores(10.0, 10.0, 10.0, 10.0, 10.0)
    assert weighted_fit_score(s) == 10.0


def test_weighted_min_is_1() -> None:
    s = _scores(1.0, 1.0, 1.0, 1.0, 1.0)
    assert weighted_fit_score(s) == 1.0


def test_weighted_known_combo() -> None:
    """Hand-computed: 0.3*8 + 0.25*7 + 0.2*9 + 0.15*6 + 0.1*5 = 7.35

    Python's `round` uses banker's-rounding and float representation of
    7.35 lands just below the boundary, so it rounds down to 7.3. That's
    expected — we just want one-decimal precision for readability.
    """
    s = _scores(8.0, 7.0, 9.0, 6.0, 5.0)
    assert weighted_fit_score(s) == pytest.approx(7.35, abs=0.05)


def test_weighted_rounding_to_one_decimal() -> None:
    """jobs.json stays readable when scores aren't 15-digit floats."""
    s = _scores(7.5, 7.5, 7.5, 7.5, 7.5)
    score = weighted_fit_score(s)
    # Score should have at most one decimal place.
    assert str(score) == "7.5" or len(str(score).split(".")[-1]) <= 1


# --- verdict_from_score ---------------------------------------------------


@pytest.mark.parametrize("score", [8.0, 8.5, 9.0, 10.0])
def test_verdict_apply(score: float) -> None:
    assert verdict_from_score(score, apply_threshold=8.0, consider_threshold=5.0) == Verdict.APPLY


@pytest.mark.parametrize("score", [5.0, 6.0, 7.0, 7.9])
def test_verdict_consider(score: float) -> None:
    result = verdict_from_score(score, apply_threshold=8.0, consider_threshold=5.0)
    assert result == Verdict.CONSIDER


@pytest.mark.parametrize("score", [1.0, 3.0, 4.9])
def test_verdict_skip(score: float) -> None:
    assert verdict_from_score(score, apply_threshold=8.0, consider_threshold=5.0) == Verdict.SKIP


def test_verdict_boundary_at_consider_threshold() -> None:
    """`>=` semantics: 5.0 exactly → consider, not skip."""
    assert verdict_from_score(5.0, apply_threshold=8.0, consider_threshold=5.0) == Verdict.CONSIDER


def test_verdict_boundary_at_apply_threshold() -> None:
    """`>=` semantics: 8.0 exactly → apply, not consider."""
    assert verdict_from_score(8.0, apply_threshold=8.0, consider_threshold=5.0) == Verdict.APPLY


def test_verdict_uses_configurable_thresholds() -> None:
    """If user tightens thresholds, the verdict shifts."""
    # Same score but thresholds bumped: 7.5 used to be "consider" with 5.0
    # cutoff; now 7.0 cutoff for consider means 6.9 → skip.
    assert verdict_from_score(6.9, apply_threshold=9.0, consider_threshold=7.0) == Verdict.SKIP
    assert verdict_from_score(7.0, apply_threshold=9.0, consider_threshold=7.0) == Verdict.CONSIDER
    assert verdict_from_score(9.0, apply_threshold=9.0, consider_threshold=7.0) == Verdict.APPLY
