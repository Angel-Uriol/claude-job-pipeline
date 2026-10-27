"""Setup step 1: check (and install) what the pipeline needs on this machine.

The web UI itself already runs (so Python + the UI's Node deps are present);
this covers the rest, each installable with one click on the Setup page:

* resume renderer — `npm ci` in job_pipeline/resume/node (renders .docx)
* Claude Code CLI  — Anthropic's official native installer; only needed for
                     "My Claude Code login" mode

Logging the CLI in is interactive (`claude setup-token` opens a browser and
prints a token), so we can't do it from a web request. `open_login_terminal`
opens a Terminal window running it; the user pastes the token on the page.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import paths
from ..llm.cli_client import resolve_claude_cli

NODE_DIR = paths.REPO_ROOT / "job_pipeline" / "resume" / "node"

# Anthropic's official Claude Code installers (install to ~/.local/bin, no sudo).
CLAUDE_INSTALL_UNIX = "curl -fsSL https://claude.ai/install.sh | bash"
CLAUDE_INSTALL_WINDOWS = "irm https://claude.ai/install.ps1 | iex"

INSTALL_TIMEOUT = 600  # seconds


def _check(cid: str, label: str, ok: bool, detail: str, *, installable: bool = False,
           optional: bool = False) -> dict[str, Any]:
    return {"id": cid, "label": label, "ok": ok, "detail": detail,
            "installable": installable and not ok, "optional": optional}


def system_checks(cli_path: str = "") -> list[dict[str, Any]]:
    """Status of every prerequisite, for the Setup page checklist."""
    out: list[dict[str, Any]] = []

    node = shutil.which("node")
    node_ver = ""
    if node:
        node_ver = subprocess.run([node, "--version"], capture_output=True, text=True,
                                  check=False).stdout.strip()
    out.append(_check("node", "Node.js 20+", bool(node), node_ver or
                      "Install Node.js from nodejs.org, then reload this page."))

    renderer = (NODE_DIR / "node_modules" / "docx").exists()
    out.append(_check("renderer", "Resume renderer (.docx)", renderer,
                      "installed" if renderer else "Needed to create tailored resumes.",
                      installable=bool(node)))

    cli = resolve_claude_cli(cli_path)
    out.append(_check("claude_cli", "Claude Code CLI", bool(cli),
                      cli or "Only needed for the “My Claude Code login” option.",
                      installable=True, optional=True))
    return out


def _run(cmd: list[str] | str, *, cwd: Path | None = None, shell: bool = False) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True, text=True,
                              timeout=INSTALL_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "log": str(e)}
    log = (proc.stdout + "\n" + proc.stderr).strip()
    return {"ok": proc.returncode == 0, "log": log[-4000:]}


def install(component: str) -> dict[str, Any]:
    """Install one component. Returns {ok, log}. Never raises."""
    if component == "renderer":
        npm = shutil.which("npm")
        if not npm:
            return {"ok": False, "log": "npm not found — install Node.js first."}
        return _run([npm, "ci", "--no-audit", "--no-fund"], cwd=NODE_DIR)
    if component == "claude_cli":
        if platform.system() == "Windows":
            return _run(["powershell", "-NoProfile", "-Command", CLAUDE_INSTALL_WINDOWS])
        result = _run(CLAUDE_INSTALL_UNIX, shell=True)
        # The installer drops the binary in ~/.local/bin, which a running
        # server's PATH may not include yet.
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")
        return result
    return {"ok": False, "log": f"Unknown component {component!r}."}


def open_login_terminal(cli_path: str = "") -> dict[str, Any]:
    """Open a terminal running `claude setup-token` (macOS); else return the command."""
    cli = resolve_claude_cli(cli_path)
    if not cli:
        return {"opened": False, "command": None,
                "detail": "Install the Claude Code CLI first."}
    command = f"{shlex.quote(cli)} setup-token"
    if platform.system() == "Darwin" and shutil.which("osascript"):
        escaped = command.replace("\\", "\\\\").replace('"', '\\"')  # AppleScript string
        script = f'tell application "Terminal" to do script "{escaped}"'
        proc = subprocess.run(["osascript", "-e", script, "-e",
                               'tell application "Terminal" to activate'],
                              capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return {"opened": True, "command": command,
                    "detail": "Follow the browser login, then copy the sk-ant-oat… token "
                              "Terminal prints and paste it below."}
    return {"opened": False, "command": command,
            "detail": "Run this in a terminal, log in, then paste the printed token below."}
