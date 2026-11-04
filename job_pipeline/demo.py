""""Try a demo first": a ready-made profile with made-up data.

Lets people see scored jobs, the checklist and a resume before they create an
API key or answer any questions. The demo profile:
* uses the fictional "Jordan Rivera" example profile;
* gets ~12 pre-scored fictional jobs from examples/demo/jobs.yaml (dates are
  computed relative to today) and two tracked applications;
* has resume tailoring OFF, so resumes render without Claude;
* is marked `"demo": true` in profile.json — Claude calls are refused while
  it's active (see llm/factory.py), with a friendly message.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from . import paths, profiles, tracker_db
from .config import load_config
from .models import Job, PreFilterVerdict, ScoreResult, Source, Status, Verdict
from .store import JobStore

DEMO_DIR = paths.REPO_ROOT / "examples" / "demo"
DEMO_NAME = "Demo — Jordan (made-up data)"


def is_demo(profile_dir: Path | None = None) -> bool:
    d = profile_dir or paths.profile_dir()
    return bool(profiles._read_meta(d).get("demo"))


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _job(i: int, raw: dict[str, Any]) -> Job:
    status = Status.PENDING if raw["verdict"] in ("apply", "consider") else Status.SKIPPED
    return Job(
        company=raw["company"], job_id=f"demo-{i}", title=raw["title"],
        source=Source.GREENHOUSE, url=f"https://example.com/demo-jobs/{i}",
        location=raw["location"], posted_date=_iso(raw["days_ago"]),
        discovered_at=_iso(min(raw["days_ago"], 1)), description=raw["description"].strip(),
        extracted={"visa_stance": "unknown", "tech_stack": []},
        pre_filter_verdict=PreFilterVerdict.PASS, pre_filter_reason="demo",
        fit_score=raw["fit_score"], fit_verdict=Verdict(raw["verdict"]),
        fit_subscores=ScoreResult(**raw["subscores"]),
        fit_justification=" ".join(raw["fit_reasons"]) or "Not a fit.",
        honest_bridge_notes=raw.get("bridge") or None,
        fit_reasons=raw["fit_reasons"], fit_gaps=raw["fit_gaps"],
        is_target_company=bool(raw.get("target")), target_tier=1 if raw.get("target") else None,
        status=status,
    )


def create_demo(*, activate: bool = True) -> dict[str, Any]:
    """Create (or recreate) the demo profile and switch to it."""
    for p in profiles.list_profiles():
        d = paths.profile_dir_for(p["slug"])
        if is_demo(d):
            shutil.rmtree(d)  # always start the demo fresh
    summary = profiles.create(DEMO_NAME, activate_it=False)
    d = paths.profile_dir_for(summary["slug"])
    for name in ("resume_content.yaml", "target_companies.yaml"):
        shutil.copy(paths.EXAMPLES_DIR / name, d / name)
    cfg_raw = yaml.safe_load((paths.EXAMPLES_DIR / "config.yaml").read_text(encoding="utf-8"))
    cfg_raw.setdefault("resume", {})["tailor_enabled"] = False  # no Claude needed
    (d / "config.yaml").write_text(yaml.safe_dump(cfg_raw, sort_keys=False), encoding="utf-8")
    profiles._write_meta(d, demo=True)

    data = yaml.safe_load((DEMO_DIR / "jobs.yaml").read_text(encoding="utf-8"))
    cfg = load_config(d / "config.yaml")
    store = JobStore(cfg.paths.jobs_json)
    store.load()
    for i, raw in enumerate(data["jobs"], 1):
        store.upsert(_job(i, raw))
    store.save()

    apps = data.get("applications") or []
    tracker_db.insert_jobs(cfg, [
        {"company": a["company"], "role": a["role"], "verdict": "apply",
         "status": a["status"], "applied_at": _iso(a["applied_days_ago"]),
         "location": "San Francisco, CA", "fit_score": 8.0,
         "full_jd_text": "Fictional demo application."}
        for a in apps
    ], source="demo")
    for row in tracker_db.list_jobs(cfg):
        app = next((a for a in apps if a["role"] == row["role"]), None)
        if app is not None:
            tracker_db.set_follow_up(cfg, row["id"], _iso(app["applied_days_ago"] - 7))
    if activate:
        profiles.activate(summary["slug"])
    return profiles._summary(d, paths.active_slug())
