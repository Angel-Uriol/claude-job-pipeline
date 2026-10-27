"""HTML digest renderer.

Pulls jobs from the store, partitions them by verdict, groups by company,
and renders `prompts/digest.html.j2` via Jinja2. Output goes to
`data/digests/digest-YYYY-MM-DD.html`.

The Jinja2 template is autoescaped — every interpolation passes through
`html.escape` to neutralize any JD content that contains HTML (rare, but
the scrapers don't promise their `description` is fully clean).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Config
from ..models import Job, Status, Verdict


@dataclass
class DigestData:
    """Everything the Jinja2 template needs in one bundle."""

    date_str: str
    apply_groups: list[tuple[str, list[Job]]]
    consider_groups: list[tuple[str, list[Job]]]
    no_api_prompts: list[object]  # ChromePromptEntry — kept loose to avoid circular import
    skipped: list[Job]
    applied: list[Job]
    dead_companies: set[str]
    counts: dict[str, int] = field(default_factory=dict)


# --- partition + group ----------------------------------------------------


def partition_jobs(
    jobs: list[Job],
) -> tuple[list[Job], list[Job], list[Job], list[Job]]:
    """Split jobs into (apply, consider, skipped, applied) buckets.

    - `apply`     — fit_verdict == APPLY and status not yet applied
    - `consider`  — fit_verdict == CONSIDER and status not yet applied
    - `skipped`   — pre_filter_verdict == SKIP, or fit_verdict == SKIP,
                    or status == SKIPPED
    - `applied`   — status == APPLIED

    A job that has been APPLIED is shown only in the applied bucket,
    even if its score still qualifies for apply/consider — so the
    digest doesn't badger you to re-apply.
    """
    apply_b: list[Job] = []
    consider_b: list[Job] = []
    skipped_b: list[Job] = []
    applied_b: list[Job] = []

    for j in jobs:
        if j.status == Status.APPLIED:
            applied_b.append(j)
            continue
        if j.status == Status.SKIPPED:
            skipped_b.append(j)
            continue
        if j.fit_verdict == Verdict.APPLY:
            apply_b.append(j)
        elif j.fit_verdict == Verdict.CONSIDER:
            consider_b.append(j)
        elif j.fit_verdict == Verdict.SKIP:
            skipped_b.append(j)
        # Else: scored but no verdict yet → not surfaced anywhere.

    # Sort each bucket: apply/consider by descending fit_score (best first);
    # skipped/applied alphabetical by company for predictable layout.
    apply_b.sort(key=lambda j: (-(j.fit_score or 0.0), j.company))
    consider_b.sort(key=lambda j: (-(j.fit_score or 0.0), j.company))
    skipped_b.sort(key=lambda j: (j.company.lower(), j.title.lower()))
    applied_b.sort(key=lambda j: (j.applied_at or "", j.company))

    return apply_b, consider_b, skipped_b, applied_b


def group_by_company(jobs: list[Job]) -> list[tuple[str, list[Job]]]:
    """Group jobs by company, preserving the input order's first-seen.

    Jinja2 templates want a list of tuples (not a dict) so the order is
    stable. We use `defaultdict` to gather then yield in first-seen order.
    """
    by_company: dict[str, list[Job]] = defaultdict(list)
    order: list[str] = []
    for j in jobs:
        if j.company not in by_company:
            order.append(j.company)
        by_company[j.company].append(j)
    return [(c, by_company[c]) for c in order]


# --- rendering ------------------------------------------------------------


def _build_environment(template_dir: Path) -> Environment:
    """Configure Jinja2 with autoescape on .html/.j2 files."""
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "htm", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def build_digest_data(
    cfg: Config,
    jobs: list[Job],
    no_api_prompts: list[object],
    *,
    today: date | None = None,
) -> DigestData:
    """Assemble all the inputs the digest template needs.

    `today` is overridable so tests don't depend on the system clock.
    """
    today = today or date.today()
    apply_b, consider_b, skipped_b, applied_b = partition_jobs(jobs)

    counts = {
        "apply": len(apply_b),
        "consider": len(consider_b),
        "skipped": len(skipped_b),
        "applied": len(applied_b),
        "total": len(jobs),
    }

    return DigestData(
        date_str=today.isoformat(),
        apply_groups=group_by_company(apply_b),
        consider_groups=group_by_company(consider_b),
        no_api_prompts=no_api_prompts,
        skipped=skipped_b,
        applied=applied_b,
        dead_companies={c.lower() for c in cfg.dead_companies},
        counts=counts,
    )


def build_digest_html(
    cfg: Config,
    jobs: list[Job],
    no_api_prompts: list[object],
    *,
    template_path: Path,
    today: date | None = None,
) -> str:
    """Render the full digest HTML as a string. Pure function — caller
    writes to disk."""
    env = _build_environment(template_path.parent)
    template = env.get_template(template_path.name)
    data = build_digest_data(cfg, jobs, no_api_prompts, today=today)
    return template.render(
        date_str=data.date_str,
        apply_groups=data.apply_groups,
        consider_groups=data.consider_groups,
        no_api_prompts=data.no_api_prompts,
        skipped=data.skipped,
        applied=data.applied,
        dead_companies=data.dead_companies,
        counts=data.counts,
    )
