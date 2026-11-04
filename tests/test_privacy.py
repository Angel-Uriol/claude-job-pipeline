"""Delete all my data: typed confirmation, refusals, what's removed."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_pipeline import paths, profiles
from job_pipeline.ui import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("JOB_PIPELINE_PROFILE_DIR", raising=False)
    monkeypatch.setenv("JOB_PIPELINE_PROFILES_DIR", str(tmp_path / "profiles"))
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(paths, "LEGACY_PROFILE_DIR", tmp_path / "profile")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-test\nJOB_PIPELINE_AUTH_MODE=api_key\n")
    monkeypatch.setattr(paths, "ENV_FILE", env)
    profiles.create("Throwaway Person")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "usage.jsonl").write_text("{}\n")
    return TestClient(create_app(config_path=str(tmp_path / "unused.yaml")))


def test_inventory_lists_what_will_go(client: TestClient) -> None:
    inv = client.get("/api/privacy").json()
    assert [p["name"] for p in inv["profiles"]] == ["Throwaway Person"]
    assert inv["keys"] == ["ANTHROPIC_API_KEY"] and inv["pinned"] is False


def test_wrong_confirmation_is_refused(client: TestClient, tmp_path: Path) -> None:
    r = client.post("/api/privacy/delete-all", json={"confirm": "delete"})
    assert r.status_code == 400
    assert (tmp_path / "profiles").exists()


def test_refused_while_running(client: TestClient, tmp_path: Path) -> None:
    client.app.state.pipeline_state.running = "daily"  # type: ignore[attr-defined]
    assert client.post("/api/privacy/delete-all", json={"confirm": "DELETE"}).status_code == 409
    assert (tmp_path / "profiles").exists()


def test_delete_everything(client: TestClient, tmp_path: Path) -> None:
    r = client.post("/api/privacy/delete-all", json={"confirm": "DELETE"}).json()
    assert not (tmp_path / "profiles").exists() and not (tmp_path / "data").exists()
    assert r["keys_cleared"] == ["ANTHROPIC_API_KEY"]
    env = (tmp_path / ".env").read_text()
    assert "sk-ant-test" not in env and "JOB_PIPELINE_AUTH_MODE=api_key" in env


def test_pinned_folder_is_never_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    monkeypatch.setenv("JOB_PIPELINE_PROFILE_DIR", str(pinned))
    c = TestClient(create_app(config_path=str(tmp_path / "x.yaml")))
    assert c.post("/api/privacy/delete-all", json={"confirm": "DELETE"}).status_code == 409
    assert pinned.exists()
