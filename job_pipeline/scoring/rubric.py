"""Scoring rubric — weights and verdict thresholds.

This is pure math, fully testable without any LLM calls. Kept in its
own module so the weights live one obvious place: change them here and
both deep-fit and the digest pick up the new behavior.

Weight calibration (from spec):
  stack         0.30   ←  the biggest signal
  level         0.25
  function      0.20
  bridgeability 0.15   ←  honest-gap penalty
  desirability  0.10   ←  smallest signal; ties usually
  ----------------------
  total         1.00

Verdict thresholds are configurable in `config.yaml` under
`scoring.thresholds` — the defaults are 8.0 / 5.0 per spec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Verdict

if TYPE_CHECKING:
    from .deep_fit import DeepFitScores


# Sum is exactly 1.0; spelled out so it's easy to spot a typo.
WEIGHTS: dict[str, float] = {
    "stack": 0.30,
    "level": 0.25,
    "function": 0.20,
    "bridgeability": 0.15,
    "desirability": 0.10,
}


def weighted_fit_score(scores: DeepFitScores) -> float:
    """Compute the weighted average of the five axis scores.

    Result is in [1.0, 10.0] because each input is constrained there.
    Rounded to one decimal so jobs.json stays readable.
    """
    total = (
        scores.stack * WEIGHTS["stack"]
        + scores.level * WEIGHTS["level"]
        + scores.function * WEIGHTS["function"]
        + scores.bridgeability * WEIGHTS["bridgeability"]
        + scores.desirability * WEIGHTS["desirability"]
    )
    return round(total, 1)


def verdict_from_score(
    score: float,
    *,
    apply_threshold: float,
    consider_threshold: float,
) -> Verdict:
    """Map a weighted score to apply / consider / skip.

    Thresholds come from `config.scoring.thresholds`. Default: 8.0 / 5.0.
    """
    if score >= apply_threshold:
        return Verdict.APPLY
    if score >= consider_threshold:
        return Verdict.CONSIDER
    return Verdict.SKIP
