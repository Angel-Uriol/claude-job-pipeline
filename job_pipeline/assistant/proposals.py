"""Jobi's change cards: proposed now, applied only when the user clicks Apply.

Each proposal is checked when it's created: the change must pass the same
models the pipeline uses, and the card shows before → after plus any
warnings. Applying goes through the same writers the forms use: a `.bak`
of the old file, and the comment header kept.

Stored per profile in `<profile>/data/jobi_proposals.json`.
"""

from __future__ import annotations

import json
import secrets as _secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from .. import paths
from ..config import Config, Profile
from ..resume.content_loader import ResumeContent
from ..ui.edit_routes import assign_ids, new_numbers, write_yaml
from . import commands

Kind = Literal[
    "update_profile", "update_resume", "set_companies", "update_search_settings",
    "start_search", "start_premium_search", "clear_chrome_queue", "run_command",
]
Status = Literal["pending", "applied", "dismissed", "failed"]


class Proposal(BaseModel):
    id: str
    kind: Kind
    title: str
    summary: str = ""
    before: Any = None
    after: Any = None
    payload: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    status: Status = "pending"
    result: str = ""
    created_at: str = ""


class ProposalError(ValueError):
    """The change can't be proposed or applied (message is shown to Claude/user)."""


# --- storage ------------------------------------------------------------------


def _file() -> Path:
    return paths.profile_dir() / "data" / "jobi_proposals.json"


def load_all() -> list[Proposal]:
    f = _file()
    if not f.exists():
        return []
    try:
        return [Proposal(**p) for p in json.loads(f.read_text(encoding="utf-8"))]
    except (ValueError, TypeError):
        return []


def _save_all(items: list[Proposal]) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([p.model_dump() for p in items[-100:]], indent=2), encoding="utf-8")


def get(pid: str) -> Proposal:
    for p in load_all():
        if p.id == pid:
            return p
    raise ProposalError(f"No change card {pid!r}.")


def _store(p: Proposal) -> Proposal:
    p.id = p.id or _secrets.token_hex(4)
    p.created_at = datetime.now(UTC).isoformat()
    items = load_all()
    items.append(p)
    _save_all(items)
    return p


def _update(p: Proposal) -> None:
    _save_all([p if x.id == p.id else x for x in load_all()])


def clear() -> None:
    """Forget pending cards (a new chat)."""
    _save_all([p for p in load_all() if p.status != "pending"])


# --- file helpers ---------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return data if isinstance(data, dict) else {}


def _config_raw(config_path: Path) -> dict[str, Any]:
    return _read_yaml(config_path)


def _resume_path(config_path: Path) -> Path:
    return config_path.parent / "resume_content.yaml"


def _targets_path(config_path: Path) -> Path:
    return config_path.parent / "target_companies.yaml"


def _validation(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                     for err in e.errors()[:4])


# --- proposing --------------------------------------------------------------------

PROFILE_FIELDS = set(Profile.model_fields)


def propose_profile(changes: dict[str, Any], reason: str, config_path: Path) -> Proposal:
    bad = sorted(set(changes) - PROFILE_FIELDS)
    if bad:
        raise ProposalError(f"Unknown profile fields: {', '.join(bad)}. "
                            f"Allowed: {', '.join(sorted(PROFILE_FIELDS))}.")
    current = (_config_raw(config_path).get("profile") or {})
    merged = {**current, **changes}
    try:
        Profile(**merged)
    except ValidationError as e:
        raise ProposalError(f"That change isn't valid: {_validation(e)}") from e
    return _store(Proposal(
        id="", kind="update_profile", title="Update your profile", summary=reason,
        before={k: current.get(k) for k in changes}, after=changes,
        payload={"changes": changes},
    ))


class ResumeEdit(BaseModel):
    """One resume operation."""

    op: Literal["set_summary", "replace_bullet", "add_bullet", "remove_bullet", "set_skills"]
    key: str = Field("", description="set_summary: summary key (e.g. 'default'). add_bullet: "
                     "experience or project key. set_skills: skill group (languages, "
                     "data_platform, cloud, ml, frontend, other).")
    bullet_id: str = Field("", description="replace_bullet / remove_bullet: the bullet id")
    text: str = Field("", description="New text (summary or bullet)")
    items: list[str] = Field(default_factory=list, description="set_skills: the full list")


def _apply_resume_edits(raw: dict[str, Any], edits: list[ResumeEdit]) -> dict[str, Any]:
    data = json.loads(json.dumps(raw))  # deep copy
    groups = (data.get("experiences") or {}, data.get("projects") or {})

    def find_bullet(bid: str) -> tuple[list[dict[str, Any]], int]:
        for group in groups:
            for item in group.values():
                bullets = item.get("bullets") or []
                for i, b in enumerate(bullets):
                    if str(b.get("id")) == bid:
                        return bullets, i
        raise ProposalError(f"No bullet with id {bid!r}.")

    for e in edits:
        if e.op == "set_summary":
            data.setdefault("summaries", {})[e.key or "default"] = e.text
        elif e.op == "replace_bullet":
            bullets, i = find_bullet(e.bullet_id)
            bullets[i]["text"] = e.text
        elif e.op == "remove_bullet":
            bullets, i = find_bullet(e.bullet_id)
            bullets.pop(i)
        elif e.op == "add_bullet":
            target = next((g[e.key] for g in groups if e.key in g), None)
            if target is None:
                raise ProposalError(f"No experience or project with key {e.key!r}.")
            target.setdefault("bullets", []).append({"id": "", "text": e.text})
        elif e.op == "set_skills":
            data.setdefault("skills", {})[e.key] = e.items
    return assign_ids(data)


def propose_resume(edits: list[ResumeEdit], reason: str, config_path: Path) -> Proposal:
    path = _resume_path(config_path)
    if not path.exists():
        raise ProposalError("There's no resume yet — finish Setup first.")
    raw = _read_yaml(path)
    new_raw = _apply_resume_edits(raw, edits)
    try:
        old, new = ResumeContent(**raw), ResumeContent(**new_raw)
    except ValidationError as e:
        raise ProposalError(f"That resume change isn't valid: {_validation(e)}") from e
    warnings = [f'"{n}" is new on your resume — only apply this if it\'s accurate.'
                for n in new_numbers(old, new)]
    return _store(Proposal(
        id="", kind="update_resume", title="Edit your resume", summary=reason,
        before=_resume_view(raw, edits), after=_resume_view(new_raw, edits),
        payload={"edits": [e.model_dump() for e in edits]}, warnings=warnings,
    ))


def _resume_view(raw: dict[str, Any], edits: list[ResumeEdit]) -> dict[str, Any]:
    """Only the parts the edits touch, for the before → after card."""
    out: dict[str, Any] = {}
    for e in edits:
        if e.op == "set_summary":
            out[f"summary ({e.key or 'default'})"] = (raw.get("summaries") or {}).get(
                e.key or "default", "")
        elif e.op == "set_skills":
            out[f"skills: {e.key}"] = (raw.get("skills") or {}).get(e.key, [])
        else:
            key = e.key
            for group in ("experiences", "projects"):
                for k, item in (raw.get(group) or {}).items():
                    ids = [str(b.get("id")) for b in item.get("bullets") or []]
                    if k == key or e.bullet_id in ids:
                        out[f"{item.get('company') or item.get('title') or k} bullets"] = [
                            b.get("text") for b in item.get("bullets") or []]
    return out


def propose_companies(add: list[dict[str, Any]], remove: list[str], reason: str,
                      config_path: Path) -> Proposal:
    """`add` items: {name, careers_url?, tier?}. Boards are looked up now."""
    from ..onboarding.companies import verify_companies

    add = [a for a in add if str(a.get("name", "")).strip()]
    if not add and not remove:
        raise ProposalError("Nothing to add or remove.")
    found = verify_companies([{"name": a["name"].strip()} for a in add]) if add else []
    rows, warnings = [], []
    for a, f in zip(add, found, strict=True):
        row = {"name": a["name"].strip(), "tier": int(a.get("tier") or 2),
               "platform": f.get("platform"), "slug": f.get("slug"),
               "careers_url": a.get("careers_url") or ""}
        if not row["platform"] and not row["careers_url"]:
            warnings.append(f"Couldn't find a public job board for {row['name']}. It'll be "
                            "added as a target company only (premium Chrome searches can "
                            "still cover it).")
        rows.append(row)
    current = _company_names(config_path)
    gone = {r.lower() for r in remove}
    return _store(Proposal(
        id="", kind="set_companies", title="Change your companies", summary=reason,
        before=current,
        after=sorted(n for n in {*current, *(r["name"] for r in rows)} if n.lower() not in gone),
        payload={"add": rows, "remove": remove}, warnings=warnings,
    ))


def _company_names(config_path: Path) -> list[str]:
    raw = _config_raw(config_path)
    names = {str(c.get("name")) for k in ("greenhouse", "lever", "ashby", "no_api")
             for c in (raw.get("scrapers") or {}).get(k) or []}
    names |= {str(c.get("name")) for c in _read_yaml(_targets_path(config_path))
              .get("companies") or []}
    return sorted(n for n in names if n and n != "None")


SETTINGS = {
    "filters.max_age_days": "Only keep jobs posted within N days",
    "filters.location_tokens": "Location words a job must contain (e.g. 'San Francisco')",
    "filters.allow_remote": "Allow remote jobs",
    "filters.location_filter_enabled": "Filter by location at all",
    "scoring.max_jobs_per_run": "Max new jobs Claude scores per search (cost cap)",
    "excluded_companies": "Companies to never show",
    "scrapers.jsearch": "Open-web searches: list of {query, date_posted, num_pages, remote_only}",
}


def _set_path(raw: dict[str, Any], dotted: str, value: Any) -> None:
    node = raw
    *head, last = dotted.split(".")
    for part in head:
        node = node.setdefault(part, {})
    node[last] = value


def _get_path(raw: dict[str, Any], dotted: str) -> Any:
    node: Any = raw
    for part in dotted.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def propose_settings(changes: dict[str, Any], reason: str, config_path: Path) -> Proposal:
    bad = sorted(set(changes) - set(SETTINGS))
    if bad:
        raise ProposalError(f"Can't change {', '.join(bad)}. Allowed: {', '.join(SETTINGS)}.")
    raw = _config_raw(config_path)
    trial = json.loads(json.dumps(raw))
    for k, v in changes.items():
        _set_path(trial, k, v)
    try:
        Config(**trial)
    except ValidationError as e:
        raise ProposalError(f"That setting isn't valid: {_validation(e)}") from e
    return _store(Proposal(
        id="", kind="update_search_settings", title="Change search settings", summary=reason,
        before={k: _get_path(raw, k) for k in changes}, after=changes,
        payload={"changes": changes},
    ))


def propose_action(kind: Kind, title: str, reason: str, payload: dict[str, Any] | None = None,
                   ) -> Proposal:
    payload = payload or {}
    warnings: list[str] = []
    if kind == "run_command":
        cmd = commands.COMMANDS.get(str(payload.get("command")))
        if cmd is None:
            raise ProposalError(f"Not an allowed command. Allowed: {', '.join(commands.COMMANDS)}.")
        title = cmd.label
        payload = {"command": cmd.name, "shown": cmd.shown}
    if kind == "start_premium_search":
        names = [str(c).strip() for c in payload.get("companies") or [] if str(c).strip()]
        if not names:
            raise ProposalError("Name at least one company for the Chrome search.")
        payload = {"companies": names[:10]}
        warnings.append("Needs the Claude in Chrome extension connected to Claude Code. "
                        "Uses your Claude plan/credits while it browses.")
    if kind == "start_search":
        warnings.append("Scores new jobs with Claude (capped by 'max jobs per run').")
    return _store(Proposal(id="", kind=kind, title=title, summary=reason, payload=payload,
                           warnings=warnings))


# --- applying ---------------------------------------------------------------------------


def apply(pid: str, config_path: Path) -> dict[str, Any]:
    """Apply one pending card. Returns {message, client_action?, log?}."""
    p = get(pid)
    if p.status != "pending":
        raise ProposalError(f"This card was already {p.status}.")
    try:
        out = _apply(p, config_path)
    except (ProposalError, ValidationError, OSError) as e:
        p.status, p.result = "failed", str(e)
        _update(p)
        raise ProposalError(str(e)) from e
    # A command that ran but reported a problem (e.g. Chrome still not
    # connected) shows as "didn't work" rather than a green "applied".
    p.status = "failed" if out.get("ok") is False else "applied"
    p.result = str(out.get("message", ""))
    _update(p)
    return out


def dismiss(pid: str) -> None:
    p = get(pid)
    if p.status == "pending":
        p.status = "dismissed"
        _update(p)


def _apply(p: Proposal, config_path: Path) -> dict[str, Any]:
    if p.kind == "update_profile":
        raw = _config_raw(config_path)
        raw["profile"] = Profile(**{**(raw.get("profile") or {}),
                                    **p.payload["changes"]}).model_dump()
        write_yaml(config_path, raw)
        return {"message": "Profile saved.", "changed": "profile"}

    if p.kind == "update_resume":
        path = _resume_path(config_path)
        edits = [ResumeEdit(**e) for e in p.payload["edits"]]
        new = ResumeContent(**_apply_resume_edits(_read_yaml(path), edits))
        write_yaml(path, new.model_dump())
        return {"message": "Resume saved.", "changed": "resume"}

    if p.kind == "set_companies":
        _apply_companies(p.payload, config_path)
        return {"message": "Companies updated.", "changed": "companies"}

    if p.kind == "update_search_settings":
        raw = _config_raw(config_path)
        for k, v in p.payload["changes"].items():
            _set_path(raw, k, v)
        Config(**raw)
        write_yaml(config_path, raw)
        return {"message": "Search settings saved.", "changed": "settings"}

    if p.kind == "start_search":
        return {"message": "Starting a search for new jobs…", "client_action": {"run": "daily"}}

    if p.kind == "start_premium_search":
        from ..chrome_queue import ChromeQueue

        profile = None if paths.pinned_profile_dir() else paths.active_slug()
        rec = ChromeQueue().enqueue(
            scope="mix", ad_hoc_companies=p.payload["companies"], score_after=True,
            extra_notes="Premium search requested through Jobi.", profile=profile,
            premium=True,
        )
        return {"message": f"Queued a Chrome search (run {rec['id']}). Starting it…",
                "client_action": {"run": "premium"}}

    if p.kind == "clear_chrome_queue":
        from ..chrome_queue import ChromeQueue

        q = ChromeQueue()
        profile = None if paths.pinned_profile_dir() else paths.active_slug()
        stuck = [r for r in q.list_status() if r.get("status") in ("pending", "running")
                 and r.get("profile") == profile]
        for r in stuck:
            q.fail(r["id"], error="Cleared by Jobi.")
        return {"message": f"Cleared {len(stuck)} waiting Chrome search(es)."}

    if p.kind == "run_command":
        r = commands.run_command(str(p.payload.get("command")))
        status = "finished" if r["ok"] else "failed"
        return {"message": f"{p.title}: {status}.", "ok": r["ok"], "log": r["log"]}

    raise ProposalError(f"Unknown change type {p.kind!r}.")


def _apply_companies(payload: dict[str, Any], config_path: Path) -> None:
    raw = _config_raw(config_path)
    scrapers = raw.setdefault("scrapers", {})
    remove = {str(r).lower() for r in payload.get("remove") or []}
    for key in ("greenhouse", "lever", "ashby", "no_api"):
        scrapers[key] = [c for c in scrapers.get(key) or []
                         if str(c.get("name", "")).lower() not in remove]
    targets_path = _targets_path(config_path)
    targets = _read_yaml(targets_path)
    tlist = [c for c in targets.get("companies") or []
             if str(c.get("name", "")).lower() not in remove]
    have = {str(c.get("name", "")).lower() for c in tlist}
    for row in payload.get("add") or []:
        name = row["name"]
        plat = row.get("platform")
        existing = {str(c.get("name", "")).lower() for k in ("greenhouse", "lever", "ashby",
                                                             "no_api")
                    for c in scrapers.get(k) or []}
        if name.lower() not in existing:
            if plat in ("greenhouse", "lever", "ashby") and row.get("slug"):
                scrapers.setdefault(plat, []).append({"name": name, "slug": row["slug"]})
            elif row.get("careers_url"):
                scrapers.setdefault("no_api", []).append(
                    {"name": name, "careers_url": row["careers_url"], "platform": "custom"})
        if name.lower() not in have:
            tlist.append({"name": name, "tier": int(row.get("tier") or 2), "aliases": []})
    Config(**raw)
    write_yaml(config_path, raw)
    targets["companies"] = tlist
    targets.setdefault("boost_by_tier", {1: 0.7, 2: 0.5, 3: 0.3})
    write_yaml(targets_path, targets)
