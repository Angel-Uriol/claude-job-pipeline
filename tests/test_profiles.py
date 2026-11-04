"""Multiple profiles: create / switch / isolate / migrate / export / import."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from job_pipeline import paths, profiles, tracker_db
from job_pipeline.config import load_config
from tests.conftest import EXAMPLE_PROFILE


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Unpinned profiles under tmp (conftest pins the example profile)."""
    monkeypatch.delenv("JOB_PIPELINE_PROFILE_DIR", raising=False)
    r = tmp_path / "profiles"
    monkeypatch.setenv("JOB_PIPELINE_PROFILES_DIR", str(r))
    monkeypatch.setattr(paths, "LEGACY_PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    return r


def test_never_migrates_into_a_custom_profiles_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("JOB_PIPELINE_PROFILE_DIR", raising=False)
    monkeypatch.setenv("JOB_PIPELINE_PROFILES_DIR", str(tmp_path / "elsewhere"))
    legacy = tmp_path / "repo" / "profile"
    legacy.mkdir(parents=True)
    (legacy / ".interview.json").write_text("{}")
    monkeypatch.setattr(paths, "LEGACY_PROFILE_DIR", legacy)
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "repo")
    assert profiles.migrate_legacy() is None
    assert (legacy / ".interview.json").exists()  # left alone


def _configure(slug: str) -> Path:
    """Copy the fictional example profile into `slug` and record one applied job."""
    d = paths.profile_dir_for(slug)
    for name in ("config.yaml", "resume_content.yaml", "target_companies.yaml"):
        shutil.copy(EXAMPLE_PROFILE / name, d / name)
    cfg = load_config(d / "config.yaml")
    tracker_db.insert_jobs(cfg, [{"company": "Stripe", "role": "Backend Engineer",
                                  "job_id": "1"}])
    jid = tracker_db.list_jobs(cfg)[0]["id"]
    tracker_db.set_status(cfg, jid, "applied")
    return d


def test_create_switch_and_isolation(root: Path) -> None:
    a = profiles.create("Angel")
    assert a["slug"] == "angel" and paths.active_slug() == "angel"
    _configure("angel")
    b = profiles.create("Sam Lee")
    assert b["slug"] == "sam-lee" and paths.active_slug() == "sam-lee"
    assert not paths.is_configured()  # Sam starts empty
    profiles.activate("angel")
    cfg = load_config()
    # Angel's history lives inside Angel's folder.
    assert cfg.tracker.db_path.startswith(str(root / "angel"))
    names = {p["slug"]: p for p in profiles.list_profiles()}
    assert names["angel"]["applied"] == 1 and names["angel"]["active"]
    assert names["sam-lee"]["tracked_jobs"] == 0
    assert profiles.list_profiles()[0]["slug"] == "angel"  # active first


def test_unique_slugs_and_bad_names(root: Path) -> None:
    assert profiles.create("Sam")["slug"] == "sam"
    assert profiles.create("Sam")["slug"] == "sam-2"
    with pytest.raises(profiles.ProfileError):
        profiles.create("   ")
    with pytest.raises(ValueError):
        profiles.activate("../etc")


def test_delete_moves_active(root: Path) -> None:
    profiles.create("A")
    profiles.create("B")
    profiles.delete("b")
    assert paths.active_slug() == "a"
    assert not (root / "b").exists()


def test_rename_changes_display_name(root: Path) -> None:
    profiles.create("Old")
    assert profiles.rename("old", "New Name")["name"] == "New Name"


def test_migrates_legacy_single_profile(root: Path, tmp_path: Path) -> None:
    legacy = tmp_path / "profile"
    legacy.mkdir()
    shutil.copy(EXAMPLE_PROFILE / "config.yaml", legacy / "config.yaml")
    (legacy / ".interview.json").write_text("{}")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "jobs.json").write_text("[]")
    profiles.ensure_active()
    slug = paths.active_slug()
    assert slug == "jordan-rivera"  # named after the config's candidate
    assert (root / slug / ".interview.json").exists()
    assert (root / slug / "data" / "jobs.json").exists()
    assert not legacy.exists()


def test_fresh_install_gets_a_default_profile(root: Path) -> None:
    profiles.ensure_active()
    assert (root / "default").is_dir()
    assert profiles.list_profiles()[0]["name"] == "My profile"


def test_pinned_dir_disables_switching(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profiles.create("A")
    monkeypatch.setenv("JOB_PIPELINE_PROFILE_DIR", str(EXAMPLE_PROFILE))
    assert not profiles.switching_enabled()
    with pytest.raises(profiles.ProfileError):
        profiles.activate("a")


def test_export_import_round_trip(root: Path) -> None:
    profiles.create("Angel")
    _configure("angel")
    bundle = profiles.export_profile("angel")
    assert bundle["format"] == profiles.FORMAT
    assert "paths" not in bundle["config"]  # machine-specific, stripped
    assert bundle["tracker"]["jobs"][0]["status"] == "applied"
    json.dumps(bundle)  # JSON-serializable

    restored = profiles.import_bundle(json.loads(json.dumps(bundle)), name="Angel copy")
    assert restored["slug"] == "angel-copy" and restored["restored"]["jobs"] == 1
    assert paths.active_slug() == "angel-copy"
    cfg = load_config()
    assert cfg.profile.name == "Jordan Rivera"
    # The applied history came along, so the applied guard still blocks it.
    assert tracker_db.list_jobs(cfg)[0]["status"] == "applied"
    # Exporting without history leaves the tracker out.
    assert "tracker" not in profiles.export_profile("angel", include_history=False)


def test_import_rejects_invalid_bundle(root: Path) -> None:
    bad = {"format": profiles.FORMAT, "version": 1, "name": "X", "config": {"profile": {}}}
    with pytest.raises(profiles.ProfileError, match="config"):
        profiles.import_bundle(bad)
    assert profiles.list_profiles() == []  # nothing half-created


def test_smart_import_prefills_chat(root: Path) -> None:
    from job_pipeline.onboarding.interview import InterviewState, ProfileDraft

    class FakeLLM:
        auth_mode = "api_key"

        def structured(self, prompt: str, schema: type[Any], **kw: Any) -> Any:
            assert "Sam Lee" in prompt
            return ProfileDraft(name="Sam Lee", email="sam@example.com", industry="health")

    out = profiles.smart_import({"person": "Sam Lee", "field": "health"},
                                llm=FakeLLM(), model="m")
    assert out["name"] == "Sam Lee" and out["active"]
    state = InterviewState.load()
    assert state.phase == "round1" and state.draft.industry == "health"
    assert state.messages[-1].kind == "note"


# --- HTTP ------------------------------------------------------------------


@pytest.fixture
def client(root: Path) -> TestClient:
    from job_pipeline.ui import create_app

    return TestClient(create_app())


def test_profile_routes(client: TestClient, root: Path) -> None:
    listed = client.get("/api/profiles").json()
    assert listed["switching_enabled"] and listed["active"] == "default"
    assert client.post("/api/profiles", json={"name": "Sam"}).json()["slug"] == "sam"
    assert client.get("/api/setup/status").json()["configured"] is False
    _configure("sam")
    assert client.get("/api/setup/status").json()["configured"] is True
    assert client.post("/api/profiles/default/activate").status_code == 200
    assert client.get("/api/setup/status").json()["configured"] is False  # follows switch

    exported = client.get("/api/profiles/sam/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    files = {"file": ("sam.json", exported.content, "application/json")}
    imp = client.post("/api/profiles/import", files=files, data={"name": "Sam 2"}).json()
    assert imp["mode"] == "restore" and imp["slug"] == "sam-2"

    bad = client.post("/api/profiles/import",
                      files={"file": ("x.json", b"not json", "application/json")})
    assert bad.status_code == 400
    # Free-form JSON needs Claude (no key in tests) → clear 503.
    free = client.post("/api/profiles/import",
                       files={"file": ("x.json", b'{"name": "Kim"}', "application/json")})
    assert free.status_code == 503
    assert client.delete("/api/profiles/sam-2").json() == {"deleted": "sam-2"}
    assert client.post("/api/profiles/nope/activate").status_code == 400
