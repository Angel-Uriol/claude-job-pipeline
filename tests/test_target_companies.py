"""Tests for the target-company overlay (flag + fit-score boost).

Covers the normalization/matching rules that make the target list robust
to the messy company strings scrapers produce, plus the YAML loader's
graceful degradation.
"""

from __future__ import annotations

from pathlib import Path

from job_pipeline import paths
from job_pipeline.target_companies import (
    TargetCompanyIndex,
    _normalize,
    default_target_index,
    load_target_index,
)

# --- normalization ------------------------------------------------------


def test_normalize_lowercases_and_strips_punctuation() -> None:
    # Keys are joined with no separator, so spacing variants collapse together.
    assert _normalize("Stripe, Inc.") == "stripe"
    assert _normalize("U.S. Bank") == "usbank"
    assert _normalize("  Meta   Platforms  ") == "metaplatforms"
    # Spacing/punctuation variants all fold to the same key.
    assert _normalize("Together AI") == _normalize("together.ai") == _normalize("TogetherAI")


def test_normalize_strips_corporate_suffixes_only() -> None:
    assert _normalize("Meta Platforms, Inc.") == "metaplatforms"
    assert _normalize("Block LLC") == "block"
    # Does NOT strip "ai" / "labs" — those are real parts of names here.
    assert _normalize("Scale AI") == "scaleai"
    assert _normalize("Notion Labs") == "notionlabs"


# --- matching against a small fixture -----------------------------------


def _fixture_index(tmp_path: Path) -> TargetCompanyIndex:
    yaml_text = """
boost_by_tier:
  1: 0.7
  2: 0.5
companies:
  - {name: databricks, tier: 1, aliases: []}
  - {name: amazon, tier: 1, aliases: [aws, amazon web services]}
  - {name: box, tier: 2, aliases: []}
  - {name: dropbox, tier: 2, aliases: []}
  - {name: scale ai, tier: 2, aliases: [scale]}
"""
    p = tmp_path / "targets.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return load_target_index(p)


def test_match_exact_and_case_insensitive(tmp_path: Path) -> None:
    idx = _fixture_index(tmp_path)
    assert idx.match("Databricks").canonical == "databricks"  # type: ignore[union-attr]
    assert idx.match("DATABRICKS").tier == 1  # type: ignore[union-attr]
    assert idx.match("Databricks, Inc.").canonical == "databricks"  # type: ignore[union-attr]


def test_match_via_alias(tmp_path: Path) -> None:
    idx = _fixture_index(tmp_path)
    assert idx.match("AWS").canonical == "amazon"  # type: ignore[union-attr]
    assert idx.match("Amazon Web Services").canonical == "amazon"  # type: ignore[union-attr]
    assert idx.match("scale").canonical == "scale ai"  # type: ignore[union-attr]


def test_match_is_exact_not_substring(tmp_path: Path) -> None:
    """'box' must not match 'dropbox' and vice-versa."""
    idx = _fixture_index(tmp_path)
    assert idx.match("box").canonical == "box"  # type: ignore[union-attr]
    assert idx.match("dropbox").canonical == "dropbox"  # type: ignore[union-attr]


def test_match_unknown_returns_none(tmp_path: Path) -> None:
    idx = _fixture_index(tmp_path)
    assert idx.match("Acme Widgets") is None
    assert idx.match("") is None


def test_boost_for_tier(tmp_path: Path) -> None:
    idx = _fixture_index(tmp_path)
    assert idx.boost_for(1) == 0.7
    assert idx.boost_for(2) == 0.5
    assert idx.boost_for(99) == 0.0  # unknown tier → no boost


# --- graceful degradation -----------------------------------------------


def test_missing_file_yields_empty_index(tmp_path: Path) -> None:
    idx = load_target_index(tmp_path / "nope.yaml")
    assert len(idx) == 0
    assert idx.match("Databricks") is None
    assert idx.boost_for(1) == 0.0


# --- the shipped example list ------------------------------------------


def test_default_index_loads_the_example_list() -> None:
    assert paths.target_companies_path().exists()
    default_target_index.cache_clear()
    idx = default_target_index()
    assert idx.match("Stripe").tier == 1  # type: ignore[union-attr]
    assert idx.match("Notion Labs").tier == 2  # alias  # type: ignore[union-attr]
    assert idx.match("Palantir Technologies").tier == 3  # type: ignore[union-attr]
    assert idx.match("Some Random Co") is None
    assert idx.boost_for(1) == 0.7
