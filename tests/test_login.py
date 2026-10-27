"""One-click Claude login: run a (fake) `claude setup-token` in a pty and
capture the token it prints. The fake mimics the real CLI's colored output."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="pty is POSIX-only")

FAKE_CLI = """#!/bin/sh
[ -t 1 ] || { echo "not a tty" >&2; exit 3; }
printf '\\033[1mOpening browser to sign in…\\033[0m\\r\\n'
printf 'If it did not open, visit: https://claude.ai/oauth/authorize?code=true&x=1\\r\\n'
sleep 1
printf '\\033[32m✓ Long-lived authentication token created:\\033[0m\\r\\n\\r\\n'
# One write, like the real CLI's full-screen redraw: token + the next line.
printf 'sk-ant-oat01-ABCdef_123-xyzXYZ0987654321abcdEFGH\\r\\nStore\\033[1Cthis\\033[1Ctoken\\033[1Csecurely.\\r\\n'
sleep 30
"""


def _wait(login: object, until: str, timeout: float = 10) -> dict[str, object]:
    end = time.time() + timeout
    while time.time() < end:
        s = login.status()  # type: ignore[attr-defined]
        if s["state"] == until:
            return s  # type: ignore[no-any-return]
        time.sleep(0.1)
    raise AssertionError(f"login never reached {until!r}: {login.status()}")  # type: ignore[attr-defined]


def test_login_captures_token_and_saves_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import job_pipeline.onboarding.env_file as ef
    from job_pipeline.onboarding import login

    cli = tmp_path / "claude"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    env = tmp_path / ".env"
    monkeypatch.setattr(ef.paths, "ENV_FILE", env)
    monkeypatch.setattr(login, "_S", login._Session())

    first = login.start(str(cli))
    assert first["state"] == "running"
    s = _wait(login, "done")
    assert "sk-ant-oat01-ABCdef_123-xyzXYZ0987654321abcdEFGH" not in str(s)  # never leaked
    assert "saved" in str(s["message"])
    text = env.read_text()
    # Exact line: nothing from the following "Store this token…" line glued on.
    assert "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-ABCdef_123-xyzXYZ0987654321abcdEFGH" \
        in text.splitlines()
    assert "JOB_PIPELINE_AUTH_MODE=claude_code" in text


def test_login_reports_auth_url_and_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_pipeline.onboarding import login

    cli = tmp_path / "claude"
    cli.write_text(
        "#!/bin/sh\nprintf 'visit https://claude.ai/oauth/authorize?a=1\\r\\n'\nsleep 30\n"
    )
    cli.chmod(0o755)
    monkeypatch.setattr(login, "_S", login._Session())
    login.start(str(cli))
    end = time.time() + 5
    while login.status()["auth_url"] is None and time.time() < end:
        time.sleep(0.1)
    assert login.status()["auth_url"] == "https://claude.ai/oauth/authorize?a=1"
    assert login.cancel()["state"] == "idle"


def test_login_errors_without_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_pipeline.onboarding import login

    monkeypatch.setattr(login, "_S", login._Session())
    monkeypatch.setattr(login, "resolve_claude_cli", lambda _p="": None)
    assert login.start()["state"] == "error"
