"""Target-company overlay: flag + grade-boost the companies you care about most.

The data lives in ``profile/target_companies.yaml`` so the list is editable
without touching code. This module loads that file into a fast lookup index and
answers two questions:

    * "Is this company one of the targets, and if so, which tier?"  -> ``match``
    * "How big a fit-score bump does that tier get?"                 -> ``boost_for``

Matching is strict-but-forgiving: case-insensitive, and it ignores punctuation
and common corporate suffixes ("Stripe, Inc." == "stripe"), but it is EXACT on
the normalized name so "box" never matches "dropbox". Alternate spellings are
handled by explicit ``aliases`` in the YAML, never by fuzzy matching.

Nothing here imports the rest of the pipeline, so it stays trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from . import paths

# Corporate suffix tokens stripped from the END of a company name before
# matching. Deliberately conservative — we do NOT strip "ai", "labs", or "co"
# because those are real parts of names here (Scale AI, Notion Labs, etc.).
_SUFFIX_TOKENS = frozenset(
    {"inc", "llc", "corp", "corporation", "ltd", "limited", "incorporated", "gmbh", "lp"}
)


def _normalize(name: str) -> str:
    """Fold a company name to its match key.

    Lowercase -> split on every non-alphanumeric char -> drop trailing
    corporate suffix tokens -> join with NO separator. Removing the separator
    means spacing/punctuation variants all collapse to the same key, so
    "Together AI", "together.ai", and "TogetherAI" every fold to "togetherai"
    (which is what makes matching robust against messy scraped company names).
    Exact-but-forgiving: "box" -> "box" still never equals "dropbox".
    """
    lowered = name.lower()
    cleaned = "".join(ch if ch.isalnum() else " " for ch in lowered)
    tokens = cleaned.split()
    # Peel off corporate suffixes from the end (handle "meta platforms inc").
    while tokens and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    return "".join(tokens)


@dataclass(frozen=True)
class TargetMatch:
    """A company that's on the target list."""

    canonical: str  # the YAML `name`, e.g. "databricks"
    tier: int       # 1 (strongest) .. 5


class TargetCompanyIndex:
    """In-memory lookup over the target list.

    Built once from the YAML; reads are O(1). Construct via
    ``load_target_index`` / ``default_target_index`` rather than directly.
    """

    def __init__(
        self, by_norm: dict[str, TargetMatch], boost_by_tier: dict[int, float]
    ) -> None:
        self._by_norm = by_norm
        self._boost_by_tier = boost_by_tier

    def match(self, company: str) -> TargetMatch | None:
        """Return the target match for a company name, or None if not a target."""
        if not company:
            return None
        return self._by_norm.get(_normalize(company))

    def boost_for(self, tier: int) -> float:
        """Fit-score points to add for a company in this tier (0 if unknown)."""
        return self._boost_by_tier.get(tier, 0.0)

    def __len__(self) -> int:
        return len(self._by_norm)


def load_target_index(path: str | Path | None = None) -> TargetCompanyIndex:
    """Load the target list from YAML into an index.

    A missing or empty file yields an empty index (every ``match`` returns
    None, every ``boost_for`` returns 0) so the overlay degrades to a no-op
    rather than crashing the pipeline.
    """
    p = Path(path) if path is not None else paths.target_companies_path()
    if not p.exists():
        return TargetCompanyIndex({}, {})

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    boost_by_tier = {int(k): float(v) for k, v in (raw.get("boost_by_tier") or {}).items()}

    by_norm: dict[str, TargetMatch] = {}
    for entry in raw.get("companies") or []:
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        tier = int(entry.get("tier", 0))
        match = TargetMatch(canonical=name, tier=tier)
        # Index the canonical name and every alias under their normalized form.
        # If two entries collide on a normalized key, the first one wins (and
        # the YAML is the place to resolve the ambiguity with a clearer name).
        for label in (name, *(entry.get("aliases") or [])):
            key = _normalize(str(label))
            if key:
                by_norm.setdefault(key, match)

    return TargetCompanyIndex(by_norm, boost_by_tier)


@lru_cache(maxsize=1)
def default_target_index() -> TargetCompanyIndex:
    """The shared index loaded from the default path, cached for the process.

    Used by the runner so scrape/jsearch/deep-fit don't each re-parse the YAML.
    Tests that need isolation should call ``load_target_index(tmp_path)``.
    """
    return load_target_index(paths.target_companies_path())
