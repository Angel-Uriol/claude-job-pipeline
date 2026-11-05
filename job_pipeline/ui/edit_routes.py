"""Edit your profile and resume in the app — no YAML.

GET/PUT /api/profile   the `profile:` block of config.yaml
GET/PUT /api/resume    resume_content.yaml

Both validate with the same models the pipeline uses, keep a `.bak` of the
previous file, and keep the explanatory comment block at the top of the file.
Resume saves keep bullet ids stable (the tailorer merges by id) and give new
bullets the next free id. If a save adds a number that wasn't on the resume
before, it's returned as a warning first — an honesty nudge, not a block.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from .. import paths
from ..config import Profile
from ..resume.content_loader import ResumeContent

_NUMBER = re.compile(r"\d[\d,.]*%?\+?")


class ProfileBody(BaseModel):
    profile: dict[str, Any]


class ResumeBody(BaseModel):
    resume: dict[str, Any]
    confirm: bool = False  # save even if there are new-number warnings


def _comment_header(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            lines.append(line)
        else:
            break
    return "\n".join(lines).rstrip() + "\n\n" if lines else ""


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old:
        path.with_suffix(path.suffix + ".bak").write_text(old, encoding="utf-8")
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(_comment_header(old) + body, encoding="utf-8")


#: Public names for other modules (Jobi) that save through the same writer.
write_yaml = _write_yaml


def _errors(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:5]:
        where = " → ".join(str(x) for x in err["loc"])
        parts.append(f"{where}: {err['msg']}")
    return "Please fix: " + "; ".join(parts)


def _slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def assign_ids(resume: dict[str, Any]) -> dict[str, Any]:
    """Keep existing bullet ids; give new/duplicate ones the next free bN / pN."""
    for group, prefix in (("experiences", "b"), ("projects", "p")):
        items = resume.get(group) or {}
        used: set[str] = set()
        top = 0
        for item in items.values():
            for b in item.get("bullets") or []:
                m = re.fullmatch(rf"{prefix}(\d+)", str(b.get("id") or ""))
                if m:
                    top = max(top, int(m.group(1)))
        for item in items.values():
            for b in item.get("bullets") or []:
                bid = str(b.get("id") or "")
                if not bid or bid in used:
                    top += 1
                    bid = f"{prefix}{top}"
                    b["id"] = bid
                used.add(bid)
    return resume


def normalize_keys(resume: dict[str, Any]) -> dict[str, Any]:
    """The editor sends lists ([{key?, ...}]); the file stores dicts by key."""
    for group, name_field in (("experiences", "company"), ("projects", "title")):
        value = resume.get(group)
        if isinstance(value, list):
            out: dict[str, Any] = {}
            for item in value:
                item = dict(item)
                key = str(item.pop("key", "") or "")
                key = key or _slug(str(item.get(name_field, "")), set(out))
                if key in out:
                    key = _slug(key, set(out))
                out[key] = item
            resume[group] = out
    return resume


def new_numbers(old: ResumeContent | None, new: ResumeContent) -> list[str]:
    def numbers(rc: ResumeContent) -> set[str]:
        text = " ".join([*rc.summaries.values(),
                         *(b.text for e in rc.experiences.values() for b in e.bullets),
                         *(b.text for p in rc.projects.values() for b in p.bullets)])
        return {re.sub(r"[^\d]", "", n) for n in _NUMBER.findall(text)}

    before = numbers(old) if old else set()
    found: list[str] = []
    text = " ".join([*new.summaries.values(),
                     *(b.text for e in new.experiences.values() for b in e.bullets),
                     *(b.text for p in new.projects.values() for b in p.bullets)])
    for n in _NUMBER.findall(text):
        digits = re.sub(r"[^\d]", "", n)
        if len(digits) >= 2 and digits not in before and n not in found:
            found.append(n)
    return found[:8]


def register_edit_routes(app: FastAPI) -> None:
    def config_file() -> Path:
        return Path(app.state.config_path or paths.config_path()).resolve()

    def not_running() -> None:
        if app.state.pipeline_state.running is not None:
            raise HTTPException(409, "A search is running — save again when it finishes.")

    @app.get("/api/profile")
    async def get_profile() -> dict[str, Any]:
        raw = yaml.safe_load(config_file().read_text(encoding="utf-8")) or {}
        return {"profile": Profile(**(raw.get("profile") or {})).model_dump()}

    @app.put("/api/profile")
    async def put_profile(body: ProfileBody) -> dict[str, Any]:
        not_running()
        try:
            profile = Profile(**body.profile)
        except ValidationError as e:
            raise HTTPException(400, _errors(e)) from e
        path = config_file()
        async with app.state.lock:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw["profile"] = profile.model_dump()
            _write_yaml(path, raw)
        return {"profile": profile.model_dump(), "saved": True}

    @app.get("/api/resume")
    async def get_resume() -> dict[str, Any]:
        path = config_file().parent / "resume_content.yaml"
        if not path.exists():
            raise HTTPException(404, "No resume yet — finish Setup first.")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rc = ResumeContent(**raw)
        data = rc.model_dump()
        # Lists are easier to edit; keep each item's key so ids/keys stay stable.
        data["experiences"] = [{"key": k, **v} for k, v in data["experiences"].items()]
        data["projects"] = [{"key": k, **v} for k, v in data["projects"].items()]
        return {"resume": data}

    @app.put("/api/resume")
    async def put_resume(body: ResumeBody) -> dict[str, Any]:
        not_running()
        path = config_file().parent / "resume_content.yaml"
        data = assign_ids(normalize_keys(dict(body.resume)))
        try:
            new = ResumeContent(**data)
        except ValidationError as e:
            raise HTTPException(400, _errors(e)) from e
        old: ResumeContent | None = None
        if path.exists():
            try:
                old = ResumeContent(**(yaml.safe_load(path.read_text(encoding="utf-8")) or {}))
            except (ValidationError, TypeError, yaml.YAMLError):
                old = None
        warnings = new_numbers(old, new)
        if warnings and not body.confirm:
            return {"saved": False, "warnings": [
                f'"{n}" is new on your resume — make sure it\'s accurate.' for n in warnings]}
        async with app.state.lock:
            _write_yaml(path, new.model_dump())
        return {"saved": True, "warnings": []}
