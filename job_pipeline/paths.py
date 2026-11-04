"""Where things live on disk.

Code, prompts and examples ship in the repo. Everything personal lives in a
PROFILE — one folder per person, so several people (or several job searches)
can share one install without mixing anything:

    profiles/
      .active                 ← slug of the profile in use
      angel/
        config.yaml           profile + scrapers + filters
        resume_content.yaml   every bullet / job / degree
        target_companies.yaml
        .interview.json       the setup chat, so it can resume
        data/                 jobs.json, tracker.db, digests  (that person's history)
        outputs/resumes/      their generated .docx files
      sam/
        ...

The whole `profiles/` tree is gitignored. Relative paths inside a profile's
config.yaml resolve against that profile's folder (see `config.load_config`).

Overrides:
* ``JOB_PIPELINE_PROFILE_DIR`` — pin ONE folder as the profile (switching is
  disabled). Used by tests and by anyone who keeps a profile elsewhere.
* ``JOB_PIPELINE_PROFILES_DIR`` — use another folder as the profiles root.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
EXAMPLES_DIR = REPO_ROOT / "examples" / "profile"
ENV_FILE = REPO_ROOT / ".env"
#: Pre-profiles layout (a single `profile/` folder) — migrated on startup.
LEGACY_PROFILE_DIR = REPO_ROOT / "profile"

DEFAULT_SLUG = "default"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def pinned_profile_dir() -> Path | None:
    """The folder set by JOB_PIPELINE_PROFILE_DIR, if any (disables switching)."""
    override = os.environ.get("JOB_PIPELINE_PROFILE_DIR", "").strip()
    return Path(override).expanduser() if override else None


def profiles_root() -> Path:
    override = os.environ.get("JOB_PIPELINE_PROFILES_DIR", "").strip()
    return Path(override).expanduser() if override else REPO_ROOT / "profiles"


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


def active_slug() -> str:
    """Slug of the profile in use (``profiles/.active``), or "default"."""
    marker = profiles_root() / ".active"
    try:
        slug = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_SLUG
    return slug if is_valid_slug(slug) else DEFAULT_SLUG


def profile_dir_for(slug: str) -> Path:
    if not is_valid_slug(slug):
        raise ValueError(f"invalid profile name {slug!r}")
    return profiles_root() / slug


def profile_dir() -> Path:
    """The folder holding the active profile's config, resume, history."""
    return pinned_profile_dir() or profile_dir_for(active_slug())


def config_path() -> Path:
    return profile_dir() / "config.yaml"


def resume_content_path() -> Path:
    return profile_dir() / "resume_content.yaml"


def target_companies_path() -> Path:
    return profile_dir() / "target_companies.yaml"


def interview_state_path() -> Path:
    """Saved setup chat, so setup can resume where it stopped."""
    return profile_dir() / ".interview.json"


def is_configured() -> bool:
    """True once setup has written a config (the UI redirects to /setup otherwise)."""
    return config_path().exists()
