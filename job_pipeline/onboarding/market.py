"""Live market scan during the setup chat (between rounds).

    1. Claude suggests employers that hire for the candidate's target roles.
    2. We find which have a public Greenhouse / Lever / Ashby board (ats.py)
       and fetch their open postings with the regular scrapers.
    3. Postings whose titles match the candidate's search keywords are ranked
       and a handful (max 2 per company) are kept.
    4. Claude reads those job descriptions and lists the requirements they
       share, marking which ones the profile already confirms.

The next chat round turns the unconfirmed requirements into questions
("3 of the 8 roles I found ask for Kubernetes — have you used it?"). The
employers found here also pre-fill the Companies step, already verified.

Network or LLM trouble never breaks the chat: the scan degrades to fewer (or
zero) postings and the round still runs.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel, Field

from ..llm import ClaudeClient, LLMError
from ..models import Job
from ..scrapers import get_scraper
from .ats import find_boards
from .companies import suggest_companies
from .interview import InterviewState, ProfileDraft, step

log = logging.getLogger(__name__)

MAX_JOBS = 8
PER_COMPANY = 2
JD_CHARS = 2500
SCAN_COMPANIES = 12


class Requirement(BaseModel):
    skill: str = Field(description="Short name: a tool, skill, credential or experience type.")
    count: int = Field(ge=0, description="How many of the postings ask for it.")
    confirmed: bool = Field(description="True if the candidate profile already shows it.")


class MarketFindings(BaseModel):
    summary: str = Field(description="1-2 friendly sentences on what these postings look for.")
    requirements: list[Requirement] = Field(default_factory=list)


FINDINGS_PROMPT = """You are helping a job seeker understand what real employers ask for.

Candidate profile (JSON):
{profile}

Here are {n} real, currently open postings:

{postings}

List up to 10 requirements that appear in several postings (skills, tools,
certifications, years/levels, domain experience), with how many postings
mention each, and whether the candidate profile already confirms it. Then
write a 1-2 sentence friendly summary addressed to the candidate ("Most of
these roles…"). Be factual — only count what the postings actually say."""


def _keywords(draft: ProfileDraft) -> list[str]:
    words = list(draft.search_keywords or [])
    if not words:
        words = [f.name for f in (draft.target_functions or []) if f.tier in ("A", "B")]
    return [w.lower() for w in words if w.strip()]


def _title_score(title: str, keywords: list[str]) -> int:
    """Phrase hits count most; otherwise count shared significant words."""
    t = title.lower()
    score = 0
    for kw in keywords:
        if kw in t:
            score += 10
        else:
            words = [w for w in re.findall(r"[a-z0-9+#]+", kw) if len(w) > 2]
            score += sum(2 for w in words if w in t)
    return score


def _excluded(title: str, draft: ProfileDraft) -> bool:
    t = title.lower()
    return any(re.search(rf"\b{re.escape(x.lower())}\b", t) for x in draft.excluded_titles or [])


def _fetch(board: dict[str, Any]) -> list[Job]:
    try:
        return get_scraper(board["platform"], board["name"], board["slug"]).fetch()
    except Exception as e:  # noqa: BLE001 - one bad board shouldn't stop the scan
        log.warning("market scan: %s failed: %s", board["name"], e)
        return []


def pick_jobs(
    jobs: list[Job], draft: ProfileDraft, *, exclude_urls: set[str], limit: int = MAX_JOBS
) -> list[Job]:
    keywords = _keywords(draft)
    scored = [
        (s, j) for j in jobs
        if j.url not in exclude_urls and not _excluded(j.title, draft)
        and (s := _title_score(j.title, keywords)) > 0
    ]
    scored.sort(key=lambda x: -x[0])
    out: list[Job] = []
    per: dict[str, int] = {}
    for _s, j in scored:
        if per.get(j.company, 0) >= PER_COMPANY:
            continue
        out.append(j)
        per[j.company] = per.get(j.company, 0) + 1
        if len(out) >= limit:
            break
    return out


def scan_market(
    state: InterviewState, *, llm: ClaudeClient, model: str
) -> dict[str, Any]:
    """Find real postings for this profile and summarize their requirements."""
    draft = state.draft
    # Reuse employers found by an earlier scan; ask Claude only the first time.
    companies = [c for c in state.companies if c.get("platform")]
    if not companies:
        try:
            suggested = suggest_companies(draft, llm=llm, model=model, n=SCAN_COMPANIES)
        except LLMError as e:
            log.warning("market scan: company suggestions failed: %s", e)
            suggested = []
        results = find_boards((c["name"], c.get("aliases") or []) for c in suggested)
        companies = [
            {**c, "platform": r.platform, "slug": r.slug, "jobs": r.jobs}
            for c, r in zip(suggested, results, strict=True)
        ]
        _merge_companies(state, companies)
        companies = [c for c in companies if c.get("platform")]

    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = list(pool.map(_fetch, companies))
    all_jobs = [j for batch in batches for j in batch]
    seen = {j.get("url", "") for m in state.market for j in m.get("jobs") or []}
    picked = pick_jobs(all_jobs, draft, exclude_urls=seen)

    findings = MarketFindings(summary="", requirements=[])
    if picked:
        postings = "\n\n".join(
            f"## {i}. {j.title} — {j.company} ({j.location or 'location n/a'})\n"
            f"{(j.description or '')[:JD_CHARS]}"
            for i, j in enumerate(picked, 1)
        )
        try:
            findings = llm.structured(
                FINDINGS_PROMPT.format(
                    profile=draft.model_dump_json(exclude_none=True),
                    n=len(picked), postings=postings,
                ),
                MarketFindings,
                model=model,
                max_tokens=1500,
                tool_name="submit_findings",
                tool_description="Submit the shared requirements and a short summary.",
            )
        except LLMError as e:
            log.warning("market scan: findings failed: %s", e)

    return {
        "round": state.round,
        "summary": findings.summary,
        "requirements": [r.model_dump() for r in findings.requirements],
        "jobs": [
            {"company": j.company, "title": j.title, "url": j.url, "location": j.location}
            for j in picked
        ],
        "boards": len(companies),
        "scanned": len(all_jobs),
    }


def _merge_companies(state: InterviewState, found: list[dict[str, Any]]) -> None:
    """Add scanned employers to the Companies step (verified), without dupes."""
    have = {c["name"].lower() for c in state.companies}
    for c in found:
        if c["name"].lower() not in have:
            state.companies.append({**c, "selected": True})
            have.add(c["name"].lower())


def run_market(state: InterviewState, *, llm: ClaudeClient, model: str) -> InterviewState:
    """Run the scan for a market phase, post the findings, start the next round."""
    if state.phase not in ("market1", "market2"):
        return state
    result = scan_market(state, llm=llm, model=model)
    state.market.append(result)
    jobs = result["jobs"]
    if jobs:
        text = (
            f"I scanned {result['scanned']} open roles at {result['boards']} companies and "
            f"picked {len(jobs)} that look like you. {result['summary']}".strip()
        )
    else:
        text = ("I couldn't pull matching postings right now, so I'll ask a few "
                "questions based on what roles like yours usually need.")
    state.say(text, kind="market", jobs=jobs)
    state.phase = "round2" if state.phase == "market1" else "round3"
    state.round_questions = 0
    return step(state, None, llm=llm, model=model)
