"""Where things live on disk.

Code, prompts and examples ship in the repo. Everything personal (your
profile, resume content, target companies) lives in ONE folder, ``profile/``
by default, which is gitignored and written by the setup questionnaire.
Point ``JOB_PIPELINE_PROFILE_DIR`` somewhere else to keep several profiles.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
EXAMPLES_DIR = REPO_ROOT / "examples" / "profile"
ENV_FILE = REPO_ROOT / ".env"


def profile_dir() -> Path:
    """The folder holding this user's config + resume + target list."""
    override = os.environ.get("JOB_PIPELINE_PROFILE_DIR", "").strip()
    return Path(override).expanduser() if override else REPO_ROOT / "profile"


def config_path() -> Path:
    return profile_dir() / "config.yaml"


def resume_content_path() -> Path:
    return profile_dir() / "resume_content.yaml"


def target_companies_path() -> Path:
    return profile_dir() / "target_companies.yaml"


def interview_state_path() -> Path:
    """Saved questionnaire conversation, so setup can resume where it stopped."""
    return profile_dir() / ".interview.json"


def is_configured() -> bool:
    """True once setup has written a config (the UI redirects to /setup otherwise)."""
    return config_path().exists()
