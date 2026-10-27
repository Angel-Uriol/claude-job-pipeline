"""ATS scrapers — one module per platform.

`base.Scraper` is the interface every concrete scraper implements.
`registry.get_scraper()` is the factory the CLI uses to look up a scraper
by platform name. Filters (US-only, max age, title exclusions) are
applied separately in `filters.py` after fetch.
"""

from .ashby import AshbyScraper
from .base import Scraper, strip_html
from .greenhouse import GreenhouseScraper
from .jsearch import JSearchScraper
from .lever import LeverScraper
from .registry import PLATFORM_REGISTRY, get_scraper
from .remoteok import RemoteOKScraper

__all__ = [
    "Scraper",
    "GreenhouseScraper",
    "LeverScraper",
    "AshbyScraper",
    "RemoteOKScraper",
    "JSearchScraper",
    "PLATFORM_REGISTRY",
    "get_scraper",
    "strip_html",
]
