"""Pure orchestration for each pipeline phase.

This module is the SHARED core between the CLI and the web UI. Each
`run_*` function:

- accepts a loaded `Config` and a loaded `JobStore` (and clients/secrets
  where LLM calls are needed),
- mutates the store in place,
- emits per-item progress events via an optional `progress` callback,
- returns a count dict the caller can show/log/respond with.

The caller is responsible for `store.save()` after `run_*` returns
(so we can crash mid-run without corrupting jobs.json). Both the CLI
wrapper in `cli.py` and the UI endpoints in `ui/server.py` do this.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    # Type-only import — avoids pulling python-docx / anthropic into the
    # runner module at import time (resume rendering is invoked lazily).
    from .resume import ResumeTailorer

from .config import Config, Secrets
from .digest import build_chrome_prompts_for_config, build_digest_html
from .llm import ClaudeClient, build_llm
from .models import Job, PreFilterVerdict, ScoreResult, Source, Status
from .prompts import render_prompt_file
from .scoring import (
    DeepFitter,
    PreFilterer,
    apply_hard_skip_rules,
    verdict_from_score,
    weighted_fit_score,
)
from .scoring.deep_fit import ClaudeDeepFitClient
from .scoring.pre_filter import ClaudePreFilterClient
from .scrapers import JSearchScraper, get_scraper
from .scrapers.base import HTTP_TIMEOUT_SECONDS
from .scrapers.filters import apply_filters
from .seen_ledger import SeenLedger, ledger_path_for
from .store import JobStore
from .target_companies import TargetCompanyIndex, TargetMatch, default_target_index
from .tracker import write_tracker

log = logging.getLogger(__name__)


# --- progress events ----------------------------------------------------


@dataclass(frozen=True)
class ProgressEvent:
    """One event emitted by a pipeline phase. Consumed by CLI (logger)
    or UI (SSE stream)."""

    # `phase` values: scrape | pre-filter | deep-fit | digest | tracker | done | error
    phase: str
    message: str  # human-readable, shown in UI status strip
    i: int | None = None  # 1-indexed position in the current loop
    total: int | None = None  # total items in the loop
    item: str | None = None  # short label for the current item
    extra: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], None]


def _emit(progress: ProgressCallback | None, **kwargs: Any) -> None:
    """Helper that no-ops when no callback is set — keeps call sites tidy."""
    if progress is not None:
        progress(ProgressEvent(**kwargs))


# --- target-company overlay ---------------------------------------------


def _tag_target_company(job: Job, index: TargetCompanyIndex) -> TargetMatch | None:
    """Flag the job if its company is on the target_companies.yaml list.

    Sets `is_target_company` / `target_tier` in place and returns the match
    (or None). The fit-score bump is applied separately, at deep-fit time —
    see `run_deep_fit`.
    """
    match = index.match(job.company)
    job.is_target_company = match is not None
    job.target_tier = match.tier if match is not None else None
    return match


# --- scrape -------------------------------------------------------------


def run_scrape(
    cfg: Config,
    store: JobStore,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, int]:
    """Pull jobs from every configured Greenhouse, Lever, Ashby, and
    RemoteOK board. (JSearch is on-demand — see `run_jsearch`.)

    Returns counts: inserted / updated / excluded / filtered / errors /
    deduped. The store is mutated in place; caller must `save()`.
    """
    counts: dict[str, int] = {
        "inserted": 0,
        "updated": 0,
        "excluded": 0,
        "filtered": 0,
        "errors": 0,
        "deduped": 0,
        "seen_skipped": 0,
    }

    # Seen-ledger: skip postings the user already reviewed (even if their row
    # was later deleted from jobs.json). `store_keys` lets us tell a genuinely
    # new posting from one that's still present (which the store dedups itself).
    ledger = SeenLedger(ledger_path_for(cfg.paths.jobs_json))
    ledger.load()
    store_keys: set[tuple[str, str]] = {j.dedup_key() for j in store.jobs}

    # Target-company overlay: flag jobs from your target_companies.yaml list.
    target_index = default_target_index()

    # Build flat (platform, name, slug) list so the progress index is meaningful.
    # `slug` is unused for aggregator-style sources (RemoteOK) — we pass the
    # entry name there so logging is still meaningful.
    work: list[tuple[str, str, str]] = []
    for gh in cfg.scrapers.greenhouse:
        work.append(("greenhouse", gh.name, gh.slug))
    for lv in cfg.scrapers.lever:
        work.append(("lever", lv.name, lv.slug))
    for ab in cfg.scrapers.ashby:
        work.append(("ashby", ab.name, ab.slug))
    for ro in cfg.scrapers.remoteok:
        # RemoteOK is an aggregator — slug is unused by the scraper.
        work.append(("remoteok", ro.name, ro.name))

    if not work:
        _emit(
            progress,
            phase="scrape",
            message="No scrapers configured.",
            i=0,
            total=0,
        )
        return counts

    for i, (platform, name, slug) in enumerate(work, start=1):
        _emit(
            progress,
            phase="scrape",
            i=i,
            total=len(work),
            item=name,
            message=f"Scraping {name} ({i}/{len(work)})",
        )
        try:
            scraper = get_scraper(platform, name, slug)
            jobs = scraper.fetch()
        except Exception as e:
            counts["errors"] += 1
            _emit(
                progress,
                phase="scrape",
                i=i,
                total=len(work),
                item=name,
                message=f"Scrape failed for {name}: {e}",
                extra={"error": True},
            )
            time.sleep(cfg.rate_limits.scraper_delay_seconds)
            continue

        per_board = {"inserted": 0, "updated": 0, "excluded": 0, "filtered": 0}
        for job in jobs:
            key = job.dedup_key()
            # Already reviewed in a past run and no longer in the store →
            # don't resurface it. (Jobs still in the store are handled by
            # the store's own dedup, e.g. dismissed tombstones.)
            if key not in store_keys and ledger.contains(key):
                counts["seen_skipped"] += 1
                continue
            result = apply_filters(job, cfg)
            if not result.passed:
                per_board["filtered"] += 1
                counts["filtered"] += 1
                continue
            _tag_target_company(job, target_index)
            outcome = store.upsert(job)
            per_board[outcome] += 1
            counts[outcome] = counts.get(outcome, 0) + 1
            # Remember every job that reaches the store (insert or update).
            ledger.add(key)
            store_keys.add(key)

        _emit(
            progress,
            phase="scrape",
            i=i,
            total=len(work),
            item=name,
            message=(
                f"{name}: +{per_board['inserted']} new, "
                f"{per_board['updated']} updated, "
                f"{per_board['filtered']} filtered"
            ),
            extra={"per_board": per_board},
        )
        time.sleep(cfg.rate_limits.scraper_delay_seconds)

    ledger.save()

    # Collapse aggregator-sourced dupes (RemoteOK here) of direct-ATS jobs.
    removed = _dedup_cross_source(store)
    counts["deduped"] += removed
    if removed:
        _emit(
            progress,
            phase="scrape",
            message=f"Cross-source dedup: removed {removed} aggregator duplicate(s).",
            extra={"deduped": removed},
        )

    return counts


# --- cross-source dedup -------------------------------------------------
#
# JSearch and RemoteOK are open-web aggregators: the SAME real job can also
# come from a direct ATS scrape (Greenhouse/Lever/Ashby) under a different
# job_id, so the store's job_id-based dedup never collapses them. This pass
# drops the aggregator copy whenever a direct-source job with the same
# normalized (company, title) is already present — keeping the richer
# direct-source record. It leaves `dedup_key()` and the store index alone.

_DIRECT_SOURCES = {Source.GREENHOUSE, Source.LEVER, Source.ASHBY}
_AGGREGATOR_SOURCES = {Source.JSEARCH, Source.REMOTEOK}

# Leading seniority words we strip so "Senior Software Engineer" (aggregator)
# matches "Software Engineer" (direct). Order matters only for readability.
_SENIORITY_PREFIXES = (
    "senior ",
    "sr. ",
    "sr ",
    "staff ",
    "principal ",
    "lead ",
    "junior ",
    "jr. ",
    "jr ",
    "entry level ",
    "entry-level ",
)


def _norm_title(title: str) -> str:
    """Normalize a job title for cross-source matching.

    Lowercase, drop a trailing parenthetical ("(Remote)", "(L4)"), drop the
    post-comma team qualifier ("Software Engineer, ML Platform" → base),
    strip a leading seniority word, and collapse whitespace.
    """
    t = title.lower().strip()
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)  # trailing "(...)"
    t = t.split(",")[0].strip()  # post-comma qualifier
    for p in _SENIORITY_PREFIXES:
        if t.startswith(p):
            t = t[len(p):]
            break
    return " ".join(t.split())


def _dedup_cross_source(store: JobStore) -> int:
    """Drop aggregator jobs whose (company, normalized-title) already exists
    from a direct ATS source. Returns the number removed. Mutates the store.
    """
    direct_keys = {
        (j.company.lower(), _norm_title(j.title))
        for j in store.jobs
        if j.source in _DIRECT_SOURCES
    }
    if not direct_keys:
        return 0

    def _is_aggregator_dupe(j: Job) -> bool:
        return (
            j.source in _AGGREGATOR_SOURCES
            and (j.company.lower(), _norm_title(j.title)) in direct_keys
        )

    return store.remove_jobs(_is_aggregator_dupe)


# --- jsearch (on-demand open-web discovery) -----------------------------


def run_jsearch(
    cfg: Config,
    store: JobStore,
    *,
    secrets: Secrets | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, int]:
    """Run the configured JSearch queries (open-web discovery via RapidAPI).

    On-demand, NOT part of `run_scrape`/`daily` — the free tier is small, so
    the user triggers this explicitly. Gracefully no-ops (warn, return zeros)
    when no queries are configured or the API key is missing — it must never
    abort, since it's only one optional source.

    Returns counts: inserted / updated / excluded / filtered / errors /
    deduped / requests. The store is mutated in place; caller must `save()`.
    """
    counts: dict[str, int] = {
        "inserted": 0,
        "updated": 0,
        "excluded": 0,
        "filtered": 0,
        "errors": 0,
        "deduped": 0,
        "seen_skipped": 0,
        "requests": 0,
    }

    queries = cfg.scrapers.jsearch
    if not queries:
        _emit(
            progress,
            phase="jsearch",
            i=0,
            total=0,
            message="No JSearch queries configured.",
        )
        return counts

    # Seen-ledger backstop (same as run_scrape): skip already-reviewed jobs.
    ledger = SeenLedger(ledger_path_for(cfg.paths.jobs_json))
    ledger.load()
    store_keys: set[tuple[str, str]] = {j.dedup_key() for j in store.jobs}

    # Target-company overlay: flag jobs from your target_companies.yaml list.
    target_index = default_target_index()

    api_key = secrets.jsearch_api_key if secrets else ""
    if not api_key:
        _emit(
            progress,
            phase="jsearch",
            i=0,
            total=0,
            message="JSEARCH_API_KEY is empty — skipping JSearch. "
            "Add it to .env to enable open-web search.",
            extra={"skipped": True},
        )
        return counts

    # One authenticated client for all queries. Built here (not in the
    # scraper) so the scraper stays secret-agnostic and the RapidAPI key
    # never leaks onto the no-auth scrapers' client.
    client = httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS,
        headers={
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
            "User-Agent": "job-pipeline-v2 (personal use)",
        },
    )

    try:
        for i, entry in enumerate(queries, start=1):
            label = f"jsearch:{entry.query}"
            _emit(
                progress,
                phase="jsearch",
                i=i,
                total=len(queries),
                item=entry.query,
                message=f"Searching: {entry.query} ({i}/{len(queries)})",
            )
            counts["requests"] += entry.num_pages
            try:
                scraper = JSearchScraper(
                    name=label,
                    query=entry.query,
                    num_pages=entry.num_pages,
                    date_posted=entry.date_posted,
                    remote_only=entry.remote_only,
                    client=client,
                )
                jobs = scraper.fetch()
            except Exception as e:
                counts["errors"] += 1
                _emit(
                    progress,
                    phase="jsearch",
                    i=i,
                    total=len(queries),
                    item=entry.query,
                    message=f"JSearch failed for {entry.query!r}: {e}",
                    extra={"error": True},
                )
                time.sleep(cfg.rate_limits.scraper_delay_seconds)
                continue

            per_query = {"inserted": 0, "updated": 0, "excluded": 0, "filtered": 0}
            for job in jobs:
                key = job.dedup_key()
                if key not in store_keys and ledger.contains(key):
                    counts["seen_skipped"] += 1
                    continue
                result = apply_filters(job, cfg)
                if not result.passed:
                    per_query["filtered"] += 1
                    counts["filtered"] += 1
                    continue
                _tag_target_company(job, target_index)
                outcome = store.upsert(job)
                per_query[outcome] += 1
                counts[outcome] = counts.get(outcome, 0) + 1
                ledger.add(key)
                store_keys.add(key)

            _emit(
                progress,
                phase="jsearch",
                i=i,
                total=len(queries),
                item=entry.query,
                message=(
                    f"{entry.query}: +{per_query['inserted']} new, "
                    f"{per_query['updated']} updated, "
                    f"{per_query['filtered']} filtered"
                ),
                extra={"per_query": per_query},
            )
            time.sleep(cfg.rate_limits.scraper_delay_seconds)
    finally:
        client.close()

    ledger.save()

    # Drop JSearch hits that duplicate a direct-ATS job already in the store.
    removed = _dedup_cross_source(store)
    counts["deduped"] += removed

    _emit(
        progress,
        phase="jsearch",
        message=(
            f"JSearch done: +{counts['inserted']} new, {counts['updated']} updated, "
            f"{counts['filtered']} filtered, {removed} deduped "
            f"(~{counts['requests']} API requests used)."
        ),
        extra={"deduped": removed, "requests": counts["requests"]},
    )
    return counts


# --- pre-filter ---------------------------------------------------------


def run_pre_filter(
    cfg: Config,
    secrets: Secrets,
    store: JobStore,
    *,
    force: bool = False,
    progress: ProgressCallback | None = None,
    prompt_path: Path,
    llm: ClaudeClient | None = None,
) -> dict[str, int]:
    """Score every NEW (or all when force=True) job with the fast Claude model.

    Returns counts: passed / skipped_llm / skipped_rule / errors.
    """
    counts: dict[str, int] = {
        "passed": 0,
        "skipped_llm": 0,
        "skipped_rule": 0,
        "errors": 0,
    }

    targets: list[Job] = [
        j for j in store.jobs if force or j.pre_filter_verdict is None
    ]
    if not targets:
        _emit(
            progress,
            phase="pre-filter",
            i=0,
            total=0,
            message="Nothing to pre-filter.",
        )
        return counts

    client = ClaudePreFilterClient(
        llm=llm or build_llm(secrets), model=cfg.scoring.pre_filter_model
    )
    filterer = PreFilterer(client, render_prompt_file(prompt_path, cfg))
    limiter = _RateLimiter(cfg.rate_limits.api_calls_per_minute)

    for i, job in enumerate(targets, start=1):
        _emit(
            progress,
            phase="pre-filter",
            i=i,
            total=len(targets),
            item=f"{job.company} / {job.title}",
            message=f"Pre-filtering {i}/{len(targets)}: {job.title}",
        )
        try:
            limiter.wait()
            extraction = filterer.score(job)
        except Exception as e:
            counts["errors"] += 1
            _emit(
                progress,
                phase="pre-filter",
                i=i,
                total=len(targets),
                item=job.title,
                message=f"Pre-filter failed: {e}",
                extra={"error": True},
            )
            continue

        job.extracted = extraction.model_dump()
        should_skip, rule_reason = apply_hard_skip_rules(job, extraction, cfg.profile)
        if should_skip:
            job.pre_filter_verdict = PreFilterVerdict.SKIP
            job.pre_filter_reason = rule_reason
            job.status = Status.SKIPPED
            counts["skipped_rule"] += 1
        elif extraction.verdict == "skip":
            job.pre_filter_verdict = PreFilterVerdict.SKIP
            job.pre_filter_reason = extraction.reason
            job.status = Status.SKIPPED
            counts["skipped_llm"] += 1
        else:
            job.pre_filter_verdict = PreFilterVerdict.PASS
            job.pre_filter_reason = extraction.reason
            job.status = Status.PENDING
            counts["passed"] += 1

    return counts


# --- deep-fit -----------------------------------------------------------


def run_deep_fit(
    cfg: Config,
    secrets: Secrets,
    store: JobStore,
    *,
    force: bool = False,
    progress: ProgressCallback | None = None,
    prompt_path: Path,
    llm: ClaudeClient | None = None,
) -> dict[str, int]:
    """Run deep-fit scoring (Claude) on every PASS-pre-filter job."""
    counts: dict[str, int] = {"apply": 0, "consider": 0, "skip": 0, "errors": 0}

    targets: list[Job] = [
        j
        for j in store.jobs
        if j.pre_filter_verdict == PreFilterVerdict.PASS
        and (force or j.fit_score is None)
    ]
    if not targets:
        _emit(
            progress,
            phase="deep-fit",
            i=0,
            total=0,
            message="Nothing to deep-fit.",
        )
        return counts

    client = ClaudeDeepFitClient(llm=llm or build_llm(secrets), model=cfg.scoring.deep_fit_model)
    fitter = DeepFitter(client, render_prompt_file(prompt_path, cfg))
    limiter = _RateLimiter(cfg.rate_limits.api_calls_per_minute)

    # Target-company overlay: the tier-weighted fit-score bump is applied here.
    target_index = default_target_index()

    for i, job in enumerate(targets, start=1):
        _emit(
            progress,
            phase="deep-fit",
            i=i,
            total=len(targets),
            item=f"{job.company} / {job.title}",
            message=f"Deep-fitting {i}/{len(targets)}: {job.company} / {job.title}",
        )
        try:
            limiter.wait()
            result = fitter.score(job, cfg.profile)
        except Exception as e:
            # log.exception emits the full traceback to stderr so the
            # backend log shows us WHY a deep-fit failed (validation
            # error, network, rate limit). SSE just gets the short msg.
            log.exception(
                "Deep-fit failed for %s / %s",
                job.company,
                job.title,
            )
            counts["errors"] += 1
            _emit(
                progress,
                phase="deep-fit",
                i=i,
                total=len(targets),
                item=job.title,
                message=f"Deep-fit failed: {e}",
                extra={"error": True},
            )
            continue

        base_score = weighted_fit_score(result.scores)
        # Target-company overlay: a posting at one of your target
        # companies gets a tier-weighted bump (target_companies.yaml). This is
        # the ONLY place fit_score is nudged beyond the model's rubric, and the
        # boosted score also feeds the verdict — so a target can tip a
        # borderline "consider" up to "apply".
        match = _tag_target_company(job, target_index)
        if match is not None:
            fit_score = min(10.0, base_score + target_index.boost_for(match.tier))
        else:
            fit_score = base_score
        verdict = verdict_from_score(
            fit_score,
            apply_threshold=cfg.scoring.thresholds.apply,
            consider_threshold=cfg.scoring.thresholds.consider,
        )

        job.fit_score = fit_score
        job.fit_verdict = verdict
        job.fit_subscores = ScoreResult(**result.scores.model_dump())
        job.fit_justification = result.justification
        job.honest_bridge_notes = result.honest_bridge_notes
        counts[verdict.value] += 1

    return counts


# --- digest / tracker (one-shot, no progress loop needed) --------------


def run_digest(
    cfg: Config,
    store: JobStore,
    *,
    template_path: Path,
    chrome_template_path: Path,
    today: date | None = None,
) -> Path:
    """Render the daily HTML digest. Returns the output path."""
    today = today or date.today()
    chrome_prompts = build_chrome_prompts_for_config(
        cfg, template_path=chrome_template_path
    )
    html = build_digest_html(
        cfg,
        list(store.jobs),
        list(chrome_prompts),
        template_path=template_path,
        today=today,
    )
    out_dir = Path(cfg.paths.digests_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"digest-{today.isoformat()}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def run_tracker(cfg: Config, store: JobStore) -> Path:
    """Rebuild the Excel tracker. Returns the output path."""
    return write_tracker(list(store.jobs), cfg.paths.tracker_xlsx)


# --- clean discover (archive the inbox before a fresh run) --------------


def run_clean_discover(
    cfg: Config,
    store: JobStore,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, int]:
    """Archive the whole Discover inbox before a fresh discovery run.

    Every job currently in the store is (1) recorded in the seen-ledger so a
    later re-scrape never resurfaces it, and (2) marked `dismissed` unless it
    already is. Nothing is deleted — dismissed rows stay as tombstones and the
    ledger is the durable backstop. The store is mutated in place; the caller
    must `save()`.

    Returns counts: dismissed / already_dismissed / ledger_size.
    """
    ledger = SeenLedger(ledger_path_for(cfg.paths.jobs_json))
    ledger.load()

    counts: dict[str, int] = {
        "dismissed": 0,
        "already_dismissed": 0,
        "ledger_size": 0,
    }
    jobs = list(store.jobs)
    total = len(jobs)
    _emit(
        progress,
        phase="clean-discover",
        i=0,
        total=total,
        message=f"Archiving {total} job(s) from the Discover inbox…",
    )
    for job in jobs:
        ledger.add(job.dedup_key())
        if job.status == Status.DISMISSED:
            counts["already_dismissed"] += 1
        else:
            job.status = Status.DISMISSED
            counts["dismissed"] += 1

    ledger.save()
    counts["ledger_size"] = len(ledger)
    _emit(
        progress,
        phase="clean-discover",
        i=total,
        total=total,
        message=(
            f"Inbox cleaned: {counts['dismissed']} dismissed, "
            f"ledger now holds {counts['ledger_size']} key(s)."
        ),
        extra=dict(counts),
    )
    return counts


# --- resumes (bulk) -----------------------------------------------------


def _job_select_key(j: Job) -> str:
    """The string the UI uses to identify a job for explicit selection.

    Matches `_job_to_ui`'s `key` field in the FastAPI server: `job_id`
    when present, else `dedup_key()[1]`. Kept here as a small helper so
    runner.py doesn't have to import from the UI layer.
    """
    return j.job_id or j.dedup_key()[1]


def run_resumes_batch(
    cfg: Config,
    store: JobStore,
    *,
    content_path: Path,
    progress: ProgressCallback | None = None,
    max_resumes: int = 10,
    verdicts: Iterable[str] | None = None,
    min_score: float | None = None,
    job_keys: Iterable[str] | None = None,
    tailorer: ResumeTailorer | None = None,
) -> dict[str, int]:
    """Generate `.docx` resumes.

    Two selection modes:

    1. **Explicit (preferred when given):** `job_keys` is non-empty →
       render exactly those jobs (intersected with "doesn't already
       have a resume"). `verdicts` and `min_score` are ignored. The
       `max_resumes` cap is also bypassed — the user explicitly chose
       the set.

    2. **Filter-based (default):** match every job that
       - matches one of the requested verdicts (default: only `apply`), AND
       - meets the optional `min_score` floor (1.0-10.0), AND
       - doesn't already have a `resume_path`.

       Caps at `max_resumes` per run so a noisy scoring pass doesn't
       burst out 50 docx files by accident.

    Resumes are FREE (no LLM cost) — only the Node renderer runs.

    Args:
        verdicts: which `fit_verdict` values to include. None → {"apply"}
            (the historical default — same as the original "top picks"
            behavior). Pass {"apply", "consider"} to render resumes for
            both verdicts. Values are normalized lowercase and ignored
            if they don't match a known Verdict.
        min_score: minimum `fit_score` (1.0–10.0 scale, NOT the
            UI's 100-scale). None → no floor.
        job_keys: explicit job key list. When non-empty, this overrides
            verdicts + min_score + the max_resumes cap. Each key is the
            same identifier the UI uses — `job_id` if present, else
            `dedup_key[1]`.

    Caller is responsible for `store.save()` after. Each successful
    render stamps `resume_path` on the job; if the job's status was
    new/pending we bump it to `resume_only` to mirror the per-job
    resume action.
    """
    # Lazy import — avoids a Python→Node dependency at module load.
    # `Status` + `generate_resume` are used by `_render_targets`, not
    # here; they get re-imported there. Keeping the imports tight to
    # what each function uses also keeps ruff F401 happy.
    from .models import Verdict
    from .resume import load_resume_content

    counts: dict[str, int] = {"generated": 0, "skipped": 0, "errors": 0}

    # --- mode 1: explicit selection -------------------------------------
    # If the caller passed specific job keys, that wins — generate exactly
    # those (minus any that already have a resume). No cap; no verdict
    # filter; no min_score floor. The user told us what they want.
    explicit_keys: set[str] | None = None
    if job_keys is not None:
        explicit_keys = {k for k in job_keys if k}
        if not explicit_keys:
            # Caller passed an empty list — treat as "no jobs to render."
            _emit(
                progress,
                phase="resumes",
                i=0,
                total=0,
                message="No jobs selected.",
            )
            return counts

    if explicit_keys is not None:
        # Build a fast lookup so order matches store.jobs (stable for the UI).
        targets = [
            j
            for j in store.jobs
            if _job_select_key(j) in explicit_keys and not j.resume_path
        ]
        if not targets:
            _emit(
                progress,
                phase="resumes",
                i=0,
                total=0,
                message="None of the selected jobs need a resume "
                "(all already have one or no longer exist).",
            )
            return counts
        resume_content = load_resume_content(content_path)
        return _render_targets(
            targets, resume_content, cfg, store, progress, counts, tailorer
        )

    # --- mode 2: filter-based -------------------------------------------
    # Normalize the verdict filter. None → apply-only (backwards-compat
    # with the original "top picks" button).
    if verdicts is None:
        allowed_verdicts: set[Verdict] = {Verdict.APPLY}
    else:
        allowed_verdicts = set()
        for v in verdicts:
            try:
                allowed_verdicts.add(Verdict(v.lower()))
            except ValueError:
                # Unknown verdict string — silently drop. The endpoint
                # validates inputs; this is defensive.
                continue
        if not allowed_verdicts:
            allowed_verdicts = {Verdict.APPLY}

    def _meets_filter(j: Job) -> bool:
        if j.fit_verdict not in allowed_verdicts:
            return False
        if j.resume_path:
            return False
        if min_score is not None:
            score = j.fit_score or 0.0
            if score < min_score:
                return False
        return True

    targets = [j for j in store.jobs if _meets_filter(j)][:max_resumes]

    if not targets:
        # Build a human-readable describe of WHICH verdicts were looked for,
        # since the default is no longer the only option.
        verdict_label = "/".join(sorted(v.value for v in allowed_verdicts))
        _emit(
            progress,
            phase="resumes",
            i=0,
            total=0,
            message=f"No {verdict_label}-verdict jobs needing resumes.",
        )
        return counts

    resume_content = load_resume_content(content_path)
    return _render_targets(targets, resume_content, cfg, store, progress, counts, tailorer)


def _render_targets(
    targets: list[Job],
    resume_content: Any,
    cfg: Config,
    store: JobStore,  # noqa: ARG001 — kept for future use (audit trail / re-load)
    progress: ProgressCallback | None,
    counts: dict[str, int],
    tailorer: ResumeTailorer | None = None,
) -> dict[str, int]:
    """Render `.docx` resumes for each job in `targets`.

    Shared by run_resumes_batch's two selection modes. Mutates `counts`
    in place and stamps `resume_path` + `status` on each rendered job.
    When `tailorer` is set, each resume is honestly reframed via Claude
    before rendering (with deterministic fallback on failure).
    """
    # Lazy imports keep the module top clean and parallel run_resumes_batch.
    from .models import Status
    from .resume import generate_resume

    for i, job in enumerate(targets, start=1):
        _emit(
            progress,
            phase="resumes",
            i=i,
            total=len(targets),
            item=f"{job.company} / {job.title}",
            message=f"Rendering resume {i}/{len(targets)}: {job.company} / {job.title}",
        )
        try:
            out_path = generate_resume(
                job, resume_content, cfg.paths.resumes_dir, tailorer=tailorer
            )
        except Exception as e:
            counts["errors"] += 1
            _emit(
                progress,
                phase="resumes",
                i=i,
                total=len(targets),
                item=job.title,
                message=f"Resume failed for {job.title}: {e}",
                extra={"error": True},
            )
            continue

        job.resume_path = str(out_path.resolve())
        if job.status in (Status.NEW, Status.PENDING):
            job.status = Status.RESUME_ONLY
        counts["generated"] += 1

    return counts


# --- helpers ------------------------------------------------------------


class _RateLimiter:
    """Between-call throttle. Copied from cli.py so runner has no
    cli.py dependency. Same behavior."""

    def __init__(self, calls_per_minute: int) -> None:
        self.min_interval = 60.0 / max(calls_per_minute, 1)
        self._last_call = 0.0

    def wait(self) -> None:
        now = time.time()
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()
