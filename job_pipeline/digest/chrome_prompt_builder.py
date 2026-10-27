"""Builds the Chrome Claude prompt for every no-API company.

For each entry in `config.scrapers.no_api`, we render
`prompts/chrome_claude_template.txt` with the company name, careers URL,
and platform filled in. The digest's "Companies without API" section
then surfaces each prompt with a one-click Copy button.

The prompt itself is plain text; placeholders use Python's `str.format`
double-brace escape (`{{...}}`) where the prompt contains literal JSON
braces. We don't use Jinja2 here because the template is meant to be
trivially editable — single placeholders, no logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Config, NoApiEntry


@dataclass(frozen=True)
class ChromePromptEntry:
    """One row in the digest's Chrome-Claude section."""

    name: str
    careers_url: str
    prompt: str


@dataclass(frozen=True)
class BatchPromptEntry:
    """One batch of up to `batch_size` companies rendered as a single prompt."""

    index: int  # 1-based batch number
    companies: list[str]  # company names in this batch
    prompt: str


DEFAULT_PROFILE_FILENAME = "candidate_profile.txt"


def load_profile(
    prompts_dir: Path, *, profile_path: Path | None = None, cfg: Config | None = None
) -> str:
    """Render the single shared candidate-profile block.

    `prompts/candidate_profile.txt` is a Jinja template filled from the
    user's profile (see `job_pipeline.prompts`), so the per-company template,
    the batched template, and the scoring prompts all describe the candidate
    the same way. Without `cfg` the raw template text is returned.
    """
    p = profile_path or (prompts_dir / DEFAULT_PROFILE_FILENAME)
    if cfg is None:
        return p.read_text(encoding="utf-8").strip()
    from ..prompts import render_prompt_file

    return render_prompt_file(p, cfg).strip()


def build_chrome_prompt(
    entry: NoApiEntry, *, template: str, profile: str = ""
) -> str:
    """Render one prompt by substituting {COMPANY}, {CAREERS_URL}, {PLATFORM},
    and {PROFILE} in `template`.

    Uses Python `.format` with **kwargs. Any literal `{` / `}` in the
    template must be doubled (already done in the shipped template).
    `profile` is inserted verbatim; a template without a {PROFILE}
    placeholder simply ignores it.
    """
    return template.format(
        COMPANY=entry.name,
        CAREERS_URL=entry.careers_url,
        PLATFORM=entry.platform,
        PROFILE=profile,
    )


def build_chrome_prompts_for_config(
    cfg: Config, *, template_path: Path, profile_path: Path | None = None
) -> list[ChromePromptEntry]:
    """Build a prompt entry for every `scrapers.no_api` company in cfg.

    The shared candidate profile is read once from
    `template_path.parent / candidate_profile.txt` (override via
    `profile_path`) and injected into every prompt's {PROFILE}.

    Returns an empty list if `no_api` is empty — the digest handles
    that gracefully.
    """
    template = template_path.read_text(encoding="utf-8")
    profile = load_profile(template_path.parent, profile_path=profile_path, cfg=cfg)
    return [
        ChromePromptEntry(
            name=entry.name,
            careers_url=entry.careers_url,
            prompt=build_chrome_prompt(entry, template=template, profile=profile),
        )
        for entry in cfg.scrapers.no_api
    ]


def _chunk(items: list[NoApiEntry], size: int) -> list[list[NoApiEntry]]:
    """Split `items` into consecutive chunks of at most `size`."""
    step = max(1, size)
    return [items[i : i + step] for i in range(0, len(items), step)]


def _render_targets(entries: list[NoApiEntry]) -> str:
    """A numbered, human- + LLM-readable list of the batch's companies."""
    return "\n".join(
        f"  {n}. {e.name} — {e.careers_url} (platform: {e.platform})"
        for n, e in enumerate(entries, start=1)
    )


def build_batched_chrome_prompts(
    cfg: Config,
    *,
    template_path: Path,
    profile_path: Path | None = None,
    batch_size: int | None = None,
) -> list[BatchPromptEntry]:
    """Group `scrapers.no_api` into batches and render ONE multi-company
    prompt per batch.

    `batch_size` defaults to `cfg.chrome_claude.batch_size`. The shared
    candidate profile (one file) fills {PROFILE} in every batch prompt; the
    batch's companies fill {TARGETS}. Empty `no_api` → empty list.
    """
    size = batch_size or cfg.chrome_claude.batch_size
    template = template_path.read_text(encoding="utf-8")
    profile = load_profile(template_path.parent, profile_path=profile_path, cfg=cfg)
    batches: list[BatchPromptEntry] = []
    for idx, group in enumerate(_chunk(list(cfg.scrapers.no_api), size), start=1):
        prompt = template.format(TARGETS=_render_targets(group), PROFILE=profile)
        batches.append(
            BatchPromptEntry(
                index=idx,
                companies=[e.name for e in group],
                prompt=prompt,
            )
        )
    return batches
