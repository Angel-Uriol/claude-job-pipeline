"""Tests for the natural-language → Chrome Claude run parser.

Uses a fake client (no Claude call) so we test the field mapping + the
known-vs-ad-hoc company split deterministically.
"""

from __future__ import annotations

from pathlib import Path

from job_pipeline.chrome_nl import ParsedChromeRun, parse_request
from job_pipeline.config import load_config
from tests.conftest import EXAMPLE_CONFIG

REPO = Path(__file__).resolve().parent.parent


class _FakeClient:
    def __init__(self, parsed: ParsedChromeRun) -> None:
        self._parsed = parsed
        self.calls: list[tuple[str, str]] = []

    def call(self, *, system: str, user: str) -> ParsedChromeRun:
        self.calls.append((system, user))
        return self._parsed


def _cfg():
    return load_config(EXAMPLE_CONFIG)


def test_parse_splits_known_and_ad_hoc_companies() -> None:
    """Tracked companies map to canonical no_api names; the rest are ad-hoc."""
    cfg = _cfg()
    parsed = ParsedChromeRun(
        scope="selected",
        companies=["Uber", "figma"],
        posted_within_days=7,
        score_after=True,
        extra_notes="prioritize Iceberg + Spark",
    )
    spec = parse_request("...", cfg=cfg, client=_FakeClient(parsed))
    assert spec["scope"] == "selected"
    assert spec["companies"] == ["uber"]
    assert spec["ad_hoc_companies"] == ["figma"]
    assert spec["posted_within"] == 7
    assert spec["score_after"] is True
    assert spec["extra_notes"] == "prioritize Iceberg + Spark"


def test_parse_dedupes_and_normalizes() -> None:
    """Duplicate/blank names collapse; non-positive day window → None."""
    cfg = _cfg()
    parsed = ParsedChromeRun(
        scope="mix",
        companies=["Figma", "figma", "  "],
        posted_within_days=0,
        score_after=False,
        extra_notes="  ",
    )
    spec = parse_request("...", cfg=cfg, client=_FakeClient(parsed))
    assert spec["ad_hoc_companies"] == ["Figma"]
    assert spec["companies"] == []
    assert spec["posted_within"] is None
    assert spec["score_after"] is False
    assert spec["extra_notes"] == ""


def test_parse_includes_tracked_companies_in_system_prompt() -> None:
    """The model is told which companies are already tracked."""
    cfg = _cfg()
    fake = _FakeClient(
        ParsedChromeRun(
            scope="new",
            companies=[],
            posted_within_days=None,
            score_after=True,
            extra_notes="",
        )
    )
    parse_request("find me new ML infra roles", cfg=cfg, client=fake)
    system, user = fake.calls[0]
    assert "uber" in system
    assert user == "find me new ML infra roles"
