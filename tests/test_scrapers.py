"""Tests for the Greenhouse and Lever scrapers.

We never hit the real network. Tests use frozen JSON fixtures in
`tests/fixtures/` and a mock httpx.Client that returns them. The fixtures
mirror the actual response shapes from each API — if those APIs ever
change, the fixtures (and the scrapers) need updating together.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from job_pipeline.models import Source
from job_pipeline.scrapers import (
    AshbyScraper,
    GreenhouseScraper,
    JSearchScraper,
    LeverScraper,
    RemoteOKScraper,
    get_scraper,
    strip_html,
)
from job_pipeline.scrapers.base import http_get_json

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> Any:
    """Load a JSON fixture from tests/fixtures/."""
    with (FIXTURES / name).open() as f:
        return json.load(f)


def _mock_client(payload: Any) -> Mock:
    """Build a mock httpx.Client whose .get() returns `payload` as JSON."""
    resp = Mock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    client = Mock(spec=httpx.Client)
    client.get.return_value = resp
    return client


# --- strip_html -----------------------------------------------------------


def test_strip_html_handles_basic_tags() -> None:
    s = strip_html("<p>Hello <strong>world</strong></p>")
    assert s == "Hello world"


def test_strip_html_decodes_entities() -> None:
    """HTMLParser with convert_charrefs=True handles &amp; etc. for us."""
    s = strip_html("<p>Python &amp; PySpark</p>")
    assert s == "Python & PySpark"


def test_strip_html_collapses_whitespace() -> None:
    s = strip_html("<p>line1</p>\n\n<p>  line2  </p>")
    assert s == "line1 line2"


def test_strip_html_empty_input() -> None:
    assert strip_html("") == ""


def test_strip_html_malformed_input_does_not_crash() -> None:
    """HTMLParser is permissive — broken HTML just gives us what it can."""
    s = strip_html("<p>unclosed tag <em>nested")
    assert "unclosed tag" in s
    assert "nested" in s


# --- Greenhouse scraper ---------------------------------------------------


def test_greenhouse_parses_basic_job() -> None:
    """A normal Greenhouse posting maps to all the expected Job fields."""
    payload = _load_fixture("greenhouse_sample.json")
    scraper = GreenhouseScraper(name="anthropic", slug="anthropic", client=_mock_client(payload))
    jobs = scraper.fetch()

    assert len(jobs) == 4
    j = jobs[0]
    assert j.company == "anthropic"
    assert j.job_id == "4567890"
    assert j.title == "Software Engineer, ML Platform"
    assert j.team == "Engineering"
    assert j.source == Source.GREENHOUSE
    assert j.location == "San Francisco, CA"
    assert "Build ML infrastructure" in j.description
    # HTML stripped, entities decoded.
    assert "<" not in j.description
    assert "Python, PySpark & Airflow" in j.description
    # `first_published` preferred over `updated_at`.
    assert j.posted_date == "2026-04-10T12:00:00Z"


def test_greenhouse_handles_empty_departments() -> None:
    """No departments → team is None, not a crash."""
    payload = _load_fixture("greenhouse_sample.json")
    scraper = GreenhouseScraper(name="anthropic", slug="anthropic", client=_mock_client(payload))
    jobs = scraper.fetch()
    # Job #4567892 has departments: []
    remote_eng = next(j for j in jobs if j.job_id == "4567892")
    assert remote_eng.team is None


def test_greenhouse_skips_malformed_job_without_crashing() -> None:
    """Per spec: one bad job shouldn't tank the run."""
    loc = {"name": "x"}
    payload = {
        "jobs": [
            {"id": 1, "title": "Good", "absolute_url": "u", "location": loc, "content": ""},
            {"title": "Missing id"},
            {"id": 3, "title": "Also good", "absolute_url": "u3", "location": loc, "content": ""},
        ]
    }
    scraper = GreenhouseScraper("test", "test", client=_mock_client(payload))
    jobs = scraper.fetch()
    assert len(jobs) == 2
    assert {j.job_id for j in jobs} == {"1", "3"}


def test_greenhouse_job_id_is_stringified() -> None:
    """Greenhouse returns int IDs; we store strings so JSON round-trips cleanly."""
    payload = _load_fixture("greenhouse_sample.json")
    scraper = GreenhouseScraper("anthropic", "anthropic", client=_mock_client(payload))
    jobs = scraper.fetch()
    for j in jobs:
        assert isinstance(j.job_id, str)


# --- Lever scraper --------------------------------------------------------


def test_lever_parses_basic_job() -> None:
    payload = _load_fixture("lever_sample.json")
    scraper = LeverScraper(name="example", slug="example", client=_mock_client(payload))
    jobs = scraper.fetch()

    assert len(jobs) == 4
    j = jobs[0]
    assert j.company == "example"
    assert j.job_id == "abc-def-123"
    assert j.title == "Senior Software Engineer, Data Platform"
    assert j.team == "Data Platform"
    assert j.source == Source.LEVER
    assert j.location == "San Francisco, CA"
    # descriptionPlain preferred, then list sections appended.
    assert "Build the data platform" in j.description
    assert "Responsibilities" in j.description
    assert "PySpark and Airflow" in j.description


def test_lever_converts_ms_epoch_to_iso() -> None:
    """`createdAt` is ms-since-epoch UTC; we normalize to ISO 8601."""
    payload = _load_fixture("lever_sample.json")
    scraper = LeverScraper("example", "example", client=_mock_client(payload))
    jobs = scraper.fetch()
    j = jobs[0]
    assert j.posted_date is not None
    # 1712000000000 ms = 2024-04-01T...
    assert j.posted_date.startswith("2024-04")


def test_lever_falls_back_to_department_when_no_team() -> None:
    """When `categories.team` is empty, use `department`."""
    payload = [
        {
            "id": "x",
            "text": "Some Role",
            "hostedUrl": "u",
            "createdAt": 0,
            "categories": {"location": "NYC", "department": "Eng"},
            "description": "",
            "lists": [],
        }
    ]
    scraper = LeverScraper("test", "test", client=_mock_client(payload))
    jobs = scraper.fetch()
    assert jobs[0].team == "Eng"


def test_lever_handles_array_response_shape() -> None:
    """Lever returns a top-level JSON array (not a dict). Empty array → empty list."""
    scraper = LeverScraper("test", "test", client=_mock_client([]))
    assert scraper.fetch() == []


def test_lever_tolerates_unexpected_dict_response() -> None:
    """If Lever ever returns a dict (it shouldn't), we don't crash."""
    scraper = LeverScraper("test", "test", client=_mock_client({"unexpected": "shape"}))
    assert scraper.fetch() == []


# --- http_get_json retries ------------------------------------------------


def test_http_get_json_succeeds_first_try() -> None:
    resp = Mock()
    resp.json.return_value = {"ok": True}
    resp.raise_for_status.return_value = None
    client = Mock(spec=httpx.Client)
    client.get.return_value = resp

    result = http_get_json(client, "https://example.com")
    assert result == {"ok": True}
    assert client.get.call_count == 1


def test_http_get_json_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """First two calls fail, third succeeds — we should return the third's payload."""
    monkeypatch.setattr("time.sleep", lambda _: None)  # don't actually sleep in tests

    good_resp = Mock()
    good_resp.json.return_value = {"ok": True}
    good_resp.raise_for_status.return_value = None

    client = Mock(spec=httpx.Client)
    client.get.side_effect = [
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
        good_resp,
    ]

    result = http_get_json(client, "https://example.com")
    assert result == {"ok": True}
    assert client.get.call_count == 3


def test_http_get_json_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """After exhausting retries, the original exception bubbles up."""
    monkeypatch.setattr("time.sleep", lambda _: None)

    client = Mock(spec=httpx.Client)
    client.get.side_effect = httpx.ConnectError("nope")

    with pytest.raises(httpx.ConnectError):
        http_get_json(client, "https://example.com", max_attempts=3)
    assert client.get.call_count == 3


# --- registry -------------------------------------------------------------


def test_registry_returns_greenhouse_scraper() -> None:
    s = get_scraper("greenhouse", "anthropic", "anthropic")
    assert isinstance(s, GreenhouseScraper)
    assert s.name == "anthropic"
    assert s.slug == "anthropic"


def test_registry_returns_lever_scraper() -> None:
    s = get_scraper("lever", "example", "example")
    assert isinstance(s, LeverScraper)


def test_registry_case_insensitive() -> None:
    s = get_scraper("GREENHOUSE", "x", "x")
    assert isinstance(s, GreenhouseScraper)


def test_registry_returns_ashby_scraper() -> None:
    s = get_scraper("ashby", "linear", "linear")
    assert isinstance(s, AshbyScraper)
    assert s.name == "linear"
    assert s.slug == "linear"


def test_registry_returns_remoteok_scraper() -> None:
    s = get_scraper("remoteok", "remoteok", "remoteok")
    assert isinstance(s, RemoteOKScraper)


def test_registry_unknown_platform_raises() -> None:
    with pytest.raises(KeyError):
        # "smartrecruiters" is intentionally unknown — keep this in sync if
        # we ever add it as a real platform.
        get_scraper("smartrecruiters", "x", "x")


# --- Ashby scraper --------------------------------------------------------


def test_ashby_parses_basic_job() -> None:
    payload = _load_fixture("ashby_sample.json")
    scraper = AshbyScraper(name="linear", slug="linear", client=_mock_client(payload))
    jobs = scraper.fetch()

    # 4 in fixture; 1 is unlisted (filtered out) → expect 3.
    assert len(jobs) == 3
    j = jobs[0]
    assert j.company == "linear"
    assert j.job_id == "d3bc1ced-3ce4-4086-a050-555055dbb1ff"
    assert j.title == "Senior Data Engineer"
    assert j.team == "Data Platform"
    assert j.source == Source.ASHBY
    assert j.url.startswith("https://jobs.ashbyhq.com/")
    assert "San Francisco" in j.location
    assert "Remote — US" in j.location  # secondary location appended
    # descriptionPlain preferred over descriptionHtml.
    assert "Build the data platform" in j.description


def test_ashby_skips_unlisted_jobs() -> None:
    """isListed=false → posting is suppressed and never reaches the store."""
    payload = _load_fixture("ashby_sample.json")
    scraper = AshbyScraper(name="linear", slug="linear", client=_mock_client(payload))
    jobs = scraper.fetch()
    assert all(j.job_id != "unlisted-skip-me" for j in jobs)


def test_ashby_stamps_remote_when_only_isremote_flag_set() -> None:
    """A job with empty `location` but `isRemote: true` should be tagged 'Remote'."""
    payload = _load_fixture("ashby_sample.json")
    scraper = AshbyScraper(name="linear", slug="linear", client=_mock_client(payload))
    jobs = scraper.fetch()
    remote = next(j for j in jobs if j.job_id == "a1b2c3d4-1111-2222-3333-444455556666")
    assert "Remote" in remote.location
    # And the CA-or-remote downstream filter will see the keyword.


def test_ashby_falls_back_to_html_description() -> None:
    """When descriptionPlain is empty, use descriptionHtml with strip_html."""
    payload = _load_fixture("ashby_sample.json")
    scraper = AshbyScraper(name="linear", slug="linear", client=_mock_client(payload))
    jobs = scraper.fetch()
    j = next(j for j in jobs if j.job_id == "html-fallback-id")
    # The HTML had <em>great</em> — after strip we should see the text.
    assert "Build great APIs." in j.description


def test_ashby_skips_malformed_job_without_crashing() -> None:
    """Per spec: one bad job shouldn't tank the run."""
    payload = {
        "jobs": [
            {
                "id": "good-1",
                "title": "Good",
                "isListed": True,
                "jobUrl": "https://example.com/good-1",
                "applyUrl": "https://example.com/good-1/apply",
                "descriptionPlain": "ok",
                "location": "Remote",
                "publishedAt": "2026-05-01T00:00:00.000+00:00",
            },
            {"title": "Missing id", "isListed": True},  # bad — no id
            {
                "id": "good-2",
                "title": "Also good",
                "isListed": True,
                "jobUrl": "https://example.com/good-2",
                "applyUrl": "https://example.com/good-2/apply",
                "descriptionPlain": "ok",
                "location": "Remote",
                "publishedAt": "2026-05-01T00:00:00.000+00:00",
            },
        ]
    }
    scraper = AshbyScraper("test", "test", client=_mock_client(payload))
    jobs = scraper.fetch()
    assert len(jobs) == 2
    assert {j.job_id for j in jobs} == {"good-1", "good-2"}


# --- RemoteOK scraper -----------------------------------------------------


def test_remoteok_filters_to_engineering_jobs() -> None:
    """Customer support + marketing roles should be skipped; eng + ML kept."""
    payload = _load_fixture("remoteok_sample.json")
    scraper = RemoteOKScraper(name="remoteok", slug="remoteok", client=_mock_client(payload))
    jobs = scraper.fetch()
    # 1 disclaimer + 4 jobs; 2 are eng (data eng + ML), 2 are not.
    assert len(jobs) == 2
    titles = {j.title for j in jobs}
    assert "Senior Data Engineer" in titles
    assert "Machine Learning Engineer" in titles
    assert "Customer Support Specialist" not in titles
    assert "Marketing Manager" not in titles


def test_remoteok_company_is_per_posting_not_scraper_name() -> None:
    """RemoteOK is an aggregator — each posting carries its own company name."""
    payload = _load_fixture("remoteok_sample.json")
    scraper = RemoteOKScraper(name="remoteok", slug="remoteok", client=_mock_client(payload))
    jobs = scraper.fetch()
    companies = {j.company for j in jobs}
    # Lowercased per Source convention used across scrapers.
    assert "foo co" in companies
    assert "baz labs" in companies
    # None should be tagged as 'remoteok'
    assert "remoteok" not in companies


def test_remoteok_stamps_remote_when_location_blank() -> None:
    """If a RemoteOK posting has no `location`, default it to 'Remote'."""
    payload = _load_fixture("remoteok_sample.json")
    scraper = RemoteOKScraper(name="remoteok", slug="remoteok", client=_mock_client(payload))
    jobs = scraper.fetch()
    ml = next(j for j in jobs if j.title == "Machine Learning Engineer")
    assert ml.location == "Remote"


def test_remoteok_skips_disclaimer_at_index_zero() -> None:
    """The first array element on RemoteOK is a legal disclaimer; jobs start at [1]."""
    # Minimal payload: just a disclaimer, no jobs.
    payload = [{"last_updated": "x", "legal": "y"}]
    scraper = RemoteOKScraper(name="remoteok", slug="remoteok", client=_mock_client(payload))
    assert scraper.fetch() == []


def test_remoteok_tolerates_unexpected_dict_response() -> None:
    """If RemoteOK ever returns a dict (it shouldn't), we don't crash."""
    scraper = RemoteOKScraper(name="remoteok", slug="remoteok", client=_mock_client({"oops": True}))
    assert scraper.fetch() == []


# --- JSearch scraper ------------------------------------------------------


def test_jsearch_not_in_registry() -> None:
    """JSearch is intentionally NOT registered — it's instantiated directly
    in run_jsearch because it needs auth + query params get_scraper can't
    carry. Pin that design choice."""
    with pytest.raises(KeyError):
        get_scraper("jsearch", "x", "x")


def test_jsearch_parses_basic_job() -> None:
    payload = _load_fixture("jsearch_sample.json")
    scraper = JSearchScraper(
        name="jsearch:ML Data Engineer", query="ML Data Engineer", client=_mock_client(payload)
    )
    jobs = scraper.fetch()

    # 5 in fixture; "Marketing Manager" is filtered by the eng-title gate → 4.
    assert len(jobs) == 4
    j = next(j for j in jobs if j.title == "ML Data Engineer")
    # Company is per-posting and lowercased (NOT the query label).
    assert j.company == "acme corp"
    # job_id deliberately None so content-dedup works across sources.
    assert j.job_id is None
    assert j.source == Source.JSEARCH
    assert j.url == "https://example.com/apply/aaa111"
    assert "San Francisco" in j.location
    # JSearch returns the FULL state name ("California"), verified live.
    # The CA-or-remote filter matches it via the "california" token.
    assert "California" in j.location
    # HTML stripped from the description.
    assert "PySpark" in j.description
    assert "<b>" not in j.description


def test_jsearch_filters_non_engineering_titles() -> None:
    """The generous title gate drops obvious non-eng roles (Marketing Manager)."""
    payload = _load_fixture("jsearch_sample.json")
    scraper = JSearchScraper(name="j", query="q", client=_mock_client(payload))
    titles = {j.title for j in scraper.fetch()}
    assert "Marketing Manager" not in titles
    assert "ML Data Engineer" in titles
    assert "Backend Software Engineer" in titles


def test_jsearch_company_is_per_posting_not_query() -> None:
    payload = _load_fixture("jsearch_sample.json")
    scraper = JSearchScraper(name="jsearch:foo", query="foo", client=_mock_client(payload))
    companies = {j.company for j in scraper.fetch()}
    assert "acme corp" in companies
    assert "beta inc" in companies
    # Never tagged with the query label.
    assert "jsearch:foo" not in companies
    assert "foo" not in companies


def test_jsearch_stamps_remote_when_location_blank() -> None:
    """job_is_remote:true + blank city/state → location contains 'Remote'."""
    payload = _load_fixture("jsearch_sample.json")
    scraper = JSearchScraper(name="j", query="q", client=_mock_client(payload))
    jobs = scraper.fetch()
    remote = next(j for j in jobs if j.title == "ML Platform Engineer")
    assert "Remote" in remote.location


def test_jsearch_epoch_fallback_for_posted_date() -> None:
    """When job_posted_at_datetime_utc is absent, derive ISO from the epoch."""
    payload = _load_fixture("jsearch_sample.json")
    scraper = JSearchScraper(name="j", query="q", client=_mock_client(payload))
    jobs = scraper.fetch()
    epsilon = next(j for j in jobs if j.company == "epsilon labs")
    assert epsilon.posted_date is not None
    # 1778544000 → 2026-05-x in UTC.
    assert epsilon.posted_date.startswith("2026-05")


def test_jsearch_handles_non_ok_status() -> None:
    """A non-OK response yields no jobs and doesn't crash."""
    scraper = JSearchScraper(name="j", query="q", client=_mock_client({"status": "ERROR"}))
    assert scraper.fetch() == []


def test_jsearch_skips_malformed_job_without_crashing() -> None:
    """One job missing employer_name is skipped; the good one still parses."""
    payload = {
        "status": "OK",
        "data": [
            {"job_title": "Software Engineer", "job_apply_link": "u"},  # no employer_name
            {
                "employer_name": "Good Co",
                "job_title": "Software Engineer",
                "job_apply_link": "https://example.com/good",
                "job_description": "ok",
                "job_city": "Oakland",
                "job_state": "CA",
                "job_country": "US",
            },
        ],
    }
    scraper = JSearchScraper(name="j", query="q", client=_mock_client(payload))
    jobs = scraper.fetch()
    assert len(jobs) == 1
    assert jobs[0].company == "good co"


def test_jsearch_paginates_num_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """num_pages>1 issues one request per page (each capped to num_pages=1)."""
    payload = _load_fixture("jsearch_sample.json")
    client = _mock_client(payload)
    scraper = JSearchScraper(name="j", query="q", num_pages=3, client=client)
    scraper.fetch()
    # One client.get per page.
    assert client.get.call_count == 3
