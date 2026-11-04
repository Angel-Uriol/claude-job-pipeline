"""Today screen: matches, plain reasons, checklist, follow-ups."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from job_pipeline.checklist import checklist, next_step, reasons_and_gaps, score_label
from job_pipeline.models import ScoreResult, Status, Verdict
from job_pipeline.store import JobStore
from job_pipeline.ui import create_app
from tests.test_ui_server import _make_job, _write_isolated_config


def _client(tmp_path: Path) -> TestClient:
    cfg = _write_isolated_config(tmp_path)
    store = JobStore(tmp_path / "jobs.json")
    store.load()
    store.upsert(_make_job(job_id="a", title="Top", fit_score=8.6, fit_verdict=Verdict.APPLY,
                           fit_reasons=["Python everywhere"], fit_gaps=["No Terraform"]))
    store.upsert(_make_job(job_id="b", title="Okay", fit_score=6.0, fit_verdict=Verdict.CONSIDER,
                           resume_path="/tmp/x.docx", status=Status.RESUME_ONLY))
    store.upsert(_make_job(job_id="c", title="Skip me", fit_score=3.0, fit_verdict=Verdict.SKIP))
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    store.upsert(_make_job(job_id="d", title="Applied long ago", fit_score=8.0,
                           fit_verdict=Verdict.APPLY, status=Status.APPLIED, applied_at=old))
    store.save()
    return TestClient(create_app(config_path=str(cfg)))


def test_today_lists_matches_in_plain_words(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/today").json()
    titles = [m["title"] for m in body["matches"]]
    assert titles == ["Top", "Okay"]  # skips and applied jobs are left out
    top = body["matches"][0]
    assert top["label"] == "Strong match"
    assert top["reasons"] == ["Python everywhere"] and top["gaps"] == ["No Terraform"]
    assert top["next_step"]["id"] == "resume"


def test_waiting_on_you_has_follow_up_and_unsent_resume(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/today").json()
    kinds = {(w["kind"], w["title"]) for w in body["waiting"]}
    assert ("follow_up", "Applied long ago") in kinds
    assert ("resume_not_sent", "Okay") in kinds


def test_follow_up_done_and_snooze_remove_it(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.post("/api/follow-ups", json={"action": "snooze", "days": 3,
                                           "job_key": "d"}).status_code == 200
    kinds = [w["kind"] for w in c.get("/api/today").json()["waiting"]]
    assert "follow_up" not in kinds
    assert c.post("/api/follow-ups", json={"action": "done", "job_key": "d"}).json() == {
        "follow_up_at": "done"}


def test_mark_applied_sets_follow_up_and_open_is_recorded(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.post("/api/jobs/a/opened").json()["apply_opened_at"]
    job = c.post("/api/jobs/a/apply").json()
    due = datetime.fromisoformat(job["follow_up_at"])
    assert timedelta(days=6) < due - datetime.now(UTC) <= timedelta(days=7)


def test_checklist_order_and_next_step() -> None:
    j = _make_job(fit_score=7.0)
    steps = checklist(j)
    assert [s["id"] for s in steps] == ["resume", "cover_letter", "open", "applied"]
    j.resume_path = "/r.docx"
    assert next_step(checklist(j))["id"] == "open"  # cover letter is optional


def test_reasons_fall_back_to_subscores_for_old_jobs() -> None:
    j = _make_job(fit_subscores=ScoreResult(stack=9, level=8, function=7, bridgeability=4,
                                            desirability=6), honest_bridge_notes="Say X.")
    reasons, gaps = reasons_and_gaps(j)
    assert reasons[0] == "Strong skills overlap"
    assert gaps == ["Weaker gaps you can explain", "Say X."]


def test_score_labels() -> None:
    assert [score_label(s) for s in (None, 8.0, 6.5, 5.0, 2.0)] == [
        "Not scored yet", "Strong match", "Good match", "Possible match", "Weak match"]
