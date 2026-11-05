"""The only commands Jobi may run — a fixed allowlist.

Claude picks a command by NAME; the argv is fixed here, never built from
model text, and never run through a shell. Each one still needs the user to
click Apply on its card first.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .. import paths

TIMEOUT_SECONDS = 300
MAX_LOG = 4000


@dataclass(frozen=True)
class Command:
    name: str
    label: str        # shown on the card
    description: str  # shown to Claude
    shown: str        # the command line shown to the user
    run: Callable[[], dict[str, Any]]


def _exec(argv: list[str], cwd: str | None = None) -> dict[str, Any]:
    """Run a fixed argv (no shell). Returns {ok, log}. Never raises."""
    try:
        proc = subprocess.run(argv, cwd=cwd or str(paths.REPO_ROOT), capture_output=True,
                              text=True, timeout=TIMEOUT_SECONDS, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "log": str(e)}
    log = (proc.stdout + "\n" + proc.stderr).strip()
    return {"ok": proc.returncode == 0, "log": log[-MAX_LOG:]}


def _doctor() -> dict[str, Any]:
    return _exec([sys.executable, "-m", "job_pipeline.cli", "doctor"])


def _install_renderer() -> dict[str, Any]:
    from ..onboarding.system import install

    return install("renderer")


def _uv_sync() -> dict[str, Any]:
    uv = shutil.which("uv")
    if not uv:
        return {"ok": False, "log": "uv isn't installed. Run the Start launcher to install it."}
    return _exec([uv, "sync"])


def _claude_version() -> dict[str, Any]:
    from ..config import load_secrets
    from ..llm.cli_client import resolve_claude_cli

    cli = resolve_claude_cli(load_secrets().claude_cli_path)
    if not cli:
        return {"ok": False, "log": "The Claude Code CLI isn't installed (Setup → step 1)."}
    return _exec([cli, "--version"])


def _chrome_check() -> dict[str, Any]:
    from ..chrome_runner import check_chrome

    r = check_chrome()
    return {"ok": bool(r.get("connected")), "log": str(r.get("detail") or "")}


COMMANDS: dict[str, Command] = {c.name: c for c in [
    Command("doctor", "Check the app's setup",
            "Runs `job-pipeline doctor`: Python, Node, resume renderer, UI deps, Claude "
            "auth, profile and resume. Read-only.",
            "job-pipeline doctor", _doctor),
    Command("install_renderer", "Reinstall the resume renderer",
            "Runs `npm ci` in the resume renderer folder. Fixes .docx generation errors "
            "like 'Cannot find module docx'.",
            "npm ci (job_pipeline/resume/node)", _install_renderer),
    Command("uv_sync", "Reinstall Python packages",
            "Runs `uv sync` in the app folder. Fixes import errors / missing Python packages.",
            "uv sync", _uv_sync),
    Command("claude_version", "Check the Claude Code CLI",
            "Runs `claude --version`. Confirms the Claude Code CLI is installed and runs.",
            "claude --version", _claude_version),
    Command("chrome_check", "Test the Chrome connection",
            "Starts a tiny Claude Code session with Chrome and asks the Claude in Chrome "
            "extension for its tabs. Tells whether premium (Chrome) searches can run.",
            "claude --chrome (connection test)", _chrome_check),
]}


def describe() -> str:
    """The allowlist, for Claude's tool description."""
    return "; ".join(f"{c.name}: {c.description}" for c in COMMANDS.values())


def run_command(name: str) -> dict[str, Any]:
    cmd = COMMANDS.get(name)
    if cmd is None:
        return {"ok": False, "log": f"{name!r} isn't an allowed command."}
    return cmd.run()
