"""In-app profile + resume editors: validation, .bak, ids, honesty nudge."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from job_pipeline.ui import create_app
from job_pipeline.ui.edit_routes import assign_ids
from tests.conftest import EXAMPLE_PROFILE
from tests.test_ui_server import _write_isolated_config


def _client(tmp_path: Path) -> TestClient:
    cfg = _write_isolated_config(tmp_path)
    shutil.copy(EXAMPLE_PROFILE / "resume_content.yaml", tmp_path / "resume_content.yaml")
    return TestClient(create_app(config_path=str(cfg)))


def test_profile_round_trip_keeps_comments_and_backup(tmp_path: Path) -> None:
    c = _client(tmp_path)
    prof = c.get("/api/profile").json()["profile"]
    prof["headline"] = "Backend engineer who likes data"
    prof["must_have_skills"] = ["Python", "SQL"]
    assert c.put("/api/profile", json={"profile": prof}).json()["saved"] is True
    text = (tmp_path / "config.yaml").read_text()
    assert text.startswith("# Pipeline config")  # header comment kept
    assert yaml.safe_load(text)["profile"]["headline"] == "Backend engineer who likes data"
    assert (tmp_path / "config.yaml.bak").exists()
    assert c.get("/api/profile").json()["profile"]["must_have_skills"] == ["Python", "SQL"]


def test_profile_validation_error_is_plain(tmp_path: Path) -> None:
    c = _client(tmp_path)
    prof = c.get("/api/profile").json()["profile"]
    prof["target_functions"] = [{"name": "Backend", "tier": "Z"}]
    r = c.put("/api/profile", json={"profile": prof})
    assert r.status_code == 400 and r.json()["detail"].startswith("Please fix:")


def test_resume_edit_keeps_ids_and_assigns_new_ones(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resume = c.get("/api/resume").json()["resume"]
    first = resume["experiences"][0]
    old_ids = [b["id"] for b in first["bullets"]]
    first["bullets"].append({"id": "", "text": "Mentored two new engineers", "tags": []})
    assert c.put("/api/resume", json={"resume": resume}).json()["saved"] is True
    saved = c.get("/api/resume").json()["resume"]["experiences"][0]
    ids = [b["id"] for b in saved["bullets"]]
    assert ids[:-1] == old_ids and ids[-1] not in old_ids and ids[-1].startswith("b")
    assert saved["key"] == first["key"]


def test_new_numbers_need_confirmation(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resume = c.get("/api/resume").json()["resume"]
    resume["experiences"][0]["bullets"][0]["text"] = "Grew revenue 312% in one quarter"
    r = c.put("/api/resume", json={"resume": resume}).json()
    assert r["saved"] is False and "312%" in r["warnings"][0]
    assert c.put("/api/resume", json={"resume": resume, "confirm": True}).json()["saved"]


def test_assign_ids_fixes_duplicates() -> None:
    data = {"experiences": {"a": {"bullets": [{"id": "b1"}, {"id": "b1"}, {"id": ""}]}},
            "projects": {"p": {"bullets": [{"id": ""}]}}}
    out = assign_ids(data)
    assert [b["id"] for b in out["experiences"]["a"]["bullets"]] == ["b1", "b2", "b3"]
    assert out["projects"]["p"]["bullets"][0]["id"] == "p1"
