"""Tests for the refactored runner module.

We exercise the progress-callback wiring — the actual pipeline logic
(scoring, scraping) is already covered by per-module tests. Here we
just check that:
- run_scrape produces "scrape" phase events with i / total / item
- run_pre_filter / run_deep_fit emit per-job events
- run_scrape handles "no work" gracefully
- progress=None doesn't crash anything
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from job_pipeline.config import Secrets, load_config
from job_pipeline.models import Job, PreFilterVerdict, Source, Status
from job_pipeline.runner import (
    ProgressEvent,
    _dedup_cross_source,
    _norm_title,
    run_clean_discover,
    run_deep_fit,
    run_digest,
    run_jsearch,
    run_pre_filter,
    run_scrape,
    run_tracker,
)
from job_pipeline.scoring.deep_fit import DeepFitResult, DeepFitScores
from job_pipeline.scoring.pre_filter import PreFilterExtraction
from job_pipeline.seen_ledger import SeenLedger
from job_pipeline.store import JobStore
from tests.conftest import EXAMPLE_CONFIG

REPO = Path(__file__).resolve().parent.parent
CFG = load_config(EXAMPLE_CONFIG)


# --- helpers -----------------------------------------------------------


def _make_job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "company": "databricks",
        "job_id": "1",
        "title": "Software Engineer, ML Platform",
        "team": "ML",
        "source": Source.GREENHOUSE,
        "url": "https://example.com/job/1",
        "location": "San Francisco, CA",
        "discovered_at": datetime.now(UTC).isoformat(),
        "description": "build stuff with PySpark",
    }
    defaults.update(overrides)
    return Job(**defaults)


class _EventCollector:
    """Captures every ProgressEvent emitted into a list."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def __call__(self, e: ProgressEvent) -> None:
        self.events.append(e)


@pytest.fixture(autouse=True)
def _isolate_seen_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Keep the discovery runners off the real data/seen_jobs.json.

    run_scrape / run_jsearch / run_clean_discover derive the ledger path from
    `cfg.paths.jobs_json` (the real repo data dir). These tests use a tmp_path
    store, so redirect the ledger into this test's tmp dir — fully isolated.
    Returns the redirected path so a test can pre-seed it.
    """
    import job_pipeline.runner as runner_mod

    led_path = tmp_path / "seen_jobs.json"
    monkeypatch.setattr(runner_mod, "ledger_path_for", lambda _p: led_path)
    return led_path


# --- run_scrape --------------------------------------------------------


def test_run_scrape_no_scrapers_configured(tmp_path: Path) -> None:
    """Empty work list → emits a 'no scrapers' event, returns zeros."""
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.scrapers.greenhouse = []
    cfg.scrapers.lever = []
    cfg.scrapers.ashby = []
    cfg.scrapers.remoteok = []

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    coll = _EventCollector()
    counts = run_scrape(cfg, store, progress=coll)

    assert counts == {
        "inserted": 0, "updated": 0, "excluded": 0,
        "filtered": 0, "errors": 0, "deduped": 0, "seen_skipped": 0,
    }
    assert any("No scrapers configured" in e.message for e in coll.events)


def test_run_scrape_emits_per_board_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One event per board: 'Scraping X (i/N)' plus a final summary."""
    cfg = load_config(EXAMPLE_CONFIG)

    # Replace every scraper's fetch() with a stub that returns one job.
    def fake_fetch_factory(name: str):
        def fetch(self: Any) -> list[Job]:
            return [_make_job(company=name, job_id=f"{name}-1", location="San Francisco, CA")]
        return fetch

    from job_pipeline.scrapers import ashby as ab_mod
    from job_pipeline.scrapers import greenhouse as gh_mod
    from job_pipeline.scrapers import lever as lv_mod
    from job_pipeline.scrapers import remoteok as ro_mod

    # Stub every scraper class so no real network calls happen.
    monkeypatch.setattr(
        gh_mod.GreenhouseScraper, "fetch", lambda self: [
            _make_job(company=self.name, job_id=f"{self.name}-1", location="San Francisco, CA")
        ]
    )
    monkeypatch.setattr(
        lv_mod.LeverScraper, "fetch", lambda self: [
            _make_job(company=self.name, job_id=f"{self.name}-1", location="San Francisco, CA")
        ]
    )
    monkeypatch.setattr(
        ab_mod.AshbyScraper, "fetch", lambda self: [
            _make_job(company=self.name, job_id=f"{self.name}-1", location="San Francisco, CA")
        ]
    )
    monkeypatch.setattr(
        ro_mod.RemoteOKScraper, "fetch", lambda self: [
            _make_job(company=self.name, job_id=f"{self.name}-1", location="San Francisco, CA")
        ]
    )
    # Don't actually sleep between boards in tests.
    monkeypatch.setattr("time.sleep", lambda _: None)

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    coll = _EventCollector()
    counts = run_scrape(cfg, store, progress=coll)

    # Each board contributes one "Scraping…" event and one "summary" event.
    starts = [e for e in coll.events if "Scraping" in e.message]
    summaries = [e for e in coll.events if "new" in e.message and "updated" in e.message]
    expected_boards = (
        len(cfg.scrapers.greenhouse)
        + len(cfg.scrapers.lever)
        + len(cfg.scrapers.ashby)
        + len(cfg.scrapers.remoteok)
    )
    assert len(starts) == expected_boards
    assert len(summaries) == len(starts)
    assert counts["inserted"] >= 1


def test_run_scrape_continues_on_per_board_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A flaky board doesn't abort the whole run."""
    cfg = load_config(EXAMPLE_CONFIG)
    # Only greenhouse this test — clear all other platforms so this stays focused.
    cfg.scrapers.lever = []
    cfg.scrapers.ashby = []
    cfg.scrapers.remoteok = []

    boards_called: list[str] = []

    def fetch_stub(self: Any) -> list[Job]:
        boards_called.append(self.name)
        if self.name == cfg.scrapers.greenhouse[0].name:
            raise httpx.ConnectError("boom")
        return []

    from job_pipeline.scrapers import greenhouse as gh_mod

    monkeypatch.setattr(gh_mod.GreenhouseScraper, "fetch", fetch_stub)
    monkeypatch.setattr("time.sleep", lambda _: None)

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    coll = _EventCollector()
    counts = run_scrape(cfg, store, progress=coll)

    # The error counted, but later boards still got hit.
    assert counts["errors"] == 1
    assert len(boards_called) == len(cfg.scrapers.greenhouse)
    assert any("Scrape failed" in e.message for e in coll.events)


def test_run_scrape_without_progress_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """progress=None should be valid — no events, no crash."""
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.scrapers.greenhouse = []
    cfg.scrapers.lever = []
    cfg.scrapers.ashby = []
    cfg.scrapers.remoteok = []

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    counts = run_scrape(cfg, store, progress=None)
    assert counts["inserted"] == 0


# --- seen-ledger: skip postings already reviewed in a past run ----------


def _one_greenhouse_board_returning(
    monkeypatch: pytest.MonkeyPatch, cfg: Any, job: Job
) -> None:
    """Reduce cfg to a single greenhouse board whose fetch() yields `job`."""
    cfg.scrapers.greenhouse = cfg.scrapers.greenhouse[:1]
    cfg.scrapers.lever = []
    cfg.scrapers.ashby = []
    cfg.scrapers.remoteok = []
    from job_pipeline.scrapers import greenhouse as gh_mod

    monkeypatch.setattr(gh_mod.GreenhouseScraper, "fetch", lambda self: [job])
    monkeypatch.setattr("time.sleep", lambda _: None)


def test_run_scrape_skips_seen_not_in_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolate_seen_ledger: Path
) -> None:
    """A posting whose key is in the ledger but NOT in the store is skipped —
    it was reviewed and its row later deleted, so don't resurface it."""
    cfg = load_config(EXAMPLE_CONFIG)
    job = _make_job(company="testco", job_id="seen-1", location="San Francisco, CA")
    _one_greenhouse_board_returning(monkeypatch, cfg, job)

    # Pre-seed the ledger with this job's key (as if a past run stored it).
    led = SeenLedger(_isolate_seen_ledger)
    led.load()
    led.add(("testco", "seen-1"))
    led.save()

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    counts = run_scrape(cfg, store, progress=None)

    assert counts["seen_skipped"] == 1
    assert counts["inserted"] == 0
    assert counts["filtered"] == 0
    assert len(store.jobs) == 0


def test_run_scrape_does_not_skip_seen_still_in_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolate_seen_ledger: Path
) -> None:
    """A posting still present in the store (e.g. a dismissed tombstone) is
    re-processed normally even though its key is in the ledger — the store's
    own dedup handles it, so it counts as an update, not a seen-skip."""
    cfg = load_config(EXAMPLE_CONFIG)
    job = _make_job(company="testco", job_id="seen-1", location="San Francisco, CA")
    _one_greenhouse_board_returning(monkeypatch, cfg, job)

    led = SeenLedger(_isolate_seen_ledger)
    led.load()
    led.add(("testco", "seen-1"))
    led.save()

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job(company="testco", job_id="seen-1", status=Status.DISMISSED))

    counts = run_scrape(cfg, store, progress=None)

    assert counts["seen_skipped"] == 0
    assert counts["updated"] == 1
    # Dismissed status survives the re-scrape (status is not refreshable).
    assert store.jobs[0].status == Status.DISMISSED


def test_run_scrape_records_new_jobs_in_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolate_seen_ledger: Path
) -> None:
    """A freshly inserted job is written to the ledger so a future run with an
    emptied store won't resurface it."""
    cfg = load_config(EXAMPLE_CONFIG)
    job = _make_job(company="testco", job_id="fresh-1", location="San Francisco, CA")
    _one_greenhouse_board_returning(monkeypatch, cfg, job)

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    counts = run_scrape(cfg, store, progress=None)
    assert counts["inserted"] == 1

    reloaded = SeenLedger(_isolate_seen_ledger)
    reloaded.load()
    assert reloaded.contains(("testco", "fresh-1"))


# --- run_clean_discover ------------------------------------------------


def test_run_clean_discover_dismisses_all_and_seeds_ledger(
    tmp_path: Path, _isolate_seen_ledger: Path
) -> None:
    """Every current job is dismissed and its key recorded in the ledger."""
    cfg = load_config(EXAMPLE_CONFIG)
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job(company="a", job_id="1", status=Status.NEW))
    store.upsert(_make_job(company="b", job_id="2", status=Status.PENDING))
    store.upsert(_make_job(company="c", job_id="3", status=Status.RESUME_ONLY))

    coll = _EventCollector()
    counts = run_clean_discover(cfg, store, progress=coll)

    assert counts["dismissed"] == 3
    assert counts["already_dismissed"] == 0
    assert counts["ledger_size"] == 3
    assert all(j.status == Status.DISMISSED for j in store.jobs)

    # Ledger persisted with all three keys.
    reloaded = SeenLedger(_isolate_seen_ledger)
    reloaded.load()
    assert reloaded.contains(("a", "1"))
    assert reloaded.contains(("b", "2"))
    assert reloaded.contains(("c", "3"))
    assert any("Inbox cleaned" in e.message for e in coll.events)


def test_run_clean_discover_counts_already_dismissed_separately(
    tmp_path: Path, _isolate_seen_ledger: Path
) -> None:
    """Already-dismissed rows are recorded in the ledger but not re-counted."""
    cfg = load_config(EXAMPLE_CONFIG)
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job(company="a", job_id="1", status=Status.DISMISSED))
    store.upsert(_make_job(company="b", job_id="2", status=Status.NEW))
    store.upsert(_make_job(company="c", job_id="3", status=Status.APPLIED))

    counts = run_clean_discover(cfg, store, progress=None)

    assert counts["dismissed"] == 2
    assert counts["already_dismissed"] == 1
    assert counts["ledger_size"] == 3
    assert all(j.status == Status.DISMISSED for j in store.jobs)


def test_run_clean_discover_empty_store(
    tmp_path: Path, _isolate_seen_ledger: Path
) -> None:
    """No jobs → all-zero counts, no crash, ledger file still written."""
    cfg = load_config(EXAMPLE_CONFIG)
    store = JobStore(tmp_path / "jobs.json")
    store.load()

    counts = run_clean_discover(cfg, store, progress=None)
    assert counts == {"dismissed": 0, "already_dismissed": 0, "ledger_size": 0}


# --- run_pre_filter ----------------------------------------------------


def test_run_pre_filter_emits_per_job_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two unscored jobs → two 'Pre-filtering N/M' events."""
    cfg = load_config(EXAMPLE_CONFIG)
    secrets = Secrets(ANTHROPIC_API_KEY="sk-ant-test")

    # Patch the client builder so we don't make real network calls.
    from job_pipeline import runner as runner_mod

    fake_extraction = PreFilterExtraction(
        level="L4", years_required=4, degree_required="BS",
        visa_stance="sponsors", tech_stack=["python"],
        verdict="pass", reason="ok",
    )

    class FakeClient:
        def __init__(self, **kw): pass
        def call(self, prompt: str) -> PreFilterExtraction:
            return fake_extraction

    monkeypatch.setattr(runner_mod, "ClaudePreFilterClient", FakeClient)
    monkeypatch.setattr(runner_mod, "build_llm", lambda *a, **kw: Mock())
    monkeypatch.setattr("time.sleep", lambda _: None)  # rate limiter

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job(job_id="a", title="Engineer A"))
    store.upsert(_make_job(job_id="b", title="Engineer B"))

    coll = _EventCollector()
    counts = run_pre_filter(
        cfg, secrets, store, progress=coll,
        prompt_path=REPO / "prompts" / "pre_filter.txt",
    )

    pf_events = [e for e in coll.events if "Pre-filtering" in e.message]
    assert len(pf_events) == 2
    assert pf_events[0].i == 1 and pf_events[0].total == 2
    assert pf_events[1].i == 2 and pf_events[1].total == 2
    assert counts["passed"] == 2


def test_run_pre_filter_skips_already_scored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    secrets = Secrets(ANTHROPIC_API_KEY="sk-ant-test")
    from job_pipeline import runner as runner_mod

    monkeypatch.setattr(runner_mod, "ClaudePreFilterClient", lambda **kw: Mock())
    monkeypatch.setattr(runner_mod, "build_llm", lambda *a, **kw: Mock())

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job(pre_filter_verdict=PreFilterVerdict.PASS))

    coll = _EventCollector()
    counts = run_pre_filter(
        cfg, secrets, store, progress=coll,
        prompt_path=REPO / "prompts" / "pre_filter.txt",
    )

    assert counts == {"passed": 0, "skipped_llm": 0, "skipped_rule": 0, "errors": 0}
    assert any("Nothing to pre-filter" in e.message for e in coll.events)


# --- run_deep_fit ------------------------------------------------------


def test_run_deep_fit_emits_events_and_writes_scores(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    secrets = Secrets(ANTHROPIC_API_KEY="sk-ant-test")

    fake_result = DeepFitResult(
        scores=DeepFitScores(
            stack=9.0, level=8.0, function=9.0, bridgeability=7.0, desirability=8.0
        ),
        justification="Strong fit.",
        honest_bridge_notes=None,
    )

    class FakeClient:
        def __init__(self, **kw): pass
        def call(self, prompt: str) -> DeepFitResult: return fake_result

    from job_pipeline import runner as runner_mod
    monkeypatch.setattr(runner_mod, "ClaudeDeepFitClient", FakeClient)
    monkeypatch.setattr(runner_mod, "build_llm", lambda *a, **kw: Mock())
    monkeypatch.setattr("time.sleep", lambda _: None)

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    job = _make_job(pre_filter_verdict=PreFilterVerdict.PASS)
    store.upsert(job)

    coll = _EventCollector()
    counts = run_deep_fit(
        cfg, secrets, store, progress=coll,
        prompt_path=REPO / "prompts" / "deep_fit.txt",
    )

    df_events = [e for e in coll.events if "Deep-fitting" in e.message]
    assert len(df_events) == 1
    assert counts["apply"] == 1
    assert store.jobs[0].fit_score is not None
    assert store.jobs[0].fit_score >= 7.0


# --- target-company overlay (flag + fit-score boost) --------------------


def _fake_deepfit_client(monkeypatch: pytest.MonkeyPatch, scores: DeepFitScores) -> None:
    """Patch the deep-fit client to return fixed `scores` for every job."""
    result = DeepFitResult(scores=scores, justification="x", honest_bridge_notes=None)

    class FakeClient:
        def __init__(self, **kw: Any) -> None: ...
        def call(self, prompt: str) -> DeepFitResult:
            return result

    from job_pipeline import runner as runner_mod

    monkeypatch.setattr(runner_mod, "ClaudeDeepFitClient", FakeClient)
    monkeypatch.setattr(runner_mod, "build_llm", lambda *a, **kw: Mock())
    monkeypatch.setattr("time.sleep", lambda _: None)


def test_run_deep_fit_boosts_target_company(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A target-company job gets a tier-weighted bump; an identical non-target
    job (same raw scores) does not — the difference is exactly the Tier-1 boost."""
    cfg = load_config(EXAMPLE_CONFIG)
    secrets = Secrets(ANTHROPIC_API_KEY="sk-ant-test")
    # All-8 subscores → base fit_score is exactly 8.0 regardless of weights.
    _fake_deepfit_client(
        monkeypatch,
        DeepFitScores(stack=8.0, level=8.0, function=8.0, bridgeability=8.0, desirability=8.0),
    )

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job(
        company="Databricks", job_id="t-1", pre_filter_verdict=PreFilterVerdict.PASS,
    ))
    store.upsert(_make_job(
        company="Acme Widgets", job_id="n-1", pre_filter_verdict=PreFilterVerdict.PASS,
    ))

    run_deep_fit(
        cfg, secrets, store, progress=None,
        prompt_path=REPO / "prompts" / "deep_fit.txt",
    )

    target = next(j for j in store.jobs if j.company == "Databricks")
    nontarget = next(j for j in store.jobs if j.company == "Acme Widgets")

    assert target.is_target_company is True
    assert target.target_tier == 1
    assert nontarget.is_target_company is False
    assert nontarget.target_tier is None

    assert nontarget.fit_score is not None and round(nontarget.fit_score, 2) == 8.0
    assert target.fit_score is not None
    # Tier-1 boost from the shipped target_companies.yaml is +0.7.
    assert round(target.fit_score - nontarget.fit_score, 2) == 0.7


def test_run_deep_fit_boost_capped_at_10(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A perfect raw score + boost can't exceed 10.0."""
    cfg = load_config(EXAMPLE_CONFIG)
    secrets = Secrets(ANTHROPIC_API_KEY="sk-ant-test")
    _fake_deepfit_client(
        monkeypatch,
        DeepFitScores(stack=10.0, level=10.0, function=10.0, bridgeability=10.0, desirability=10.0),
    )

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job(
        company="Databricks", job_id="t-1", pre_filter_verdict=PreFilterVerdict.PASS,
    ))

    run_deep_fit(
        cfg, secrets, store, progress=None,
        prompt_path=REPO / "prompts" / "deep_fit.txt",
    )
    assert store.jobs[0].fit_score == 10.0


def test_run_scrape_tags_target_company(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolate_seen_ledger: Path
) -> None:
    """Scrape flags a target company immediately (before scoring)."""
    cfg = load_config(EXAMPLE_CONFIG)
    job = _make_job(company="Databricks", job_id="t-1", location="San Francisco, CA")
    _one_greenhouse_board_returning(monkeypatch, cfg, job)

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    run_scrape(cfg, store, progress=None)

    assert len(store.jobs) == 1
    assert store.jobs[0].is_target_company is True
    assert store.jobs[0].target_tier == 1


def test_run_scrape_does_not_tag_non_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolate_seen_ledger: Path
) -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    job = _make_job(company="Acme Widgets", job_id="n-1", location="San Francisco, CA")
    _one_greenhouse_board_returning(monkeypatch, cfg, job)

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    run_scrape(cfg, store, progress=None)

    assert len(store.jobs) == 1
    assert store.jobs[0].is_target_company is False
    assert store.jobs[0].target_tier is None


# --- run_digest / run_tracker (smoke) -----------------------------------


def test_run_digest_writes_file(tmp_path: Path) -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.paths.digests_dir = str(tmp_path / "digests")

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job())

    out = run_digest(
        cfg, store,
        template_path=REPO / "prompts" / "digest.html.j2",
        chrome_template_path=REPO / "prompts" / "chrome_claude_template.txt",
    )
    assert out.exists()
    assert "Job Digest" in out.read_text(encoding="utf-8")


def test_run_tracker_writes_xlsx(tmp_path: Path) -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.paths.tracker_xlsx = str(tmp_path / "tracker.xlsx")

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job())

    out = run_tracker(cfg, store)
    assert out.exists()
    assert out.stat().st_size > 0


# --- cross-source dedup -------------------------------------------------


def test_norm_title_strips_seniority_and_team() -> None:
    # Seniority prefix dropped.
    assert _norm_title("Senior Software Engineer") == "software engineer"
    # Post-comma team qualifier dropped.
    assert _norm_title("Software Engineer, ML Platform") == "software engineer"
    # Trailing parenthetical dropped.
    assert _norm_title("Data Engineer (Remote)") == "data engineer"
    # Combined + case-insensitive + whitespace collapse.
    assert _norm_title("  STAFF   Data Engineer, Infra ") == "data engineer"


def test_dedup_cross_source_drops_aggregator_dupe(tmp_path: Path) -> None:
    """A JSearch/RemoteOK job whose (company, normalized-title) matches a
    direct-ATS job is removed; the direct one stays."""
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    # Direct-source job (Greenhouse).
    store.upsert(_make_job(
        company="anthropic", job_id="gh-1",
        title="Software Engineer, ML Platform", source=Source.GREENHOUSE,
    ))
    # Aggregator dupe of the same role (JSearch, job_id=None, seniority prefix).
    store.upsert(_make_job(
        company="anthropic", job_id=None,
        title="Senior Software Engineer", team=None, source=Source.JSEARCH,
    ))
    # Aggregator job with NO direct match — should survive.
    store.upsert(_make_job(
        company="someStartup", job_id=None,
        title="Backend Engineer", team=None, source=Source.JSEARCH,
    ))

    removed = _dedup_cross_source(store)
    assert removed == 1
    survivors = {(j.company.lower(), j.source) for j in store.jobs}
    assert ("anthropic", Source.GREENHOUSE) in survivors
    assert ("anthropic", Source.JSEARCH) not in survivors
    assert ("somestartup", Source.JSEARCH) in survivors


# --- run_jsearch --------------------------------------------------------


def test_run_jsearch_skips_when_no_key(tmp_path: Path) -> None:
    """JSearch must warn-and-skip (not abort) when the key is empty."""
    cfg = load_config(EXAMPLE_CONFIG)  # has jsearch queries
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    coll = _EventCollector()

    counts = run_jsearch(cfg, store, secrets=Secrets(JSEARCH_API_KEY=""), progress=coll)
    assert counts["inserted"] == 0
    assert counts["requests"] == 0
    assert any("JSEARCH_API_KEY is empty" in e.message for e in coll.events)


def test_run_jsearch_skips_when_no_queries(tmp_path: Path) -> None:
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.scrapers.jsearch = []
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    coll = _EventCollector()

    counts = run_jsearch(cfg, store, secrets=Secrets(JSEARCH_API_KEY="x"), progress=coll)
    assert counts == {
        "inserted": 0, "updated": 0, "excluded": 0, "filtered": 0,
        "errors": 0, "deduped": 0, "requests": 0, "seen_skipped": 0,
    }
    assert any("No JSearch queries configured" in e.message for e in coll.events)


def test_run_jsearch_runs_with_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With a key + queries, JSearch fetches and upserts. We stub the
    scraper's fetch so no real network call happens."""
    cfg = load_config(EXAMPLE_CONFIG)
    # Keep it to one query for a deterministic count.
    cfg.scrapers.jsearch = cfg.scrapers.jsearch[:1]

    from job_pipeline.scrapers import jsearch as js_mod

    monkeypatch.setattr(
        js_mod.JSearchScraper, "fetch",
        lambda self: [
            _make_job(
                company="discovered co", job_id=None, title="ML Data Engineer",
                team=None, source=Source.JSEARCH, location="San Francisco, CA",
            )
        ],
    )
    monkeypatch.setattr("time.sleep", lambda _: None)

    store = JobStore(tmp_path / "jobs.json")
    store.load()
    counts = run_jsearch(cfg, store, secrets=Secrets(JSEARCH_API_KEY="x"))

    assert counts["inserted"] == 1
    assert counts["requests"] == 1  # one query × num_pages 1
    assert any(j.company == "discovered co" for j in store.jobs)
