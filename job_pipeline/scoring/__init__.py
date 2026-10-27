"""Scoring stack — pre-filter (GPT) + deep-fit (Claude).

Two passes per job:

1. **Pre-filter** (cheap, ~$0.0002/job): extracts structured fields and
   gives a pass/skip verdict. Skips are filtered out before deep-fit.
2. **Deep-fit** (more expensive, ~$0.005/job): scores the job against
   a five-axis rubric, returns a weighted fit score and an apply/
   consider/skip verdict.

Public surface:
    rubric.WEIGHTS, rubric.weighted_fit_score, rubric.verdict_from_score
    pre_filter.PreFilterExtraction, pre_filter.PreFilterer,
        pre_filter.apply_hard_skip_rules
    deep_fit.DeepFitResult, deep_fit.DeepFitter
"""

from .deep_fit import DeepFitResult, DeepFitScores, DeepFitter
from .pre_filter import PreFilterer, PreFilterExtraction, apply_hard_skip_rules
from .rubric import WEIGHTS, verdict_from_score, weighted_fit_score

__all__ = [
    "WEIGHTS",
    "DeepFitResult",
    "DeepFitScores",
    "DeepFitter",
    "PreFilterExtraction",
    "PreFilterer",
    "apply_hard_skip_rules",
    "verdict_from_score",
    "weighted_fit_score",
]
