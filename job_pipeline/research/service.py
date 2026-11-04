"""Profile-local research orchestration; no SDKs and no changes to job scores."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel, Field

from .. import tracker_db
from ..config import Config
from ..llm import ClaudeClient, LLMError
from ..llm.research import WebEvidence, safe_url, web_research
from ..llm.usage import tagged
from ..paths import PROMPTS_DIR
from ..prompts import candidate_block
from ..resume.content_loader import load_resume_content
from ..store import JobStore
from .models import Comparison, InterviewPlan

Kind = Literal["comparison", "interview"]
APPLIED_STAGES = {"applied", "recruiter_screen", "phone_screen", "take_home", "onsite", "offer"}
# Discover statuses still open for comparison (resume_only = you generated a
# tailored resume but haven't applied yet — exactly when comparing matters).
COMPARABLE_STATUSES = {"new", "pending", "resume_only"}
QUOTE_MIN_CHARS = 12
# Search listings beyond this many add prompt size, not evidence.
MAX_LISTINGS_PER_JOB = 12


def _employer_page(url: str, company: str) -> bool:
    """True for the employer's own site (e.g. anthropic.com/careers for "Anthropic")."""
    from urllib.parse import urlsplit

    from ..target_companies import _normalize

    name = _normalize(company)
    labels = [_normalize(lbl) for lbl in (urlsplit(url).hostname or "").split(".")]
    return bool(name) and any(lbl in (name, f"{name}careers", f"{name}jobs") for lbl in labels)


def _same_page(a: str, b: str) -> bool:
    """Same URL ignoring scheme, "www.", query, fragment and trailing slash."""
    def key(u: str) -> str:
        from urllib.parse import urlsplit

        p = urlsplit(u.strip())
        host = (p.hostname or "").removeprefix("www.")
        return f"{host}{p.path.rstrip('/')}".lower()
    return bool(a and b) and key(a) == key(b)


class ResearchRequest(BaseModel):
    kind: Kind
    job_keys: list[str] = Field(min_length=1, max_length=5)
    stage: str = Field(default="General interview", max_length=100)
    prep_days: int = Field(default=7, ge=1, le=60)


def job_key(company: str, identity: str) -> str:
    # Existing UI keys can collide across employers; these keys cannot.
    return hashlib.sha256(json.dumps([company.lower(), identity]).encode()).hexdigest()[:24]


def candidates(cfg: Config, kind: Kind) -> list[dict[str, Any]]:
    if kind == "interview":
        return [dict(job_key=j["id"], company=j["company"], title=j["role"],
                     location=j.get("location") or "", url=j.get("url") or "",
                     description=j.get("full_jd_text") or "", fit_score=j.get("fit_score"),
                     status=j["status"], notes=j.get("notes") or "")
                for j in tracker_db.list_jobs(cfg, hidden="all")
                if j["status"] in APPLIED_STAGES or j.get("applied_at")]
    store = JobStore(cfg.paths.jobs_json)
    store.load()
    jobs = sorted((j for j in store.jobs if j.fit_verdict in {"apply", "consider"}
                   and j.status in COMPARABLE_STATUSES),
                  key=lambda j: j.fit_score or 0, reverse=True)
    return [dict(job_key=job_key(*j.dedup_key()), company=j.company, title=j.title,
                 location=j.location, url=j.url, description=j.description,
                 fit_score=j.fit_score, extracted=j.extracted, gaps=j.honest_bridge_notes)
            for j in jobs]


def candidate_evidence(cfg: Config, profile_dir: Path) -> dict[str, str]:
    evidence = {f"highlight-{i+1}": value for i, value in enumerate(cfg.profile.highlights)}
    resume_path = profile_dir / "resume_content.yaml"
    if resume_path.exists():
        resume = load_resume_content(resume_path)
        for group, items in (("experience", resume.experiences), ("project", resume.projects)):
            for key, item in items.items():
                for bullet in item.bullets:
                    evidence[f"{group}/{key}/{bullet.id}"] = f"{item.title}: {bullet.text}"
    return evidence


def report_dir(profile_dir: Path) -> Path:
    return profile_dir / "data" / "research"


def save_report(profile_dir: Path, record: dict[str, Any]) -> None:
    folder = report_dir(profile_dir)
    folder.mkdir(parents=True, exist_ok=True)
    # IDs are generated here, never paths supplied by a request or import.
    ident = str(uuid.UUID(record["id"]))
    temp = folder / f"{ident}.tmp"
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(folder / f"{ident}.json")


def list_reports(profile_dir: Path) -> list[dict[str, Any]]:
    reports = []
    for p in report_dir(profile_dir).glob("*.json"):
        try:
            reports.append(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue  # one damaged report must not hide the others
    return sorted(reports, key=lambda r: str(r.get("created_at") or ""), reverse=True)


@tagged("research")
def build_report(
    cfg: Config, profile_dir: Path, request: ResearchRequest,
    jobs: list[dict[str, Any]], client: ClaudeClient,
    *, progress: Callable[[str], None],
    researcher: Callable[..., WebEvidence] = web_research,
) -> dict[str, Any]:
    today = datetime.now(UTC).isoformat()
    evidence = candidate_evidence(cfg, profile_dir)
    sources: list[dict[str, str]] = []
    notes: list[str] = []
    warnings: list[str] = []
    for i, job in enumerate(jobs):
        if safe_url(job["url"]):
            sources.append(dict(id=f"posting-{i+1}", url=job["url"],
                                title=f"{job['company']} — {job['title']}",
                                accessed_at=today, kind="saved posting", published="",
                                excerpt=job["description"][:4000]))
        progress(f"Researching {job['company']} ({i+1}/{len(jobs)})")
        # Deliberately exclude candidate, application notes and full JD from search.
        targets = json.dumps({k: job[k] for k in ("company", "title", "location", "url")})
        prompt = (PROMPTS_DIR / "research_web.txt").read_text(encoding="utf-8")
        prompt = prompt.replace("{today}", today).replace("{targets}", targets).replace(
            "{focus}", "Salary, benefits and company reviews versus comparable market roles."
            if request.kind == "comparison" else
            "Company, role, hiring process, interview questions and preparation resources."
        )
        try:
            web = researcher(client, prompt, model=cfg.llm.smart_model)
        except LLMError as e:  # user-facing, credential-free message
            web = WebEvidence(warning=f"Live research failed ({e}); only saved evidence is used.")
        except Exception:  # noqa: BLE001 - bounded fallback, never pass credentials in errors
            web = WebEvidence(warning="Live research failed; only saved evidence is available.")
        pages = [s for s in web.sources if s.kind == "page"
                 and not any(_same_page(s.url, j["url"]) for j in jobs)
                 and not _employer_page(s.url, job["company"])]
        if not web.sources:
            warnings.append(f"{job['company']}: no live sources retrieved.")
        elif not pages:
            warnings.append(f"{job['company']}: no market page could be read (only search "
                            "listings or the posting itself), so market ratings stay unknown.")
        if web.notes:
            notes.append(f"{job['company']}: {web.notes}")
        if web.warning:
            warnings.append(f"{job['company']}: {web.warning}")
        posting_urls = [j["url"] for j in jobs]
        listings = 0
        for s in web.sources:
            if not safe_url(s.url):
                continue
            if any(_same_page(s.url, u) for u in posting_urls):
                # A live copy of the job ad is still the job ad — never a market benchmark.
                kind = "job posting"
            elif s.kind == "page" and _employer_page(s.url, job["company"]):
                # The employer describing itself: good for facts, not a comparison.
                kind = "employer page"
            elif s.kind == "page":
                kind = "web page"
            else:
                listings += 1
                if listings > MAX_LISTINGS_PER_JOB:
                    continue
                kind = "search listing"
            sources.append(dict(id=f"web-{len(sources)+1}", url=s.url, title=s.title,
                                excerpt=s.excerpt, accessed_at=today, kind=kind,
                                published=s.published))
    progress("Building a grounded comparison" if request.kind == "comparison"
             else "Building your interview plan")
    template = Environment(undefined=StrictUndefined, autoescape=False).from_string(
        (PROMPTS_DIR / "research_synthesis.txt").read_text(encoding="utf-8")
    )
    prompt = template.render(
        task=request.kind, candidate=candidate_block(cfg.profile.model_copy(
            update={"name": "Candidate"})), evidence=json.dumps(evidence),
        jobs=json.dumps(jobs), research=json.dumps({"notes": notes, "warnings": warnings}),
        sources=json.dumps(sources), details=request.model_dump_json(),
    )
    schema = Comparison if request.kind == "comparison" else InterviewPlan
    result = client.structured(prompt, schema, model=cfg.llm.smart_model, max_tokens=10000)
    data = result.model_dump(mode="json")
    validate_references(data, {s["id"] for s in sources}, set(evidence))
    by_id = {src["id"]: src for src in sources}
    if isinstance(result, Comparison):
        expected = {j["job_key"] for j in jobs}
        if len(result.rows) != len(expected) or {r.job_key for r in result.rows} != expected:
            raise LLMError("Comparison did not cover the selected jobs. Please retry.")
        for row in result.rows:
            for finding in (row.salary, row.benefits, row.company_reviews):
                problem = finding_problem(finding, by_id)
                if problem:
                    finding.explanation = f"Not verified — {problem}. {finding.explanation}"
                    finding.rating, finding.confidence = "unknown", "low"
    elif isinstance(result, InterviewPlan):
        for question in result.questions:
            if question.kind == "reported" and not any(
                by_id[i]["kind"] in ("web page", "employer page") for i in question.source_ids
            ):
                # A search listing proves a page exists, not that this was asked.
                question.kind = "practice"
                question.purpose = ("(Shown as practice: the report it came from couldn't be "
                                    "read.) " + question.purpose)
            unbacked = unbacked_figures(question.answer_outline, question.evidence_ids,
                                        evidence, cfg)
            if unbacked:
                question.verify_note = (
                    "Check before using: " + ", ".join(unbacked) + " doesn't appear in your "
                    "resume or profile. Only say it if it's true; otherwise drop it."
                )
    data = result.model_dump(mode="json")
    return {"content": data, "sources": sources, "evidence": evidence, "warnings": warnings}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[“”\"'‘’`]", "", text)).strip().lower()


def finding_problem(finding: Any, by_id: dict[str, dict[str, str]]) -> str:
    """Why a market rating can't stand ("" if it can).

    Citation membership alone doesn't prove the model read the evidence right,
    so a rating needs: a cohort, at least one cited live PAGE (not a search
    listing, not the saved posting), and an exact quote that is really in one
    of the cited sources' stored text.
    """
    if finding.rating == "unknown":
        return ""
    cited = [by_id[i] for i in finding.source_ids if i in by_id]
    market = [c for c in cited if c["kind"] == "web page"]
    if not market:
        return ("no independent market page was cited (the posting, the employer's own "
                "site or a search listing isn't a benchmark)")
    if not finding.cohort.strip():
        return "the comparison cohort wasn't stated"
    quote = _norm(finding.evidence_quote)
    if len(quote) < QUOTE_MIN_CHARS:
        return "no supporting quote was given"
    # The quote may state the employer's fact (from its own page) or the market
    # figure — but it must really be in a cited page, never the posting.
    readable = [c for c in cited if c["kind"] in ("web page", "employer page")]
    if not any(quote in _norm(c.get("excerpt", "")) for c in readable):
        return "the supporting quote isn't in a cited page"
    return ""


_FIGURE = re.compile(r"(?:[$€£]\s?)?\d[\d,.]*\s?(?:%|\+|k\b|x\b|m\b)?", re.I)


def unbacked_figures(outline: str, evidence_ids: list[str], evidence: dict[str, str],
                     cfg: Config) -> list[str]:
    """Numbers in an answer outline that none of the candidate's evidence contains."""
    known = " ".join([*evidence.values(), *cfg.profile.highlights,
                      str(cfg.profile.years_experience)])
    known_digits = {re.sub(r"\D", "", m) for m in _FIGURE.findall(known)}
    out: list[str] = []
    for m in _FIGURE.findall(outline):
        digits = re.sub(r"\D", "", m)
        # Single digits are usually structure ("3 steps"), not claims.
        if len(digits) >= 2 and digits not in known_digits and m.strip() not in out:
            out.append(m.strip())
    return out[:5]


def validate_references(value: Any, sources: set[str], evidence: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            allowed = (sources if key == "source_ids"
                       else evidence if key == "evidence_ids" else None)
            if allowed is not None and not set(child).issubset(allowed):
                raise LLMError("Research included an unsupported citation. Please retry.")
            validate_references(child, sources, evidence)
    elif isinstance(value, list):
        for child in value:
            validate_references(child, sources, evidence)


# --- profile export / import ------------------------------------------------

MAX_IMPORTED_REPORTS = 200
MAX_REPORT_BYTES = 2_000_000
_KINDS = {"comparison", "interview"}
_FINISHED = {"complete", "failed"}


def export_reports(profile_dir: Path) -> list[dict[str, Any]]:
    """Finished reports for a profile export (running ones are skipped)."""
    return [r for r in list_reports(profile_dir) if r.get("status") in _FINISHED]


def import_reports(profile_dir: Path, reports: Any) -> int:
    """Restore exported reports into a (new) profile. Anything malformed is
    skipped, never trusted: ids must be UUIDs (no path tricks), kinds and
    statuses must be known, and each report is size-capped. Returns count."""
    if not isinstance(reports, list):
        return 0
    restored = 0
    for raw in reports[:MAX_IMPORTED_REPORTS]:
        if not isinstance(raw, dict):
            continue
        try:
            ident = str(uuid.UUID(str(raw.get("id"))))
        except ValueError:
            continue
        if raw.get("kind") not in _KINDS or raw.get("status") not in _FINISHED:
            continue
        if len(json.dumps(raw, ensure_ascii=False)) > MAX_REPORT_BYTES:
            continue
        save_report(profile_dir, {**raw, "id": ident})
        restored += 1
    return restored
