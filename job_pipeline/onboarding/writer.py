"""Turn a finished questionnaire into the three profile files.

    profile/config.yaml            pipeline config (profile + scrapers + filters)
    profile/resume_content.yaml    parsed resume (source of every generated resume)
    profile/target_companies.yaml  target list with tiers (score boost)

`build_files` renders all three as YAML text first, so the Setup page can
show them for review; `write_files` validates and writes them. Pipeline
defaults (models, thresholds, paths...) come from examples/profile/config.yaml
so a new user starts from the same, documented baseline.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .. import paths
from ..config import Config
from ..resume.content_loader import ResumeContent
from .interview import InterviewState, ProfileDraft, draft_to_profile_dict

CONFIG_HEADER = """\
# Pipeline config — written by the setup questionnaire. Edit freely.
# `profile:` is who you are and what you want; everything else is pipeline
# mechanics. Validated at startup, so a typo fails loudly.
# Secrets and the Claude auth mode live in .env, not here.
"""

TARGETS_HEADER = """\
# Target companies — written by the setup questionnaire. Edit freely.
# Matching jobs get a TARGET badge and a tier-weighted fit-score bump.
# Matching ignores case/punctuation but is exact ("box" never matches
# "dropbox"); add `aliases` for alternate names.
"""

RESUME_HEADER = """\
# Resume content — parsed from your uploaded resume by the setup wizard.
# Every generated resume is built from this file. Fix anything the parser
# got wrong, reorder bullets, drop ones you don't want.
# Never inflate metrics — honest reframing only.
"""

MAX_JSEARCH_QUERIES = 6


def _example_config() -> dict[str, Any]:
    raw = yaml.safe_load((paths.EXAMPLES_DIR / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _jsearch_queries(draft: ProfileDraft) -> list[dict[str, Any]]:
    """A few open-web searches from the Tier A/B functions + location."""
    functions = [f.name for f in (draft.target_functions or []) if f.tier in ("A", "B")]
    place = (draft.location or "").strip()
    queries: list[dict[str, Any]] = []
    for fn in functions:
        if place:
            queries.append({"query": f"{fn} in {place}", "date_posted": "week", "num_pages": 1})
        if draft.allow_remote:
            queries.append({"query": f"{fn} remote", "date_posted": "week", "num_pages": 1,
                            "remote_only": True})
    return queries[:MAX_JSEARCH_QUERIES]


def build_config(draft: ProfileDraft, companies: list[dict[str, Any]]) -> dict[str, Any]:
    """Config mapping from the example baseline + questionnaire answers."""
    cfg = copy.deepcopy(_example_config())
    cfg["profile"] = draft_to_profile_dict(draft)

    selected = [c for c in companies if c.get("selected", True)]
    scrapers: dict[str, Any] = {"greenhouse": [], "lever": [], "ashby": [],
                                "remoteok": [], "jsearch": [], "no_api": []}
    for c in selected:
        name = str(c["name"]).strip().lower()
        if c.get("platform") in ("greenhouse", "lever", "ashby") and c.get("slug"):
            scrapers[c["platform"]].append({"name": name, "slug": c["slug"]})
        elif c.get("careers_url"):
            scrapers["no_api"].append(
                {"name": name, "careers_url": c["careers_url"], "platform": "custom"}
            )
    if draft.allow_remote:
        scrapers["remoteok"] = [{"name": "remoteok"}]
    scrapers["jsearch"] = _jsearch_queries(draft)
    cfg["scrapers"] = scrapers

    cfg["excluded_companies"] = [c.lower() for c in (draft.excluded_companies or [])]
    cfg["dead_companies"] = []
    filters = cfg.setdefault("filters", {})
    filters["location_tokens"] = [t.lower() for t in (draft.location_tokens or [])]
    filters["allow_remote"] = bool(draft.allow_remote)
    # No location words at all → don't silently drop every job.
    filters["location_filter_enabled"] = bool(filters["location_tokens"]) or not draft.allow_remote
    cfg.setdefault("chrome_claude", {})["exclude_companies"] = []
    return cfg


def build_targets(companies: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        {"name": str(c["name"]).strip().lower(), "tier": int(c.get("tier", 2)),
         "aliases": list(c.get("aliases") or [])}
        for c in companies if c.get("selected", True)
    ]
    return {"boost_by_tier": {1: 0.7, 2: 0.5, 3: 0.3}, "companies": rows}


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)


def skeleton_resume(draft: ProfileDraft) -> dict[str, Any]:
    """A minimal, valid resume_content when no resume was uploaded: header +
    summary + skills from the questionnaire, empty experience to fill in."""
    return {
        "header": {
            "name": draft.name or "", "email": draft.email or "", "phone": draft.phone or "",
            "linkedin": draft.linkedin or "", "location": draft.location or "",
        },
        "summaries": {"software_engineer": draft.headline or ""},
        "experiences": {},
        "projects": {},
        "education": [],
        "skills": {"languages": [], "data_platform": [], "cloud": [], "ml": [],
                   "frontend": [], "other": list(draft.preferred_skills or [])},
    }


def build_files(state: InterviewState) -> dict[str, str]:
    """Render the profile files as YAML text (for review). Validates both."""
    cfg = build_config(state.draft, state.companies)
    Config(**cfg)  # raises pydantic.ValidationError on anything malformed
    resume = state.resume_content or skeleton_resume(state.draft)
    ResumeContent(**resume)
    return {
        "config.yaml": CONFIG_HEADER + "\n" + _dump(cfg),
        "resume_content.yaml": RESUME_HEADER + "\n" + _dump(resume),
        "target_companies.yaml": TARGETS_HEADER + "\n" + _dump(build_targets(state.companies)),
    }


def write_files(state: InterviewState, out_dir: Path | None = None) -> list[Path]:
    """Validate and write the profile files. Existing files are backed up
    to `<name>.bak` first. Returns the written paths."""
    target = out_dir or paths.profile_dir()
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, text in build_files(state).items():
        p = target / name
        if p.exists():
            p.with_suffix(p.suffix + ".bak").write_text(p.read_text(encoding="utf-8"),
                                                         encoding="utf-8")
        p.write_text(text, encoding="utf-8")
        written.append(p)
    from ..target_companies import default_target_index

    default_target_index.cache_clear()
    return written
