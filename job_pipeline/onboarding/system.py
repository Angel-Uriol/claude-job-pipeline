"""Setup step 1: check (and install) what the pipeline needs on this machine.

The web UI itself already runs (so Python + the UI's Node deps are present);
this covers the rest, each installable with one click on the Setup page:

* resume renderer — `npm ci` in job_pipeline/resume/node (renders .docx)
* Claude Code CLI  — Anthropic's official native installer; needed for the
                     default "Sign in with your Claude account" and for Chrome
* Google Chrome + the Claude in Chrome extension — optional, for premium
  searches; can't be installed from here, so the page links to them

Logging the CLI in is handled separately by onboarding/login.py (one-click
"Log in with Claude").
"""

from __future__ import annotations

import os
import platform
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
           optional: bool = False, why: str = "", group: str = "required",
           url: str = "", blocks: bool | None = None) -> dict[str, Any]:
    """One requirement row for the Setup page.

    group     "required" (the app needs it) or "chrome" (premium searches only)
    why       one plain sentence: what it's for
    url       where to get it when we can't install it with one click
    blocks    True = can't continue without it (defaults to required + not optional)
    """
    return {"id": cid, "label": label, "ok": ok, "detail": detail,
            "installable": installable and not ok, "optional": optional, "why": why,
            "group": group, "url": url,
            "blocks": (not optional) if blocks is None else blocks}


def _chrome_app() -> bool | None:
    """Is Google Chrome installed? None = can't tell on this OS."""
    if platform.system() == "Darwin":
        return any(Path(p).exists() for p in (
            "/Applications/Google Chrome.app",
            str(Path.home() / "Applications" / "Google Chrome.app")))
    if platform.system() == "Windows":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)",
                                                  "LOCALAPPDATA")]
        return any((Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe").exists()
                   for r in roots if r)
    return bool(shutil.which("google-chrome") or shutil.which("google-chrome-stable")) or None


def system_checks(cli_path: str = "") -> list[dict[str, Any]]:
    """Every prerequisite, for the Setup page checklist (Setup step 1)."""
    from ..chrome_runner import EXTENSION_URL, extension_installed

    out: list[dict[str, Any]] = []

    node = shutil.which("node")
    node_ver = ""
    if node:
        node_ver = subprocess.run([node, "--version"], capture_output=True, text=True,
                                  check=False).stdout.strip()
    out.append(_check("node", "Node.js 20+", bool(node), node_ver or
                      "Not found. The Start launcher installs it for you, or get it from "
                      "nodejs.org, then reload this page.",
                      why="Builds your tailored resumes as Word documents.",
                      url="" if node else "https://nodejs.org/"))

    renderer = (NODE_DIR / "node_modules" / "docx").exists()
    out.append(_check("renderer", "Resume builder", renderer,
                      "Installed" if renderer else "Not installed yet.",
                      installable=bool(node),
                      why="The small add-on that turns your resume into a .docx file."))

    cli = resolve_claude_cli(cli_path)
    out.append(_check("claude_cli", "Claude Code", bool(cli),
                      cli or "Not installed yet (free, about a minute).",
                      installable=True, blocks=False,
                      why="Lets the app sign in with your Claude account (Pro or Max) and "
                          "run Chrome searches. Not needed if you'll use an API key."))

    chrome = _chrome_app()
    out.append(_check("chrome", "Google Chrome", chrome is not False,
                      "Installed" if chrome else ("Not found." if chrome is False
                                                  else "Couldn't check on this computer."),
                      optional=True, group="chrome",
                      why="Premium searches browse company careers sites in your Chrome.",
                      url="" if chrome else "https://www.google.com/chrome/"))

    ext = extension_installed()
    out.append(_check("chrome_extension", "Claude in Chrome extension", bool(ext),
                      "Installed" if ext else "Not installed. Add it to Chrome, click its "
                      "icon and sign in with your Claude account.",
                      optional=True, group="chrome",
                      why="Lets Claude read careers pages in your browser (read-only).",
                      url="" if ext else EXTENSION_URL))
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
