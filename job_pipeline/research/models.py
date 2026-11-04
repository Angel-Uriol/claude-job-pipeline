"""Report contracts: unknowns stay explicit and no hiring probabilities exist."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Finding(BaseModel):
    rating: Literal["below market", "around market", "above market", "unknown"]
    explanation: str
    source_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    # What the rating compares against, e.g. "L4 SWE, SF Bay Area, base salary,
    # levels.fyi 2026". Required for any rating other than unknown.
    cohort: str = ""
    # A short EXACT quote (≤200 chars) from one cited source's text that
    # supports the rating. Checked against the stored excerpt (service.py).
    evidence_quote: str = ""

    @model_validator(mode="after")
    def require_evidence(self) -> Finding:
        if self.rating != "unknown" and not self.source_ids:
            raise ValueError("A market rating needs cited evidence.")
        return self


class Prospects(BaseModel):
    signal: Literal["stronger", "mixed", "stretch", "unknown"]
    reasons: list[str]
    blockers: list[str]
    next_steps: list[str]


class ComparisonRow(BaseModel):
    job_key: str
    salary: Finding
    benefits: Finding
    company_reviews: Finding
    interview_prospects: Prospects
    hiring_prospects: Prospects
    tradeoff: str


class Comparison(BaseModel):
    summary: str
    rows: list[ComparisonRow] = Field(min_length=1, max_length=5)
    methodology: str
    missing_information: list[str]


class PrepQuestion(BaseModel):
    question: str
    kind: Literal["practice", "reported"]
    purpose: str
    answer_outline: str
    evidence_ids: list[str]
    source_ids: list[str]
    gap_to_acknowledge: str
    # Set by the service when the outline needs the candidate's attention.
    verify_note: str = ""


class StudyStep(BaseModel):
    session: str
    focus: str
    tasks: list[str]
    success_check: str


class InterviewPlan(BaseModel):
    company_brief: str
    role_priorities: list[str]
    process: str
    source_ids: list[str]
    questions: list[PrepQuestion] = Field(min_length=3, max_length=15)
    study_plan: list[StudyStep] = Field(min_length=1, max_length=10)
    questions_to_ask: list[str]
    missing_information: list[str]
