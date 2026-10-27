"""Find which public job board (ATS) a company uses, if any.

Probes Greenhouse, Lever and Ashby — the three boards the scrapers support —
with slugs derived from the company name and aliases. A board only counts
when its endpoint returns the structure the scraper expects AND it has at
least one live job (so dead or empty slugs never land in config).

Endpoints mirror the scrapers:
  greenhouse  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  lever       https://api.lever.co/v0/postings/{slug}?mode=json
  ashby       https://api.ashbyhq.com/posting-api/job-board/{slug}
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

TIMEOUT = 8.0
MAX_WORKERS = 12
HEADERS = {"User-Agent": "claude-job-pipeline ATS discovery (personal use)"}

PLATFORMS = ("greenhouse", "lever", "ashby")
URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


@dataclass
class Probe:
    """One company to probe: its name + candidate slugs to try."""

    name: str
    candidates: list[str] = field(default_factory=list)


@dataclass
class BoardResult:
    """Outcome for one company. `platform` is None when no board was found."""

    name: str
    platform: str | None = None
    slug: str | None = None
    jobs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_slugs(label: str) -> list[str]:
    """Slugs derived from a name: "Hugging Face" -> [huggingface, hugging-face, hugging]."""
    norm = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
    tokens = norm.split()
    if not tokens:
        return []
    out: list[str] = []
    for c in ("".join(tokens), "-".join(tokens), tokens[0]):
        if c and c not in out:
            out.append(c)
    return out


def build_probe(name: str, aliases: Iterable[str] = ()) -> Probe:
    cands: list[str] = []
    for label in (name, *aliases):
        for c in candidate_slugs(str(label)):
            if c not in cands:
                cands.append(c)
    return Probe(name=name, candidates=cands)


def count_jobs(client: httpx.Client, platform: str, slug: str) -> int:
    """Live-job count for a board, or -1 if the slug isn't a valid board."""
    try:
        resp = client.get(URLS[platform].format(slug=slug))
    except httpx.HTTPError:
        return -1
    if resp.status_code != 200:
        return -1
    try:
        data = resp.json()
    except ValueError:
        return -1
    if platform == "lever":
        return len(data) if isinstance(data, list) else -1
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return len(data["jobs"])
    return -1


def probe_company(probe: Probe) -> BoardResult:
    """Try each candidate slug on each platform; the first live board wins."""
    with httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
        for slug in probe.candidates:
            for platform in PLATFORMS:
                n = count_jobs(client, platform, slug)
                if n >= 1:
                    return BoardResult(name=probe.name, platform=platform, slug=slug, jobs=n)
    return BoardResult(name=probe.name)


def find_boards(companies: Iterable[tuple[str, Iterable[str]]]) -> list[BoardResult]:
    """Probe many (name, aliases) pairs in parallel. Order is preserved."""
    probes = [build_probe(name, aliases) for name, aliases in companies]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        return list(pool.map(probe_company, probes))
