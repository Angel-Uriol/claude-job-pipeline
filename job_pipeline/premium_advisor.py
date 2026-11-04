"""Recommend premium (Claude in Chrome) searches only where they add value.

Premium search is slow and token-hungry, so instead of offering it for
everything, the advisor looks at THIS profile's data and suggests it where the
regular pipeline is blind or thin. Signals (cheap, no LLM call):

  company  no_board     A company you target has no public job board, so the
                        regular scrape can't see it at all.            (high)
  company  dry_board    A dream / tier-1 company's board was scraped but
                        nothing matching you came through — roles may be on
                        their own site or listed differently.          (medium)
  job      thin_jd      A strong match (apply/consider) whose description is
                        short or cut off — read the full posting before you
                        tailor a resume or apply.                      (medium)
  job      visa_unknown You need sponsorship and a strong match says nothing
                        about it — the careers site may.                 (low)

Things already premium-searched recently (or queued) aren't suggested again.
Each suggestion carries an estimated cost learned from your past premium runs
(Claude Code reports each run's API-equivalent cost), with a default until
there's history.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .chrome_queue import ChromeQueue
from .config import Config
from .models import Job, Status, Verdict
from .target_companies import TargetCompanyIndex, _normalize, default_target_index

THIN_JD_CHARS = 1200
RECENT_DAYS = 14
STRONG_SCORE = 7.0
# Until there's history: rough API-equivalent cost of searching one company.
DEFAULT_COST_PER_COMPANY = (0.5, 1.5)
DEFAULT_COST_PER_JOB = (0.05, 0.2)
_PRIORITY = {"high": 0, "medium": 1, "low": 2}

_INACTIVE = {Status.DISMISSED, Status.APPLIED, Status.REJECTED, Status.AUTO_REJECTED,
             Status.DEAD, Status.SKIPPED, Status.EXCLUDED}


def _recently_searched(profile: str | None) -> set[str]:
    """Companies with a premium request pending, running, or done recently."""
    cutoff = (datetime.now(UTC) - timedelta(days=RECENT_DAYS)).isoformat()
    names: set[str] = set()
    for rec in ChromeQueue().list_status(limit=200):
        if not rec.get("premium") or rec.get("profile") != profile:
            continue
        if rec.get("status") == "error":
            continue
        if (rec.get("created_at") or "") >= cutoff or rec.get("status") in ("pending", "running"):
            names.update(_normalize(n) for n in rec.get("ad_hoc_companies") or [])
    return names


def learned_costs(profile: str | None) -> dict[str, Any]:
    """Average API-equivalent cost (and plan % when measured) per company from
    this profile's past premium runs."""
    per_company: list[float] = []
    pct_per_company: list[float] = []
    for rec in ChromeQueue().list_status(limit=200):
        summ = rec.get("summary") if isinstance(rec.get("summary"), dict) else None
        if not rec.get("premium") or rec.get("profile") != profile or not summ:
            continue
        cost = summ.get("cost_usd")
        n = len(rec.get("ad_hoc_companies") or []) or 1
        if isinstance(cost, (int, float)) and cost > 0:
            per_company.append(float(cost) / n)
        pct = summ.get("plan_pct_used")
        if isinstance(pct, (int, float)) and pct > 0:
            pct_per_company.append(float(pct) / n)
    pct_avg = sum(pct_per_company) / len(pct_per_company) if pct_per_company else None
    if not per_company:
        return {"per_company": DEFAULT_COST_PER_COMPANY, "runs": 0, "learned": False,
                "pct_per_company": pct_avg}
    avg = sum(per_company) / len(per_company)
    return {"per_company": (round(avg * 0.7, 2), round(avg * 1.4, 2)),
            "runs": len(per_company), "learned": True, "pct_per_company": pct_avg}


def _display(name: str, idx: TargetCompanyIndex, preferred: list[str] | None = None) -> str:
    """Use the user's own spelling if they named it ("NVIDIA"), else title-case."""
    for p in preferred or []:
        if _normalize(p) == _normalize(name):
            return p
    return name if any(c.isupper() for c in name) else name.title()


def _jobs_by_company(jobs: list[Job]) -> dict[str, list[Job]]:
    out: dict[str, list[Job]] = {}
    for j in jobs:
        out.setdefault(_normalize(j.company), []).append(j)
    return out


def _strong(j: Job) -> bool:
    return (j.fit_verdict in (Verdict.APPLY, Verdict.CONSIDER)
            and (j.fit_score or 0) >= STRONG_SCORE and j.status not in _INACTIVE)


def recommend(
    cfg: Config, jobs: list[Job], *, profile: str | None,
    targets: TargetCompanyIndex | None = None, limit: int = 8,
) -> list[dict[str, Any]]:
    """Ranked premium-search suggestions for this profile."""
    idx = targets or default_target_index()
    skip = _recently_searched(profile)
    by_company = _jobs_by_company(jobs)
    dream = {_normalize(d) for d in cfg.profile.dream_companies}
    recs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(rec: dict[str, Any]) -> None:
        key = f"{rec['kind']}:{rec.get('job_key') or _normalize(rec['company'])}"
        if key not in seen and _normalize(rec["company"]) not in skip:
            seen.add(key)
            recs.append(rec)

    # 1. Targeted companies with no public board.
    for entry in cfg.scrapers.no_api:
        if entry.platform == "linkedin_search":
            continue
        name = _display(entry.name, idx, cfg.profile.dream_companies)
        add({"kind": "company", "company": name, "signal": "no_board",
             "priority": "high", "careers_url": entry.careers_url,
             "reason": f"{name} has no public job board, so the regular scrape "
                       "can't see its openings at all."})

    # 2. Dream / tier-1 companies whose board produced nothing for you.
    scraped = ([e.name for e in cfg.scrapers.greenhouse] + [e.name for e in cfg.scrapers.lever]
               + [e.name for e in cfg.scrapers.ashby])
    for name in scraped:
        n = _normalize(name)
        match = idx.match(name)
        important = n in dream or (match is not None and match.tier == 1)
        if not important or not jobs:
            continue
        # Any job ever seen from them (even applied/dismissed) means the board works.
        if not by_company.get(n):
            name = _display(name, idx, cfg.profile.dream_companies)
            add({"kind": "company", "company": name, "signal": "dry_board",
                 "priority": "medium",
                 "reason": f"{name} is one of your top companies, but its job board had "
                           "nothing matching you — their own careers site may list more "
                           "(other teams, locations, or titles)."})

    # 3./4. Strong jobs with thin descriptions or unknown sponsorship.
    for j in sorted(jobs, key=lambda x: -(x.fit_score or 0)):
        if not _strong(j):
            continue
        key = j.job_id or j.dedup_key()[1]
        desc = j.description or ""
        if len(desc) < THIN_JD_CHARS or "truncated" in desc.lower():
            add({"kind": "job", "company": j.company, "job_key": key, "url": j.url,
                 "title": j.title, "signal": "thin_jd", "priority": "medium",
                 "reason": f"“{j.title}” scores {round((j.fit_score or 0) * 10)} but its "
                           "description looks incomplete — read the full posting before "
                           "tailoring your resume."})
        elif (cfg.profile.needs_sponsorship
              and (j.extracted or {}).get("visa_stance") == "unknown"):
            add({"kind": "job", "company": j.company, "job_key": key, "url": j.url,
                 "title": j.title, "signal": "visa_unknown", "priority": "low",
                 "reason": f"“{j.title}” is a strong match but says nothing about visa "
                           "sponsorship — the careers site may."})

    recs.sort(key=lambda r: _PRIORITY[r["priority"]])
    return recs[:limit]


def estimate(recs: list[dict[str, Any]], profile: str | None) -> dict[str, Any]:
    """Cost estimate for running these suggestions."""
    costs = learned_costs(profile)
    companies = len({_normalize(r["company"]) for r in recs if r["kind"] == "company"})
    job_only = [r for r in recs if r["kind"] == "job"]
    lo = companies * costs["per_company"][0] + len(job_only) * DEFAULT_COST_PER_JOB[0]
    hi = companies * costs["per_company"][1] + len(job_only) * DEFAULT_COST_PER_JOB[1]
    units = companies + 0.15 * len(job_only)  # a posting read ≈ a fraction of a company
    pct = costs["pct_per_company"]
    return {"usd_low": round(lo, 2), "usd_high": round(hi, 2), "companies": companies,
            "jobs": len(job_only), "learned_from_runs": costs["runs"],
            "plan_pct": round(pct * units, 1) if pct else None}


WARN_AT_PCT = 80


def usage_message(est: dict[str, Any], secrets: Any = None) -> dict[str, Any]:
    """What to tell the user about cost before they launch, by auth mode."""
    from .config import load_secrets
    from .plan_usage import get_plan_usage

    s = secrets or load_secrets()
    cost = f"about ${est['usd_low']:.2f}–${est['usd_high']:.2f}"
    basis = (f" (from your last {est['learned_from_runs']} premium runs)"
             if est["learned_from_runs"] else " (rough estimate until your first run)")
    if s.auth_mode != "claude_code":
        return {"mode": "api_key", "warn": False, "plan": None,
                "text": f"Billed to your Anthropic API key: {cost}{basis}."}
    plan = get_plan_usage(s)
    parts = ["Runs on your Claude subscription and counts toward its usage limits."]
    warn = False
    five = plan.get("five_hour") if plan.get("available") else None
    if five:
        week = plan.get("seven_day")
        parts.append(f"Right now: {five['used_pct']:g}% of your 5-hour session used"
                     + (f", {week['used_pct']:g}% of the week" if week else "") + ".")
        if five["used_pct"] >= WARN_AT_PCT:
            warn = True
            parts.append("You're close to the session limit — the search may stop partway; "
                         "consider running it after the session resets.")
    else:
        parts.append("(Live usage isn't readable for this login — `/usage` in Claude Code "
                     "shows it.)")
    if est.get("plan_pct") is not None:
        parts.append(f"This search typically uses ~{est['plan_pct']:g}% of a 5-hour session "
                     "(measured on your past runs).")
    else:
        parts.append(f"API-equivalent size: {cost}{basis}; the app measures how much of "
                     "your session each run uses, so this gets more precise.")
    return {"mode": "claude_code", "warn": warn, "plan": plan, "text": " ".join(parts)}
