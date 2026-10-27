"""Applied guard: tracked jobs never resurface, Track-tab progress syncs to Discover."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_pipeline import tracker_db
from job_pipeline.applied_guard import load_tracked_index, sync_statuses
from job_pipeline.config import load_config
from job_pipeline.importers import import_chrome_claude_jsonl
from job_pipeline.models import Job, Source, Status
from job_pipeline.store import JobStore
from tests.conftest import EXAMPLE_CONFIG


def _job(**kw: Any) -> Job:
    base: dict[str, Any] = dict(company="Stripe", job_id="111", title="Backend Engineer",
                                source=Source.GREENHOUSE, url="u", location="Remote",
                                description="jd", discovered_at=datetime.now(UTC).isoformat())
    base.update(kw)
    return Job(**base)


@pytest.fixture
def cfg(tmp_path: Path) -> Any:
    c = load_config(EXAMPLE_CONFIG)
    c.tracker.db_path = str(tmp_path / "tracker.db")
    c.paths.jobs_json = str(tmp_path / "jobs.json")
    tracker_db.insert_jobs(c, [
        {"company": "Stripe, Inc.", "role": "Backend Engineer", "job_id": "111"},
        {"company": "Notion", "role": "Data Engineer", "job_id": "n1"},
    ])
    ids = {j["role"]: j["id"] for j in tracker_db.list_jobs(c)}
    tracker_db.set_status(c, ids["Backend Engineer"], "applied")
    tracker_db.set_status(c, ids["Data Engineer"], "rejected")
    return c


def test_missing_db_gives_empty_index(tmp_path: Path) -> None:
    c = load_config(EXAMPLE_CONFIG)
    c.tracker.db_path = str(tmp_path / "none.db")
    assert len(load_tracked_index(c)) == 0


def test_match_by_id_and_by_reposted_title(cfg: Any) -> None:
    idx = load_tracked_index(cfg)
    assert idx.match(_job()) == "applied"
    # Re-post: same company + role, different job id, different casing.
    assert idx.match(_job(job_id="999", title="backend engineer", company="stripe")) == "applied"
    assert idx.match(_job(company="Datadog")) is None


def test_sync_statuses_mirrors_tracker(cfg: Any) -> None:
    idx = load_tracked_index(cfg)
    jobs = [
        _job(),
        _job(company="Notion", job_id="n1", title="Data Engineer"),
        _job(company="Notion", job_id="n2", title="Other", status=Status.NEW),
        _job(job_id="x", title="Backend Engineer", status=Status.DISMISSED),  # manual choice kept
    ]
    assert sync_statuses(jobs, idx) == 2
    assert [j.status for j in jobs] == [
        Status.APPLIED, Status.REJECTED, Status.NEW, Status.DISMISSED,
    ]


def test_chrome_import_skips_tracked(cfg: Any, tmp_path: Path) -> None:
    f = tmp_path / "run.jsonl"
    rows = [
        {"company": "Stripe", "job_id": "222", "title": "Backend Engineer", "url": "u",
         "description": "repost"},
        {"company": "Figma", "job_id": "f1", "title": "Engineer", "url": "u",
         "description": "new"},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    stats = import_chrome_claude_jsonl(f, store, tracked=load_tracked_index(cfg))
    assert stats.inserted == 1
    assert stats.already_tracked == 1
    assert [j.company for j in store.jobs] == ["Figma"]


def test_scrape_skips_tracked_jobs(cfg: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import job_pipeline.runner as runner_mod

    class FakeScraper:
        def fetch(self) -> list[Job]:
            return [_job(job_id="333", location="San Francisco, CA"),            # repost
                    _job(company="Stripe", job_id="444", title="Infra Engineer",
                         location="San Francisco, CA")]

    monkeypatch.setattr(runner_mod, "get_scraper", lambda *a, **kw: FakeScraper())
    monkeypatch.setattr("time.sleep", lambda _s: None)
    cfg.scrapers.lever = []
    cfg.scrapers.ashby = []
    cfg.scrapers.remoteok = []
    cfg.scrapers.greenhouse = cfg.scrapers.greenhouse[:1]
    store = JobStore(cfg.paths.jobs_json)
    store.load()
    counts = runner_mod.run_scrape(cfg, store)
    assert counts["already_tracked"] == 1
    assert [j.title for j in store.jobs] == ["Infra Engineer"]
