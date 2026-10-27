"""`job-pipeline doctor` — check that everything a run needs is in place.

Read-only: it reports problems and how to fix them, and changes nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable

from .. import paths
from ..config import load_config, load_secrets
from ..llm import llm_available

Check = tuple[str, bool, str]  # (label, ok, detail / fix)


def _python() -> Check:
    ok = sys.version_info >= (3, 11)
    return ("Python ≥ 3.11", ok, sys.version.split()[0] if ok else "Install Python 3.11+")


def _node() -> Check:
    node = shutil.which("node")
    if not node:
        return ("Node.js", False, "Install Node 20+ (needed to render .docx resumes)")
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    return ("Node.js", True, out.stdout.strip())


def _resume_renderer() -> Check:
    nm = paths.REPO_ROOT / "job_pipeline" / "resume" / "node" / "node_modules" / "docx"
    return ("Resume renderer deps", nm.exists(), "ok" if nm.exists() else "Run `make install`")


def _ui_deps() -> Check:
    nm = paths.REPO_ROOT / "ui" / "node_modules" / "next"
    return ("Web UI deps", nm.exists(), "ok" if nm.exists() else "Run `make install`")


def _auth() -> Check:
    s = load_secrets()
    ok = llm_available(s)
    if s.auth_mode == "claude_code":
        fix = "found `claude` CLI" if ok else "Install Claude Code and run `claude` once to log in"
    else:
        fix = "API key set" if ok else "Run `job-pipeline auth` or use the Setup page"
    return (f"Claude auth ({s.auth_mode})", ok, fix)


def _profile() -> Check:
    if not paths.is_configured():
        return ("Profile", False, "Run `job-pipeline setup` or open the Setup page")
    try:
        cfg = load_config()
    except Exception as e:  # noqa: BLE001 - report any validation problem
        return ("Profile", False, f"profile/config.yaml is invalid: {e}")
    n = sum(len(getattr(cfg.scrapers, k)) for k in ("greenhouse", "lever", "ashby"))
    return ("Profile", True, f"{cfg.profile.name} — {n} scrapeable company boards")


def _resume_content() -> Check:
    p = paths.resume_content_path()
    if not p.exists():
        return ("Resume content", False, "Missing — setup writes it from your resume")
    from ..resume.content_loader import load_resume_content

    try:
        rc = load_resume_content(p)
    except Exception as e:  # noqa: BLE001
        return ("Resume content", False, f"Invalid: {e}")
    if not rc.experiences:
        return ("Resume content", False, f"{p} has no experience entries — add some")
    return ("Resume content", True, f"{len(rc.experiences)} roles")


CHECKS: list[Callable[[], Check]] = [
    _python, _node, _resume_renderer, _ui_deps, _auth, _profile, _resume_content,
]


def run_doctor() -> int:
    bad = 0
    for fn in CHECKS:
        label, ok, detail = fn()
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {label:<26} {detail}")
    print("\nAll good." if not bad else f"\n{bad} problem(s) — see the fixes above.")
    return 0 if not bad else 1
