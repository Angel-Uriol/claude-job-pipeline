"""Tests for the Chrome Claude prompt builder."""

from __future__ import annotations

from pathlib import Path

from job_pipeline.config import NoApiEntry, load_config
from job_pipeline.digest import (
    build_batched_chrome_prompts,
    build_chrome_prompt,
    build_chrome_prompts_for_config,
)
from tests.conftest import EXAMPLE_CONFIG

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "prompts" / "chrome_claude_template.txt"
BATCH_TEMPLATE = REPO / "prompts" / "chrome_claude_batch_template.txt"


def _make_entry(name: str = "snowflake") -> NoApiEntry:
    return NoApiEntry(
        name=name,
        careers_url="https://careers.example.com/",
        platform="workday",
    )


def test_build_prompt_substitutes_placeholders() -> None:
    """{COMPANY}, {CAREERS_URL}, {PLATFORM} get filled in."""
    template = "Search {COMPANY} at {CAREERS_URL} ({PLATFORM})."
    entry = _make_entry("snowflake")
    out = build_chrome_prompt(entry, template=template)
    assert out == "Search snowflake at https://careers.example.com/ (workday)."


def test_build_prompt_handles_literal_braces() -> None:
    """The shipped template has literal JSON braces — doubled to escape."""
    template = "Return {{json}} for {COMPANY}."
    out = build_chrome_prompt(_make_entry("nvidia"), template=template)
    assert out == "Return {json} for nvidia."


def test_shipped_template_renders_for_each_no_api_company() -> None:
    """The real template + real config should produce one prompt per
    no-API entry without raising. This is the regression gate for both
    template syntax and config shape."""
    cfg = load_config(EXAMPLE_CONFIG)
    prompts = build_chrome_prompts_for_config(cfg, template_path=TEMPLATE)

    assert len(prompts) == len(cfg.scrapers.no_api)
    for entry, prompt in zip(cfg.scrapers.no_api, prompts, strict=True):
        assert prompt.name == entry.name
        assert prompt.careers_url == entry.careers_url
        # Each rendered prompt should mention the company and the URL.
        assert entry.name in prompt.prompt
        assert entry.careers_url in prompt.prompt


def test_shipped_template_contains_jsonl_instructions() -> None:
    """Sanity: the prompt must tell Chrome Claude to return JSONL."""
    cfg = load_config(EXAMPLE_CONFIG)
    prompts = build_chrome_prompts_for_config(cfg, template_path=TEMPLATE)
    sample = prompts[0].prompt
    # The output instructions reference JSON-per-line and the required fields.
    assert "JSON" in sample.upper()
    assert "company" in sample
    assert "description" in sample


def test_empty_no_api_list_returns_empty() -> None:
    """No no-API companies configured → empty result, no crash."""
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.scrapers.no_api = []
    prompts = build_chrome_prompts_for_config(cfg, template_path=TEMPLATE)
    assert prompts == []


# --- batched (multi-company) prompts ------------------------------------


def _entries(n: int) -> list[NoApiEntry]:
    return [
        NoApiEntry(
            name=f"co{i}",
            careers_url=f"https://co{i}.example.com/",
            platform="custom",
        )
        for i in range(n)
    ]


def test_batched_prompts_chunk_by_batch_size() -> None:
    """7 companies at batch_size 3 → batches of [3, 3, 1], each company once."""
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.scrapers.no_api = _entries(7)
    batches = build_batched_chrome_prompts(
        cfg, template_path=BATCH_TEMPLATE, batch_size=3
    )
    assert [len(b.companies) for b in batches] == [3, 3, 1]
    assert [b.index for b in batches] == [1, 2, 3]
    flat = [c for b in batches for c in b.companies]
    assert flat == [f"co{i}" for i in range(7)]


def test_batched_prompt_injects_profile_and_targets() -> None:
    """Each batch prompt carries the shared profile + its own companies."""
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.scrapers.no_api = _entries(4)
    batches = build_batched_chrome_prompts(
        cfg, template_path=BATCH_TEMPLATE, batch_size=3
    )
    assert len(batches) == 2  # [3, 1]
    for b in batches:
        # Shared profile (one source) is injected into every batch prompt.
        assert "Jordan Rivera" in b.prompt
        assert "Hard skips" in b.prompt
        # The batch's companies are listed; the JSONL schema is present.
        for name in b.companies:
            assert name in b.prompt
        assert "JSON" in b.prompt.upper()
        assert "description" in b.prompt


def test_batched_uses_config_batch_size_by_default() -> None:
    """Without an override, batch_size comes from config.chrome_claude."""
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.scrapers.no_api = _entries(5)
    cfg.chrome_claude.batch_size = 2
    batches = build_batched_chrome_prompts(cfg, template_path=BATCH_TEMPLATE)
    assert [len(b.companies) for b in batches] == [2, 2, 1]


def test_per_company_template_injects_shared_profile() -> None:
    """The shipped per-company template now pulls its profile from the
    shared candidate_profile.txt via {PROFILE}."""
    cfg = load_config(EXAMPLE_CONFIG)
    prompts = build_chrome_prompts_for_config(cfg, template_path=TEMPLATE)
    assert "Jordan Rivera" in prompts[0].prompt
    assert "Hard skips" in prompts[0].prompt
