"""Configuration loader.

Two concerns, kept deliberately separate:

1. **profile/config.yaml** — non-secret behavior (profile, scrapers,
   thresholds). Written by the setup questionnaire; edit freely after.
   Loaded via PyYAML + pydantic validation. Tweak this file freely; the
   shape is enforced at startup so typos blow up immediately rather than
   silently breaking a scoring run.

2. **.env** — secrets (API keys) and the Claude auth mode. Loaded via pydantic-settings so anything
   accidentally committed to config.yaml will fail to validate when it
   shows up as an unknown field.

If you add a new field, add it to BOTH the YAML file and the matching
pydantic model below — otherwise the loader will raise on the next run.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import (  # noqa: I001
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from . import paths

# --- secrets (.env) -------------------------------------------------------


class Secrets(BaseSettings):
    """API keys + auth mode, loaded from .env at the repo root.

    Priority order: `.env` file > shell environment > defaults. We invert
    pydantic-settings' default (which prefers shell env vars) because some
    tools export an EMPTY `ANTHROPIC_API_KEY` in the shell, which would
    otherwise shadow the real key in `.env`.
    """

    model_config = SettingsConfigDict(
        env_file=str(paths.ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # How the pipeline talks to Claude:
    #   api_key     — an Anthropic API key (default, recommended)
    #   claude_code — your own locally logged-in Claude Code CLI
    auth_mode: Literal["api_key", "claude_code"] = Field(
        default="api_key", alias="JOB_PIPELINE_AUTH_MODE"
    )
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    # Optional path to the `claude` binary (claude_code mode only).
    claude_cli_path: str = Field(default="", alias="CLAUDE_CLI_PATH")
    # RapidAPI key for the JSearch open-web scraper. Optional — empty
    # means the JSearch source is skipped (warn-and-continue).
    jsearch_api_key: str = Field(default="", alias="JSEARCH_API_KEY")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # dotenv before env so .env values take priority over shell vars.
        return init_settings, dotenv_settings, env_settings, file_secret_settings


# --- project config (config.yaml) -----------------------------------------


class TargetFunction(BaseModel):
    """One kind of role the candidate wants, with how strongly it fits.

    tier A = strongest fit (function axis ~10), B = strong (~7-8),
    C = stretch / career pivot (~4-6). Shown to every scoring prompt.
    """

    name: str
    tier: Literal["A", "B", "C"] = "A"
    notes: str = ""


class Profile(BaseModel):
    """Candidate profile injected into every scoring + search prompt.

    Written by the setup questionnaire. Everything here is about the PERSON;
    pipeline mechanics live in the other sections of config.yaml.
    """

    name: str
    email: str
    phone: str = ""
    linkedin: str = ""
    location: str = ""                 # where they live, e.g. "Austin, TX"
    headline: str = ""                 # one line, e.g. "Backend engineer, 4 yrs"
    years_experience: int = 0
    # Work authorization, in the candidate's words ("US citizen", "H-1B", ...)
    work_authorization: str = ""
    needs_sponsorship: bool = False
    target_levels: list[str] = Field(default_factory=list)   # e.g. ["mid", "senior"]
    target_functions: list[TargetFunction] = Field(default_factory=list)
    must_have_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    excluded_titles: list[str] = Field(default_factory=list)
    # Pre-filter hard-skips JDs asking for MORE years than this.
    # 0 = automatic (years_experience + 5, at least 5).
    max_years_required: int = 0
    # Plain-English location rule, e.g. "Bay Area or fully remote in the US".
    location_preference: str = ""
    salary_floor: str = ""             # free text, e.g. "$150k base"
    # Short facts the scorer should know (signature work, metrics).
    highlights: list[str] = Field(default_factory=list)
    # Honest gaps: skills the candidate does NOT have in production.
    honest_gaps: list[str] = Field(default_factory=list)
    # Extra deal-breakers beyond titles (e.g. "on-call heavy roles").
    deal_breakers: list[str] = Field(default_factory=list)
    # Companies the candidate is most excited about (desirability axis).
    dream_companies: list[str] = Field(default_factory=list)


class GreenhouseEntry(BaseModel):
    """One row of `scrapers.greenhouse`."""

    name: str
    slug: str


class LeverEntry(BaseModel):
    """One row of `scrapers.lever`."""

    name: str
    slug: str


class AshbyEntry(BaseModel):
    """One row of `scrapers.ashby`.

    Ashby's public job board API is `https://api.ashbyhq.com/posting-api/job-board/<slug>`.
    """

    name: str
    slug: str


class RemoteOKEntry(BaseModel):
    """One row of `scrapers.remoteok`.

    RemoteOK is an aggregator (one endpoint, many companies). The
    `name` here is just the label used in scrape progress emits and
    has no effect on the API call. Usually a single entry per pipeline.
    """

    name: str = "remoteok"


class JSearchEntry(BaseModel):
    """One row of `scrapers.jsearch`.

    JSearch (RapidAPI) is a query-driven open-web aggregator pulling
    Google for Jobs results. Each entry is one search. Location is
    encoded in the `query` string ("... in California"); `remote_only`
    additionally constrains server-side.

    `num_pages` is capped 1-20 by the API; each page ≈ 1 request against
    the free-tier quota, so default to 1 and keep the query list short.
    `date_posted` is one of: all | today | 3days | week | month.
    """

    query: str
    date_posted: str = "week"
    num_pages: int = Field(default=1, ge=1, le=20)
    remote_only: bool = False


class NoApiEntry(BaseModel):
    """One row of `scrapers.no_api`.

    These companies have no public ATS API. The digest emits a Chrome
    Claude prompt for each of them so the human can scrape manually.
    """

    name: str
    careers_url: str
    platform: str  # workday | ashby | custom


class Scrapers(BaseModel):
    """All scraper configuration."""

    greenhouse: list[GreenhouseEntry] = Field(default_factory=list)
    lever: list[LeverEntry] = Field(default_factory=list)
    ashby: list[AshbyEntry] = Field(default_factory=list)
    remoteok: list[RemoteOKEntry] = Field(default_factory=list)
    jsearch: list[JSearchEntry] = Field(default_factory=list)
    no_api: list[NoApiEntry] = Field(default_factory=list)


class Thresholds(BaseModel):
    """Fit-score cutoffs. `apply` and `consider` should not overlap."""

    apply: float
    consider: float


class Scoring(BaseModel):
    """Which Claude model runs each scoring stage."""

    pre_filter_model: str = "claude-haiku-5-5"
    deep_fit_model: str = "claude-sonnet-5-5"
    thresholds: Thresholds = Field(default_factory=lambda: Thresholds(apply=8.0, consider=5.0))


class LLMSettings(BaseModel):
    """Models for the non-scoring Claude calls."""

    # Questionnaire, resume parsing, chat → Chrome-run parsing.
    fast_model: str = "claude-haiku-5-5"
    # Company suggestions + anything that benefits from more reasoning.
    smart_model: str = "claude-sonnet-5-5"


class Resume(BaseModel):
    """Resume-generation settings.

    With `tailor_enabled`, Claude honestly reframes each resume's summary +
    bullets toward the target job before the .docx is rendered. Turn it off
    to render the plain deterministic template (no LLM call).
    """

    tailor_enabled: bool = True
    model: str = "claude-sonnet-5-5"


class Tracker(BaseModel):
    """The built-in application tracker (SQLite, shown on the Track tab).

    `send to tracker` copies jobs with these verdicts (and their tailored
    resumes) into the tracker database.
    """

    enabled: bool = True
    db_path: str = "data/tracker.db"
    verdicts: list[str] = Field(default_factory=lambda: ["apply", "consider"])
    # Max roles per company kept in the tracker (avoid spamming one employer).
    max_per_company: int = Field(default=4, ge=1, le=50)


class Filters(BaseModel):
    """Scrape-time filters. Apply before pre-filter to save LLM cost.

    Location is an allowlist: a job passes if its location string contains
    any `location_tokens` entry, or (when `allow_remote`) any `remote_tokens`
    entry without a `hybrid_disqualifiers` entry. Turn the whole location
    check off with `location_filter_enabled: false`.
    """

    location_filter_enabled: bool = True
    allow_remote: bool = True
    max_age_days: int = 20
    location_tokens: list[str] = Field(default_factory=list)
    remote_tokens: list[str] = Field(
        default_factory=lambda: ["remote", "anywhere", "distributed"]
    )
    hybrid_disqualifiers: list[str] = Field(default_factory=lambda: ["hybrid"])


class RateLimits(BaseModel):
    api_calls_per_minute: int
    scraper_delay_seconds: float


class Paths(BaseModel):
    jobs_json: str
    tracker_xlsx: str
    digests_dir: str
    resumes_dir: str


class ChromeClaudeSettings(BaseModel):
    """Caps for the "Run Chrome Claude" browser-search watcher loop.

    The watcher splits a run's companies into batches and works several at
    once in parallel browser tabs. These two knobs bound that:
      - batch_size: companies per batch (one tab handles a batch)
      - max_parallel_batches: batches (tabs) in flight at once
    Defaults of 3 × 3 = up to 9 companies searched simultaneously. Tweak
    here; nothing is hardcoded in the watcher.
    """

    batch_size: int = Field(default=3, ge=1, le=20)
    max_parallel_batches: int = Field(default=3, ge=1, le=10)
    # Max roles to capture per company in a thorough multi-source search.
    max_roles_per_company: int = Field(default=10, ge=1, le=50)
    # Companies to skip in the BROWSER search only (already covered / applied /
    # rejected). Deliberately NOT the global `excluded_companies` — the
    # automated greenhouse/ashby scrapers still cover some of these.
    exclude_companies: list[str] = Field(default_factory=list)


class Config(BaseModel):
    """Top-level pipeline configuration.

    This is the in-memory shape of profile/config.yaml. Anything missing or
    extra in the YAML raises a pydantic ValidationError at load time.
    """

    profile: Profile
    scrapers: Scrapers
    excluded_companies: list[str] = Field(default_factory=list)
    dead_companies: list[str] = Field(default_factory=list)
    filters: Filters = Field(default_factory=Filters)
    scoring: Scoring = Field(default_factory=Scoring)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    resume: Resume = Field(default_factory=Resume)
    tracker: Tracker = Field(default_factory=Tracker)
    rate_limits: RateLimits = Field(
        default_factory=lambda: RateLimits(api_calls_per_minute=30, scraper_delay_seconds=0.5)
    )
    paths: Paths = Field(default_factory=lambda: Paths(
        jobs_json="data/jobs.json",
        tracker_xlsx="data/tracker.xlsx",
        digests_dir="data/digests",
        resumes_dir="outputs/resumes",
    ))
    # Optional — defaults (3 × 3) apply if the YAML omits the section.
    chrome_claude: ChromeClaudeSettings = Field(default_factory=ChromeClaudeSettings)


# --- loaders --------------------------------------------------------------


def load_config(path: str | Path | None = None) -> Config:
    """Read, parse, and validate the profile's `config.yaml`.

    `path` defaults to `profile/config.yaml` (see `paths.config_path`).

    Raises:
        FileNotFoundError: if the file doesn't exist at `path`.
        pydantic.ValidationError: if the YAML is missing required fields
            or has wrong types.
        yaml.YAMLError: if the file isn't valid YAML.
    """
    p = Path(path) if path is not None else paths.config_path()
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Run setup first: open the web UI (make dev) "
            "or run `job-pipeline setup`."
        )
    with p.open("r", encoding="utf-8") as f:
        raw: Any = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{p} did not parse as a YAML mapping")
    return Config(**raw)


def load_secrets() -> Secrets:
    """Read `.env` into a Secrets object. Safe to call before .env exists."""
    return Secrets()
