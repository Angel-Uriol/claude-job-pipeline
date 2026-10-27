"""Pipeline data model.

Everything that moves through the pipeline — a job posting, a scoring result,
a configured company — is one of the pydantic models defined here. Using
pydantic (instead of stdlib dataclasses) gives us:

- validation on construction (a bad LLM JSON response blows up loudly),
- JSON round-tripping via .model_dump(mode="json") / Model(**data),
- enum-aware serialization out of the box.

If you're tweaking content the model doesn't carry yet, add a field here
FIRST, then bump `SCHEMA_VERSION` in store.py if existing jobs.json files
would no longer load cleanly.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --- enums ----------------------------------------------------------------
#
# String-valued enums so they survive JSON round-trips unchanged and keep
# the Excel tracker's color-coding stable across runs.


class Status(StrEnum):
    """Job lifecycle status.

    `new`     — just scraped, not pre-filtered yet
    `pending` — passed pre-filter and deep-fit; awaiting your decision
    `skipped` — auto-rejected by pre-filter or deep-fit verdict
    everything else — set manually as you work the application
    """

    NEW = "new"
    PENDING = "pending"
    APPLIED = "applied"
    RESUME_ONLY = "resume_only"
    ASSESSMENT_PASSED = "assessment_passed"
    REJECTED = "rejected"
    AUTO_REJECTED = "auto_rejected"
    DEAD = "dead"
    MISMATCH = "mismatch"
    SKIPPED = "skipped"
    EXCLUDED = "excluded"
    # User reviewed the job and isn't interested. Soft-hidden from the
    # default view but kept in the store so a re-scrape doesn't resurface
    # it (status survives upsert merges). Distinct from auto SKIPPED.
    DISMISSED = "dismissed"


class Source(StrEnum):
    """Where a job posting came from."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    REMOTEOK = "remoteok"
    JSEARCH = "jsearch"
    CHROME_CLAUDE = "chrome_claude"
    MANUAL = "manual"


class Verdict(StrEnum):
    """Output of the Claude deep-fit pass."""

    APPLY = "apply"
    CONSIDER = "consider"
    SKIP = "skip"


class PreFilterVerdict(StrEnum):
    """Output of the Claude pre-filter pass."""

    PASS = "pass"
    SKIP = "skip"


# --- scoring --------------------------------------------------------------


class ScoreResult(BaseModel):
    """Per-axis scores from the deep-fit rubric. Each is 1.0–10.0.

    The weighted average lives on Job.fit_score, not here. Keeping the
    sub-scores around lets the digest show *why* a job scored the way it did.
    """

    stack: float = Field(ge=0.0, le=10.0)
    level: float = Field(ge=0.0, le=10.0)
    function: float = Field(ge=0.0, le=10.0)
    bridgeability: float = Field(ge=0.0, le=10.0)
    desirability: float = Field(ge=0.0, le=10.0)


# --- job ------------------------------------------------------------------


class Job(BaseModel):
    """One job posting in any state.

    Fields are organized into four bands: identity, posting metadata,
    scoring, lifecycle. The split mirrors which pipeline phase writes to
    each band — scrape writes identity+metadata, pre-filter writes
    `extracted` and `pre_filter_*`, deep-fit writes `fit_*`, and lifecycle
    fields are mostly set by you (the human) after applying.
    """

    # Validating assignment catches accidental string-where-enum-expected
    # mistakes when the pipeline mutates a job in place.
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    # --- identity ---------------------------------------------------------
    company: str
    job_id: str | None = None  # ATS-provided ID, when available
    title: str
    team: str | None = None
    source: Source

    # --- posting metadata -------------------------------------------------
    url: str
    location: str
    posted_date: str | None = None  # ISO 8601 if the ATS gave us one
    discovered_at: str  # ISO 8601 — when the pipeline first saw this job

    # --- raw / extracted --------------------------------------------------
    description: str
    # Pre-filter output: {level, years_required, degree_required,
    # visa_stance, tech_stack, ...}. Untyped here so the pre-filter can
    # evolve without forcing a schema migration on jobs.json.
    extracted: dict[str, Any] | None = None

    # --- scoring ----------------------------------------------------------
    pre_filter_verdict: PreFilterVerdict | None = None
    pre_filter_reason: str | None = None
    fit_score: float | None = None  # weighted average, 1.0–10.0
    fit_verdict: Verdict | None = None
    fit_justification: str | None = None
    fit_subscores: ScoreResult | None = None
    honest_bridge_notes: str | None = None

    # Target-company overlay (see target_companies.yaml). Set when the job's
    # company is on the target_companies.yaml list. `target_tier` is 1
    # (strongest) .. 5; a tier-weighted bump is folded into `fit_score` at
    # deep-fit time. Default False/None so old jobs.json loads unchanged.
    is_target_company: bool = False
    target_tier: int | None = None

    # --- lifecycle --------------------------------------------------------
    status: Status = Status.NEW
    resume_path: str | None = None
    applied_at: str | None = None  # ISO 8601, set when you apply
    notes: str | None = None
    starred: bool = False  # personal flag, toggled from the UI

    # --- behavior ---------------------------------------------------------

    def dedup_key(self) -> tuple[str, str]:
        """Stable identity used by the store to detect duplicates.

        Two jobs collide if either rule fires:
          - same `(company, job_id)` when a job_id exists, or
          - same `(company, title, team)` when no job_id is available.

        The keys are lowercased so casing variations across boards don't
        create phantom duplicates ("Lyft" vs "lyft", "ML Platform" vs
        "ml platform").
        """
        company = self.company.lower()
        if self.job_id:
            return (company, self.job_id)
        team = (self.team or "").lower()
        title = self.title.lower()
        # Combine title and team into one string so the tuple shape stays
        # `(str, str)` regardless of which branch we took.
        return (company, f"{title}|{team}")


# --- company --------------------------------------------------------------


class Company(BaseModel):
    """One configured company.

    For Greenhouse/Lever entries, `slug` is set and `careers_url` may be
    absent. For no-API entries, the opposite — we have the careers URL
    but no slug.
    """

    name: str
    slug: str | None = None
    careers_url: str | None = None
    platform: str | None = None  # greenhouse | lever | workday | ashby | custom
