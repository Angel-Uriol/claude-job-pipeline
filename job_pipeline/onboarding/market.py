"""Live market scan during the setup chat (between rounds).

A small version of the real pipeline, so what the chat shows is what the
daily run would surface:

    1. Scrape — Claude suggests employers that hire for the candidate's
       target roles; we find which have a public Greenhouse / Lever / Ashby
       board (ats.py) and fetch their open postings with the regular scrapers.
    2. Filter — the profile's rules so far: location (allowed places, or
       remote — US-only if they said so), excluded title words, posting age,
       and title relevance to their search keywords.
    3. Score — Claude rates the top candidates for fit in ONE batched call
       (cheap) and gives a one-line reason each.
    4. Present — the best few (max 2 per company) are shown, and Claude lists
       the requirements they share, marking which the profile confirms.

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
RATE_POOL = 25        # candidates sent to the batched fit rating
MIN_RATING = 5.0      # below this a posting isn't shown
MAX_AGE_DAYS = 45
JD_CHARS = 2500
SCAN_COMPANIES = 12


class Requirement(BaseModel):
    skill: str = Field(description="Short name: a tool, skill, credential or experience type.")
    count: int = Field(ge=0, description="How many of the postings ask for it.")
    confirmed: bool = Field(description="True if the candidate profile already shows it.")


class Rating(BaseModel):
    index: int = Field(description="The posting's number in the list.")
    score: float = Field(description="Fit 0-10 for THIS candidate.")
    reason: str = Field(default="", description="Under 15 words, why.")


class Ratings(BaseModel):
    ratings: list[Rating] = Field(default_factory=list)


RATE_PROMPT = """Rate how well each posting fits this job seeker (0-10). Consider role
type vs. their target roles and tiers, seniority vs. their level and years,
location vs. their rule, and visa needs (if they need sponsorship, postings
that say no sponsorship or require clearance/citizenship score 0). Be strict:
unrelated roles (e.g. facilities, sales, hardware for a software person) score
0-2.

Candidate (JSON):
{profile}

Postings:
{postings}

Return one rating per posting, using its number as `index`."""


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


# Words that appear in almost every title, so they say nothing about fit.
_GENERIC_TITLE_WORDS = {
    "engineer", "engineering", "developer", "software", "senior", "staff", "lead",
    "principal", "junior", "associate", "specialist", "manager", "the", "and",
}


def _title_score(title: str, keywords: list[str]) -> int:
    """Phrase hits count most; otherwise count shared *meaningful* words
    ("data engineer" vs "Data Center Controls Engineer" shares only "data")."""
    t = title.lower()
    t_words = set(re.findall(r"[a-z0-9+#]+", t))
    score = 0
    for kw in keywords:
        if kw in t:
            score += 10
        else:
            words = [w for w in re.findall(r"[a-z0-9+#]+", kw)
                     if len(w) > 2 and w not in _GENERIC_TITLE_WORDS]
            score += sum(2 for w in words if w in t_words)
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


def _location_ok(location: str, draft: ProfileDraft) -> bool:
    """The profile's location rule (lenient while it's still being collected)."""
    from .writer import NON_US_TOKENS, US_NAMES

    loc = (location or "").lower()
    tokens = [t.lower() for t in draft.location_tokens or []]
    if not tokens and draft.allow_remote is None:
        return True  # no rule yet
    if any(t in loc for t in tokens):
        return True
    if draft.allow_remote and any(t in loc for t in ("remote", "anywhere", "distributed")):
        us_only = (draft.remote_country or "").strip().lower() in US_NAMES
        return not (us_only and any(t in loc for t in NON_US_TOKENS))
    return False


def _fresh(job: Job) -> bool:
    from ..scrapers.filters import is_too_old

    return not is_too_old(job.posted_date, MAX_AGE_DAYS)


def filter_jobs(jobs: list[Job], draft: ProfileDraft, *, exclude_urls: set[str]) -> list[Job]:
    """Stage 2: profile rules + title relevance, best title matches first."""
    keywords = _keywords(draft)
    scored = [
        (s, j) for j in jobs
        if j.url not in exclude_urls and not _excluded(j.title, draft)
        and _location_ok(j.location, draft) and _fresh(j)
        and (s := _title_score(j.title, keywords)) >= TITLE_MIN
    ]
    scored.sort(key=lambda x: -x[0])
    return [j for _s, j in scored]


# A single shared word ("data", "engineer") isn't relevance — need a phrase
# hit or two significant words.
TITLE_MIN = 4


def pick_jobs(
    jobs: list[Job], draft: ProfileDraft, *, exclude_urls: set[str], limit: int = MAX_JOBS
) -> list[Job]:
    """Filter, then keep the best `limit` with at most PER_COMPANY each."""
    return _per_company(filter_jobs(jobs, draft, exclude_urls=exclude_urls), limit)


def _per_company(jobs: list[Job], limit: int) -> list[Job]:
    out: list[Job] = []
    per: dict[str, int] = {}
    for j in jobs:
        if per.get(j.company, 0) >= PER_COMPANY:
            continue
        out.append(j)
        per[j.company] = per.get(j.company, 0) + 1
        if len(out) >= limit:
            break
    return out


def rate_jobs(
    jobs: list[Job], draft: ProfileDraft, *, llm: ClaudeClient, model: str
) -> list[tuple[Job, float, str]]:
    """Stage 3: one batched Claude call → (job, score, reason), best first."""
    if not jobs:
        return []
    postings = "\n".join(
        f"{i}. {j.title} — {j.company} — {j.location or 'n/a'} — "
        f"{' '.join((j.description or '').split())[:500]}"
        for i, j in enumerate(jobs, 1)
    )
    try:
        out = llm.structured(
            RATE_PROMPT.format(profile=draft.model_dump_json(exclude_none=True),
                               postings=postings),
            Ratings, model=model, max_tokens=3000, tool_name="submit_ratings",
            tool_description="Submit one fit rating per posting.",
        )
    except LLMError as e:
        log.warning("market scan: rating failed, using title order: %s", e)
        return [(j, 0.0, "") for j in jobs]
    by_index = {r.index: r for r in out.ratings}
    rated = [
        (j, by_index[i].score, by_index[i].reason)
        for i, j in enumerate(jobs, 1) if i in by_index
    ]
    rated.sort(key=lambda x: -x[1])
    return rated


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
    filtered = filter_jobs(all_jobs, draft, exclude_urls=seen)
    candidates = _per_company(filtered, RATE_POOL)
    rated = rate_jobs(candidates, draft, llm=llm, model=model)
    # A failed rating call returns (job, 0.0, "") for all — then keep title order.
    unrated = all(sc == 0.0 and not why for _j, sc, why in rated)
    good = [(j, sc, why) for j, sc, why in rated if unrated or sc >= MIN_RATING]
    shown = good[:MAX_JOBS]
    picked = [j for j, _sc, _why in shown]
    reasons = {j.url: (sc, why) for j, sc, why in shown}

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
            {"company": j.company, "title": j.title, "url": j.url, "location": j.location,
             "score": reasons[j.url][0], "reason": reasons[j.url][1]}
            for j in picked
        ],
        "boards": len(companies),
        "scanned": len(all_jobs),
        "filtered": len(filtered),
        "rated": len(candidates),
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
            f"I scanned {result['scanned']:,} open roles at {result['boards']} companies → "
            f"{result.get('filtered', 0):,} fit your location, level and role words → I rated "
            f"the top {result.get('rated', 0)} → here are the {len(jobs)} best. "
            f"{result['summary']}".strip()
        )
    else:
        text = ("I couldn't pull matching postings right now, so I'll ask a few "
                "questions based on what roles like yours usually need.")
    state.say(text, kind="market", jobs=jobs)
    state.phase = "round2" if state.phase == "market1" else "round3"
    state.round_questions = 0
    return step(state, None, llm=llm, model=model)
