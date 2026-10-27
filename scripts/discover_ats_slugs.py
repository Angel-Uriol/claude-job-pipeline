#!/usr/bin/env python3
"""Find public job boards for the companies in profile/target_companies.yaml.

Prints a YAML snippet for each company that has a live Greenhouse, Lever or
Ashby board and isn't already in profile/config.yaml, ready to paste under
`scrapers:`. The setup questionnaire does the same thing automatically.

Usage:
    python scripts/discover_ats_slugs.py
"""

from __future__ import annotations

import re
import sys

import yaml

from job_pipeline import paths
from job_pipeline.onboarding.ats import find_boards


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def main() -> int:
    targets = yaml.safe_load(paths.target_companies_path().read_text(encoding="utf-8")) or {}
    cfg_path = paths.config_path()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    covered = {
        _flat(str(e.get(k, "")))
        for platform in ("greenhouse", "lever", "ashby")
        for e in ((cfg or {}).get("scrapers") or {}).get(platform) or []
        for k in ("name", "slug")
    }
    todo = [
        (str(e["name"]), e.get("aliases") or [])
        for e in targets.get("companies") or []
        if e.get("name") and _flat(str(e["name"])) not in covered
    ]
    print(f"Probing {len(todo)} companies ...", file=sys.stderr)
    found = [r for r in find_boards(todo) if r.platform]
    for platform in ("greenhouse", "lever", "ashby"):
        rows = [r for r in found if r.platform == platform]
        if rows:
            print(f"  {platform}:")
            for r in rows:
                print(f'    - {{ name: "{r.name}", slug: "{r.slug}" }}   # ~{r.jobs} jobs')
    missing = [name for name, _ in todo if name not in {r.name for r in found}]
    if missing:
        print(f"\nNo public board found (add under no_api): {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
