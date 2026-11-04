"""Multiple profiles: one complete "memory" per person.

A profile is a folder under `profiles/` (see paths.py) holding everything about
one job seeker: config, resume content, target companies, the setup chat, and
their own history (Discover inbox, tracker DB with what they applied to,
digests, generated resumes). Switching profiles switches all of it.

Export / import uses one JSON file, so a profile can move between machines or
be handed to someone:

    {
      "format": "claude-job-pipeline.profile",
      "version": 1,
      "name": "Angel",
      "exported_at": "...",
      "config": {...},              # profile/config.yaml (paths stripped)
      "resume_content": {...},      # profile/resume_content.yaml
      "target_companies": {...},    # profile/target_companies.yaml
      "interview": {...},           # the setup chat (optional)
      "tracker": {"jobs": [...], "contacts": [...]}   # applied history (optional)
    }

Any OTHER JSON (a resume export, notes about a person, an old context file)
can be imported too: Claude reads it and pre-fills a new profile's setup
chat, which then only asks about what's missing (see `smart_import`).
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .config import Config, anchor_paths

FORMAT = "claude-job-pipeline.profile"
VERSION = 1
MAX_IMPORT_BYTES = 20 * 1024 * 1024


class ProfileError(ValueError):
    """A user-facing problem with a profile operation."""


# --- naming ---------------------------------------------------------------


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return s or "profile"


def _unique_slug(name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while (paths.profiles_root() / slug).exists():
        slug = f"{base[:36]}-{n}"
        n += 1
    return slug


def _meta_path(d: Path) -> Path:
    return d / "profile.json"


def _read_meta(d: Path) -> dict[str, Any]:
    try:
        data = json.loads(_meta_path(d).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_meta(d: Path, **fields: Any) -> None:
    meta = {**_read_meta(d), **fields}
    _meta_path(d).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def display_name(d: Path) -> str:
    """profile.json name → the config's candidate name → the folder slug."""
    meta = _read_meta(d)
    if meta.get("name"):
        return str(meta["name"])
    cfg = d / "config.yaml"
    if cfg.exists():
        try:
            raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            name = (raw.get("profile") or {}).get("name")
            if name:
                return str(name)
        except yaml.YAMLError:
            pass
    return d.name


# --- listing / switching --------------------------------------------------


def switching_enabled() -> bool:
    return paths.pinned_profile_dir() is None


def _summary(d: Path, active: str) -> dict[str, Any]:
    from . import tracker_db
    from .config import load_config

    applied = tracked = 0
    if (d / "config.yaml").exists():
        try:
            st = tracker_db.stats(load_config(d / "config.yaml"))
            tracked = int(st["total"]) + int(st["hidden_count"])
            applied = int(st["applied_count"])
        except Exception:  # noqa: BLE001 - a broken profile still lists
            pass
    meta = _read_meta(d)
    return {
        "slug": d.name,
        "name": display_name(d),
        "active": d.name == active,
        "configured": (d / "config.yaml").exists(),
        "chat_started": (d / ".interview.json").exists(),
        "tracked_jobs": tracked,
        "applied": applied,
        "created_at": meta.get("created_at"),
    }


def list_profiles() -> list[dict[str, Any]]:
    root = paths.profiles_root()
    active = paths.active_slug()
    if not root.exists():
        return []
    dirs = sorted(
        (d for d in root.iterdir() if d.is_dir() and paths.is_valid_slug(d.name)),
        key=lambda d: (d.name != active, display_name(d).lower()),
    )
    return [_summary(d, active) for d in dirs]


def _reset_caches() -> None:
    from .target_companies import default_target_index

    default_target_index.cache_clear()


def activate(slug: str) -> dict[str, Any]:
    if not switching_enabled():
        raise ProfileError("Profiles are pinned by JOB_PIPELINE_PROFILE_DIR.")
    d = paths.profile_dir_for(slug)
    if not d.is_dir():
        raise ProfileError(f"No profile named {slug!r}.")
    root = paths.profiles_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".active").write_text(slug, encoding="utf-8")
    _reset_caches()
    return _summary(d, slug)


def create(name: str, *, activate_it: bool = True) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ProfileError("Give the profile a name.")
    slug = _unique_slug(name)
    d = paths.profiles_root() / slug
    d.mkdir(parents=True)
    _write_meta(d, name=name, created_at=datetime.now(UTC).isoformat())
    if activate_it:
        activate(slug)
    return _summary(d, paths.active_slug())


def rename(slug: str, name: str) -> dict[str, Any]:
    d = paths.profile_dir_for(slug)
    if not d.is_dir():
        raise ProfileError(f"No profile named {slug!r}.")
    if not name.strip():
        raise ProfileError("Give the profile a name.")
    _write_meta(d, name=name.strip())
    return _summary(d, paths.active_slug())


def delete(slug: str) -> None:
    """Delete a profile folder for good. The active profile moves to another
    one (or none) afterwards."""
    d = paths.profile_dir_for(slug)
    if not d.is_dir():
        raise ProfileError(f"No profile named {slug!r}.")
    shutil.rmtree(d)
    if paths.active_slug() == slug:
        rest = [p["slug"] for p in list_profiles()]
        marker = paths.profiles_root() / ".active"
        if rest:
            marker.write_text(rest[0], encoding="utf-8")
        else:
            marker.unlink(missing_ok=True)
    _reset_caches()


def ensure_active(*, create_default: bool = True) -> None:
    """Migrate an old single `profile/` folder, then make sure the active
    profile's folder exists (fresh install → an empty 'default')."""
    if not switching_enabled():
        return
    migrate_legacy()
    if not create_default:
        return
    d = paths.profile_dir()
    if not d.exists():
        d.mkdir(parents=True)
        _write_meta(d, name="My profile", created_at=datetime.now(UTC).isoformat())
        (paths.profiles_root() / ".active").write_text(d.name, encoding="utf-8")


def migrate_legacy() -> str | None:
    """Move the old single `profile/` folder (and root `data/`, `outputs/`)
    into `profiles/<name>/`. Runs once; returns the new slug if it moved."""
    legacy = paths.LEGACY_PROFILE_DIR
    if not switching_enabled() or not legacy.is_dir() or not any(legacy.iterdir()):
        return None
    # Only migrate into the repo's own profiles/ folder — never into a custom
    # JOB_PIPELINE_PROFILES_DIR (that would carry this install's data away).
    if paths.profiles_root().resolve() != (paths.REPO_ROOT / "profiles").resolve():
        return None
    name = display_name(legacy)
    slug = _unique_slug(name if name != legacy.name else paths.DEFAULT_SLUG)
    dest = paths.profiles_root() / slug
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(legacy), str(dest))
    for sub in ("data", "outputs"):
        src = paths.REPO_ROOT / sub
        if src.is_dir() and any(src.iterdir()) and not (dest / sub).exists():
            shutil.move(str(src), str(dest / sub))
    _write_meta(dest, name=name if name != legacy.name else "My profile",
                created_at=datetime.now(UTC).isoformat(), migrated_from="profile/")
    (paths.profiles_root() / ".active").write_text(slug, encoding="utf-8")
    return slug


# --- export ---------------------------------------------------------------


def _load_yaml(p: Path) -> Any:
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


def export_profile(slug: str, *, include_history: bool = True) -> dict[str, Any]:
    """The profile as one JSON-able dict (see module docstring)."""
    from . import tracker_db
    from .config import load_config

    d = paths.profile_dir_for(slug)
    if not d.is_dir():
        raise ProfileError(f"No profile named {slug!r}.")
    config = _load_yaml(d / "config.yaml")
    if isinstance(config, dict):
        config.pop("paths", None)  # machine-specific; defaults apply on import
        (config.get("tracker") or {}).pop("db_path", None)
    bundle: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "name": display_name(d),
        "exported_at": datetime.now(UTC).isoformat(),
        "config": config,
        "resume_content": _load_yaml(d / "resume_content.yaml"),
        "target_companies": _load_yaml(d / "target_companies.yaml"),
    }
    interview = d / ".interview.json"
    if interview.exists():
        bundle["interview"] = json.loads(interview.read_text(encoding="utf-8"))
    if include_history and (d / "config.yaml").exists():
        bundle["tracker"] = tracker_db.export_rows(load_config(d / "config.yaml"))
    return bundle


# --- import ---------------------------------------------------------------


def is_bundle(data: Any) -> bool:
    return isinstance(data, dict) and data.get("format") == FORMAT


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)


def import_bundle(data: dict[str, Any], *, name: str | None = None,
                  activate_it: bool = True) -> dict[str, Any]:
    """Restore an exported profile as a NEW profile (never overwrites)."""
    from . import tracker_db
    from .onboarding.interview import InterviewState
    from .resume.content_loader import ResumeContent

    if int(data.get("version") or 0) > VERSION:
        raise ProfileError("This file was exported by a newer version of the app.")
    config = data.get("config")
    if config is not None:
        try:
            Config(**config)
        except Exception as e:  # noqa: BLE001 - surface validation details
            raise ProfileError(f"The profile's config isn't valid: {e}") from e
    resume = data.get("resume_content")
    if resume is not None:
        try:
            ResumeContent(**resume)
        except Exception as e:  # noqa: BLE001
            raise ProfileError(f"The profile's resume content isn't valid: {e}") from e

    summary = create(name or str(data.get("name") or "Imported profile"), activate_it=False)
    d = paths.profiles_root() / summary["slug"]
    try:
        if config is not None:
            (d / "config.yaml").write_text(_dump(config), encoding="utf-8")
        if resume is not None:
            (d / "resume_content.yaml").write_text(_dump(resume), encoding="utf-8")
        if data.get("target_companies") is not None:
            (d / "target_companies.yaml").write_text(_dump(data["target_companies"]),
                                                     encoding="utf-8")
        if data.get("interview"):
            state = InterviewState.model_validate(data["interview"])
            state.save(d / ".interview.json")
        restored = {"jobs": 0, "contacts": 0}
        if config is not None and data.get("tracker"):
            cfg = anchor_paths(Config(**config), d)
            restored = tracker_db.restore_rows(cfg, data["tracker"])
        _write_meta(d, imported_at=datetime.now(UTC).isoformat())
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    if activate_it:
        activate(summary["slug"])
    out = _summary(d, paths.active_slug())
    out["restored"] = restored
    return out


def smart_import(data: Any, *, llm: Any, model: str, name: str | None = None,
                 activate_it: bool = True) -> dict[str, Any]:
    """Import ANY JSON about a person: Claude maps it onto the setup chat's
    profile draft (and a resume, if the JSON has one), creating a new profile
    whose chat continues from there — asking only what's still missing."""
    from .onboarding.interview import InterviewState, ProfileDraft
    from .resume.content_loader import ResumeContent
    from .resume.parse_resume import ParsedResume, _parsed_to_content_dict

    text = json.dumps(data, indent=2, ensure_ascii=False)[:60000]
    draft = llm.structured(
        "Map this JSON about a job seeker onto the profile fields. Use ONLY facts "
        "present in the JSON; leave anything unknown unset. Never invent details.\n\n"
        f"JSON:\n{text}",
        ProfileDraft, model=model, max_tokens=4000, tool_name="submit_profile",
        tool_description="Submit the profile fields found in the JSON.",
    )
    resume: dict[str, Any] | None = None
    if re.search(r'"(experience|experiences|work|employment|jobs|positions)"', text, re.I):
        parsed = llm.structured(
            "If this JSON contains a person's work history, extract it as a resume — "
            "copy facts and numbers exactly, invent nothing. If it has none, return "
            f"only the name.\n\nJSON:\n{text}",
            ParsedResume, model=model, max_tokens=8000, tool_name="submit_resume",
            tool_description="Submit the resume found in the JSON.",
        )
        if parsed.experiences:
            resume = ResumeContent(**_parsed_to_content_dict(parsed)).model_dump()

    summary = create(name or draft.name or "Imported profile", activate_it=False)
    d = paths.profiles_root() / summary["slug"]
    state = InterviewState(draft=draft, phase="round1",
                           resume_mode="upload" if resume else "build",
                           resume_content=resume)
    state.say(
        "I imported what your file says about you. I'll only ask about what's missing.",
        kind="note",
    )
    state.save(d / ".interview.json")
    _write_meta(d, imported_at=datetime.now(UTC).isoformat(), smart_import=True)
    if activate_it:
        activate(summary["slug"])
    return _summary(d, paths.active_slug())


def load_import_file(path: str | Path) -> Any:
    p = Path(path).expanduser()
    if p.stat().st_size > MAX_IMPORT_BYTES:
        raise ProfileError("That file is over 20 MB.")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProfileError(f"That isn't valid JSON: {e}") from e
