"""Scraper base class and shared utilities.

A `Scraper` knows how to talk to ONE ATS platform (Greenhouse, Lever, ...).
Subclasses implement `fetch()`, which returns a list of `Job` objects with
`source` and `discovered_at` already set. Everything else (filtering,
deduping, storing) happens downstream.

Two helpers live here because every scraper needs them:
- `http_get_json` — retry with exponential backoff, 30s timeout
- `strip_html`    — clean HTML out of JD content before pre-filter sees it
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import Any

import httpx

from ..models import Job

log = logging.getLogger(__name__)


# --- HTTP -----------------------------------------------------------------


# Per spec: every external network call has a 30s timeout and exponential
# backoff on retry. Three attempts at 1s / 2s / 4s gives us a max wait of
# 7 seconds of sleep on a fully failing endpoint before we give up.
HTTP_TIMEOUT_SECONDS = 30.0
HTTP_MAX_ATTEMPTS = 3
HTTP_BACKOFF_BASE = 1.0


def http_get_json(
    client: httpx.Client,
    url: str,
    *,
    max_attempts: int = HTTP_MAX_ATTEMPTS,
    backoff_base: float = HTTP_BACKOFF_BASE,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> Any:
    """GET `url`, return parsed JSON. Retries with exponential backoff.

    Raises the last httpx error if all attempts fail. The caller is
    expected to catch and log per-call, so one bad board doesn't tank
    the whole scrape run.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = client.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                delay = backoff_base * (2**attempt)
                log.warning(
                    "GET %s failed (attempt %d/%d): %s — sleeping %.1fs",
                    url,
                    attempt + 1,
                    max_attempts,
                    e,
                    delay,
                )
                time.sleep(delay)
    # All attempts exhausted.
    assert last_exc is not None
    raise last_exc


# --- HTML stripping --------------------------------------------------------


class _TextExtractor(HTMLParser):
    """HTMLParser subclass that collects only the visible text.

    `convert_charrefs=True` (the default since Python 3.5) means we get
    decoded entities ("&amp;" → "&") via `handle_data` automatically,
    so we don't need to handle character references separately.
    """

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    @property
    def text(self) -> str:
        # Collapse all whitespace (newlines, multiple spaces) into single
        # spaces — the pre-filter doesn't care about formatting.
        return " ".join(" ".join(self._parts).split())


def strip_html(html: str) -> str:
    """Turn HTML into a single line of plain text.

    Robust to malformed HTML (HTMLParser is permissive); whitespace is
    collapsed; HTML entities are decoded. Empty input → empty output.
    """
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text


# --- abstract scraper -----------------------------------------------------


class Scraper(ABC):
    """One scraper per ATS platform (Greenhouse, Lever, ...).

    Subclasses set `source` (the Source enum value to stamp on each Job)
    and implement `fetch()`. The base class provides a shared httpx
    client; tests can inject their own mock.
    """

    # Subclasses MUST override these two:
    source: str = "UNSET"  # noqa: F821 — overridden in concrete classes
    base_url_template: str = ""

    def __init__(
        self,
        name: str,
        slug: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        """
        Args:
            name: company display name (e.g. "anthropic"). Lowercased
                in stored jobs to keep dedup keys consistent.
            slug: ATS-specific slug used in the URL.
            client: optional httpx.Client. If omitted, the scraper
                creates one with the default timeout. Test code should
                pass a mock instead of None.
        """
        self.name = name
        self.slug = slug
        # Lazy-create client so tests can stub it without ever touching httpx.
        self._client: httpx.Client | None = client

    @property
    def client(self) -> httpx.Client:
        """The httpx client, created on first use."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=HTTP_TIMEOUT_SECONDS,
                headers={"User-Agent": "job-pipeline-v2 (personal use)"},
            )
        return self._client

    @abstractmethod
    def fetch(self) -> list[Job]:
        """Pull every current job from this company's board.

        Implementations should:
        - call `http_get_json(self.client, url)` for the network call,
        - tolerate per-job parse failures (log and skip, don't abort),
        - set `source` and `discovered_at` on every returned Job.
        """
        ...
