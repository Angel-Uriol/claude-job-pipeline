"""Scraper registry — platform name → concrete Scraper class.

The CLI iterates over `config.scrapers.greenhouse`, `scrapers.lever`,
`scrapers.ashby`, and `scrapers.remoteok` and uses this registry to
instantiate the right scraper for each entry. Adding a new ATS later
(Workday, SmartRecruiters, ...) is one line here plus a new module
under `scrapers/`.
"""

from __future__ import annotations

import httpx

from .ashby import AshbyScraper
from .base import Scraper
from .greenhouse import GreenhouseScraper
from .lever import LeverScraper
from .remoteok import RemoteOKScraper

# Platform name → Scraper class. Lowercased keys for case-insensitive
# lookup. If you add a new ATS, add it here AND export the class in
# `scrapers/__init__.py`.
#
# NOTE: JSearch is intentionally NOT registered here. It's query-driven and
# needs an authenticated client plus per-query params (query, num_pages,
# date_posted, remote_only) that this identity-only factory can't carry, so
# `run_jsearch` in runner.py instantiates `JSearchScraper` directly.
PLATFORM_REGISTRY: dict[str, type[Scraper]] = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "ashby": AshbyScraper,
    "remoteok": RemoteOKScraper,
}


def get_scraper(
    platform: str,
    name: str,
    slug: str,
    *,
    client: httpx.Client | None = None,
) -> Scraper:
    """Build a scraper for one company.

    Raises:
        KeyError: if `platform` isn't in the registry.
    """
    cls = PLATFORM_REGISTRY[platform.lower()]
    return cls(name=name, slug=slug, client=client)
