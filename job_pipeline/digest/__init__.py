"""Digest builder — daily HTML output.

Two pieces:
- `chrome_prompt_builder` — renders the Chrome Claude prompt for each
  no-API company in config.yaml. Used by the digest's "Companies without
  API" section.
- `html_builder` — pulls jobs from the store, partitions by verdict,
  groups by company, and renders the whole digest with Jinja2.
"""

from .chrome_prompt_builder import (
    BatchPromptEntry,
    ChromePromptEntry,
    build_batched_chrome_prompts,
    build_chrome_prompt,
    build_chrome_prompts_for_config,
    load_profile,
)
from .html_builder import DigestData, build_digest_html, partition_jobs

__all__ = [
    "BatchPromptEntry",
    "ChromePromptEntry",
    "DigestData",
    "build_batched_chrome_prompts",
    "build_chrome_prompt",
    "build_chrome_prompts_for_config",
    "build_digest_html",
    "load_profile",
    "partition_jobs",
]
