"""Command-line entry point.

Every pipeline phase is a subcommand. They're all idempotent: re-running
`scrape` doesn't lose scoring, re-running `pre-filter` only re-scores
jobs that haven't been scored yet, etc.

Invocation patterns (both work):
    python -m job_pipeline.cli <subcommand> [...]
    job-pipeline <subcommand> [...]                # via console_scripts

First-time users: `job-pipeline auth` (connect Claude), then
`job-pipeline setup` (questionnaire), then `job-pipeline daily`.
`job-pipeline doctor` checks that everything is wired up.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__, paths
from .config import Secrets, load_config, load_secrets
from .handoff import run_handoff
from .importers import import_chrome_claude_jsonl
from .llm import llm_available
from .models import Job
from .resume import generate_resume, load_resume_content
from .runner import (
    run_clean_discover,
    run_deep_fit,
    run_digest,
    run_jsearch,
    run_pre_filter,
    run_scrape,
    run_tracker,
)
from .store import JobStore

# Type alias for subcommand handlers. Each returns a Unix-style exit code.
CommandHandler = Callable[[argparse.Namespace], int]

log = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    """Configure root logger so scrape output is readable in the terminal."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _prompts_dir() -> Path:
    """`<repo-root>/prompts/`, regardless of cwd."""
    return paths.PROMPTS_DIR


def _llm_ready(secrets: Secrets) -> bool:
    """Log a helpful error and return False if Claude isn't reachable."""
    if llm_available(secrets):
        return True
    if secrets.auth_mode == "claude_code":
        log.error("Claude Code CLI not found. Install it, run `claude` once, or "
                  "switch to an API key with `job-pipeline auth`.")
    else:
        log.error("ANTHROPIC_API_KEY is empty. Run `job-pipeline auth` (or the "
                  "Setup page in the web UI) to connect Claude.")
    return False


# --- pipeline phase wrappers (delegate to runner.py) -------------------
#
# Each wrapper is intentionally thin: load config + store, call the
# matching `run_*` function from runner.py (which is the SAME code path
# the UI uses), save the store, log the counts. Behavior is unchanged
# from the pre-refactor version — only the inner loops moved.


def cmd_scrape(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    def progress_log(event: object) -> None:
        # CLI surfaces progress via the existing logger; the UI uses SSE.
        log.info("%s", getattr(event, "message", event))

    counts = run_scrape(cfg, store, progress=progress_log)
    store.save()
    log.info(
        "Scrape done: inserted=%d updated=%d filtered=%d excluded=%d "
        "deduped=%d errors=%d",
        counts["inserted"],
        counts["updated"],
        counts["filtered"],
        counts["excluded"],
        counts.get("deduped", 0),
        counts["errors"],
    )
    return 0


def cmd_jsearch(args: argparse.Namespace) -> int:
    """Run the on-demand JSearch open-web discovery queries.

    Unlike pre-filter/deep-fit, a missing JSEARCH_API_KEY does NOT abort —
    run_jsearch warns and returns zeros, because JSearch is one optional
    source, not a required phase.
    """
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    secrets = load_secrets()
    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    def progress_log(event: object) -> None:
        log.info("%s", getattr(event, "message", event))

    counts = run_jsearch(cfg, store, secrets=secrets, progress=progress_log)
    store.save()
    log.info(
        "JSearch done: inserted=%d updated=%d filtered=%d deduped=%d "
        "errors=%d requests=%d",
        counts["inserted"],
        counts["updated"],
        counts["filtered"],
        counts.get("deduped", 0),
        counts["errors"],
        counts.get("requests", 0),
    )
    return 0


def cmd_pre_filter(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    secrets = load_secrets()
    if not _llm_ready(secrets):
        return 1

    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    def progress_log(event: object) -> None:
        log.info("%s", getattr(event, "message", event))

    counts = run_pre_filter(
        cfg,
        secrets,
        store,
        force=args.force,
        progress=progress_log,
        prompt_path=_prompts_dir() / "pre_filter.txt",
    )
    store.save()
    log.info(
        "Pre-filter done: passed=%d skipped_llm=%d skipped_rule=%d errors=%d",
        counts["passed"],
        counts["skipped_llm"],
        counts["skipped_rule"],
        counts["errors"],
    )
    return 0


def cmd_deep_fit(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    secrets = load_secrets()
    if not _llm_ready(secrets):
        return 1

    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    def progress_log(event: object) -> None:
        log.info("%s", getattr(event, "message", event))

    counts = run_deep_fit(
        cfg,
        secrets,
        store,
        force=args.force,
        progress=progress_log,
        prompt_path=_prompts_dir() / "deep_fit.txt",
    )
    store.save()
    log.info(
        "Deep-fit done: apply=%d consider=%d skip=%d errors=%d",
        counts["apply"],
        counts["consider"],
        counts["skip"],
        counts["errors"],
    )
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    out_path = run_digest(
        cfg,
        store,
        template_path=_prompts_dir() / "digest.html.j2",
        chrome_template_path=_prompts_dir() / "chrome_claude_template.txt",
    )
    log.info("Digest written to %s (%d jobs total)", out_path, len(store.jobs))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Ingest a Chrome Claude JSONL output file into jobs.json.

    Each line is one JSON object (company/title/url/description required).
    Imported jobs land with `source=chrome_claude` and `status=new`.

    With `--score`, immediately chain pre-filter + deep-fit so the imported
    jobs land actionable (apply/consider) instead of waiting for the next
    scoring run — this is what the Chrome Claude watcher loop uses.
    """
    _setup_logging(args.verbose)
    cfg = load_config(args.config)

    input_path = Path(args.file)
    if not input_path.exists():
        log.error("File not found: %s", input_path)
        return 1

    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    stats = import_chrome_claude_jsonl(input_path, store)
    store.save()

    log.info(
        "Import done: inserted=%d updated=%d excluded=%d malformed=%d (total seen=%d)",
        stats.inserted,
        stats.updated,
        stats.excluded,
        stats.skipped_malformed,
        stats.total_seen,
    )
    if stats.errors and args.verbose:
        for err in stats.errors[:20]:  # cap noise — first 20 is plenty
            log.warning("  %s", err)

    # Optional: score the freshly imported jobs in one shot.
    if getattr(args, "score", False):
        secrets = load_secrets()
        if not _llm_ready(secrets):
            log.warning("--score requested but Claude isn't connected; skipping scoring.")
            return 0

        def progress_log(event: object) -> None:
            log.info("%s", getattr(event, "message", event))

        pf = run_pre_filter(
            cfg, secrets, store,
            progress=progress_log, prompt_path=_prompts_dir() / "pre_filter.txt",
        )
        store.save()
        log.info("Pre-filter (post-import): passed=%d", pf.get("passed", 0))
        df = run_deep_fit(
            cfg, secrets, store,
            progress=progress_log, prompt_path=_prompts_dir() / "deep_fit.txt",
        )
        store.save()
        log.info(
            "Deep-fit (post-import): apply=%d consider=%d skip=%d",
            df.get("apply", 0), df.get("consider", 0), df.get("skip", 0),
        )
    return 0


def cmd_tracker(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    out_path = run_tracker(cfg, store)
    log.info("Tracker written to %s (%d rows)", out_path, len(store.jobs))
    return 0


def cmd_clean_discover(args: argparse.Namespace) -> int:
    """Archive the Discover inbox: dismiss every current job + seed the seen-ledger."""
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    def progress_log(event: object) -> None:
        log.info("%s", getattr(event, "message", event))

    counts = run_clean_discover(cfg, store, progress=progress_log)
    store.save()
    log.info(
        "Clean discover done: dismissed=%d already_dismissed=%d ledger_size=%d",
        counts["dismissed"],
        counts["already_dismissed"],
        counts["ledger_size"],
    )
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    """Push apply/consider jobs (+ tailored resumes) into the built-in tracker."""
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    def progress_log(event: object) -> None:
        log.info("%s", getattr(event, "message", event))

    counts = run_handoff(cfg, store, progress=progress_log)
    log.info(
        "Handoff done: exported=%d imported=%d%s",
        counts.get("exported", 0),
        counts.get("imported", 0),
        f" error={counts['error']}" if counts.get("error") else "",
    )
    return 1 if counts.get("error") else 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Generate a tailored .docx resume for one job.

    Picks the resume family from the job's pre-filter extraction (or
    `--family <name>` if you want to override), builds a JSON spec,
    invokes the Node renderer, validates the output, and stamps
    `job.resume_path` so the digest and tracker can link to it.
    """
    _setup_logging(args.verbose)
    cfg = load_config(args.config)

    store = JobStore(cfg.paths.jobs_json, excluded_companies=cfg.excluded_companies)
    store.load()

    # Find the job. `--job-id` matches the ATS-provided job_id when set,
    # otherwise we try a dedup-key fallback so Chrome-Claude-imported
    # jobs without a job_id are still addressable.
    target: Job | None = None
    for j in store.jobs:
        if j.job_id == args.job_id:
            target = j
            break
        if j.dedup_key()[1] == args.job_id:
            target = j
            break

    if target is None:
        log.error("No job found with id/key matching %r", args.job_id)
        return 1

    content = load_resume_content(paths.resume_content_path())

    # Optional family override.
    family = None
    if args.family:
        from .resume.families import RESUME_FAMILIES

        if args.family not in RESUME_FAMILIES:
            log.error(
                "Unknown family %r. Known: %s",
                args.family,
                ", ".join(RESUME_FAMILIES),
            )
            return 1
        family = RESUME_FAMILIES[args.family]

    # Build the Claude tailorer if enabled + Claude reachable (else None →
    # deterministic render).
    from .prompts import render_prompt
    from .resume import build_resume_tailorer

    tailorer = build_resume_tailorer(
        enabled=cfg.resume.tailor_enabled,
        model=cfg.resume.model,
        prompt_template=render_prompt("resume_tailor.txt", cfg),
    )
    if tailorer is None and cfg.resume.tailor_enabled:
        log.warning("Claude not connected — rendering deterministic resume (no tailoring).")

    try:
        out_path = generate_resume(
            target, content, cfg.paths.resumes_dir, family=family, tailorer=tailorer
        )
    except Exception as e:
        log.error("Resume generation failed: %s", e)
        return 1

    # Stamp the path on the job and save so the tracker/digest link to it.
    target.resume_path = str(out_path.resolve())
    store.save()

    log.info("Resume: %s", out_path.resolve())
    print(out_path.resolve())
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the local web UI at http://127.0.0.1:<port>.

    Imports uvicorn lazily so the rest of the CLI doesn't pay the
    import cost on every invocation.
    """
    import uvicorn

    from .ui import create_app

    _setup_logging(args.verbose)
    app = create_app(config_path=args.config)
    log.info("Starting UI on http://%s:%d", args.host, args.port)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info" if args.verbose else "warning",
    )
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    """Run the full daily pipeline: scrape → pre-filter → deep-fit → digest → tracker.

    Each step is a normal subcommand invocation; we just chain them with
    a synthesized Namespace so the per-step argparse logic stays in one
    place. Stops at the first non-zero return code — if `pre-filter`
    fails because Claude isn't connected, we don't waste time running
    deep-fit and beyond.
    """
    _setup_logging(args.verbose)
    log.info("=== Daily pipeline run ===")

    # Synthesize a namespace covering every flag the sub-commands read.
    sub_args = argparse.Namespace(
        verbose=args.verbose,
        config=args.config,
        force=False,  # daily never re-scores; use the standalone commands for that
    )

    steps: list[tuple[str, CommandHandler]] = [
        ("scrape", cmd_scrape),
        ("pre-filter", cmd_pre_filter),
        ("deep-fit", cmd_deep_fit),
        ("digest", cmd_digest),
        ("tracker", cmd_tracker),
    ]

    for name, fn in steps:
        log.info("--- %s ---", name)
        rc = fn(sub_args)
        if rc != 0:
            log.error("%s exited with code %d — stopping daily pipeline.", name, rc)
            return rc

    log.info("=== Daily pipeline complete ===")
    return 0


# --- first-run commands (implemented in onboarding/terminal.py) ----------


def cmd_auth(args: argparse.Namespace) -> int:
    from .onboarding.terminal import run_auth

    return run_auth(mode=args.mode)


def cmd_setup(args: argparse.Namespace) -> int:
    _setup_logging(args.verbose)
    from .onboarding.terminal import run_setup

    return run_setup(resume_path=args.resume, restart=args.restart)


def cmd_doctor(args: argparse.Namespace) -> int:
    from .onboarding.doctor import run_doctor

    return run_doctor()


# --- parser ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the full argparse tree.

    Kept as a separate function so tests can introspect the parser without
    actually invoking the CLI.
    """
    parser = argparse.ArgumentParser(
        prog="job-pipeline",
        description=f"Automated job-search pipeline (v{__version__})",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: profile/config.yaml)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"job-pipeline {__version__}",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )

    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")

    # --- first-run setup ---------------------------------------------
    p = sub.add_parser("auth", help="connect Claude (API key or your Claude Code login)")
    p.add_argument("--mode", choices=["api_key", "claude_code"], default=None,
                   help="skip the prompt and pick a mode")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("setup", help="LLM questionnaire that builds your profile")
    p.add_argument("--resume", default=None, help="path to your resume (PDF/DOCX/TXT)")
    p.add_argument("--restart", action="store_true", help="discard a saved, unfinished interview")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("doctor", help="check Python, Node, Claude auth and your profile")
    p.set_defaults(func=cmd_doctor)

    # scrape
    p = sub.add_parser("scrape", help="pull jobs from configured Greenhouse/Lever/Ashby boards")
    p.set_defaults(func=cmd_scrape)

    # jsearch — on-demand open-web discovery (RapidAPI). Separate from
    # scrape because the free tier is quota-limited.
    p = sub.add_parser(
        "jsearch",
        help="run on-demand JSearch open-web queries (needs JSEARCH_API_KEY)",
    )
    p.set_defaults(func=cmd_jsearch)

    # pre-filter
    p = sub.add_parser("pre-filter", help="run the fast Claude pre-filter on new jobs")
    p.add_argument(
        "--force",
        action="store_true",
        help="re-score jobs even if they already have a pre-filter verdict",
    )
    p.set_defaults(func=cmd_pre_filter)

    # deep-fit
    p = sub.add_parser("deep-fit", help="run Claude deep-fit scoring on pre-filter passes")
    p.add_argument(
        "--force",
        action="store_true",
        help="re-score jobs even if they already have a fit_score",
    )
    p.set_defaults(func=cmd_deep_fit)

    # digest
    p = sub.add_parser("digest", help="generate the HTML digest for today's run")
    p.set_defaults(func=cmd_digest)

    # import
    p = sub.add_parser("import", help="ingest Chrome Claude JSON output into the pipeline")
    p.add_argument(
        "--file",
        required=True,
        help="path to JSONL file produced by the Chrome Claude prompt",
    )
    p.add_argument(
        "--score",
        action="store_true",
        help="run pre-filter + deep-fit immediately after import",
    )
    p.set_defaults(func=cmd_import)

    # tracker
    p = sub.add_parser("tracker", help="sync the Excel tracker from jobs.json")
    p.set_defaults(func=cmd_tracker)

    # clean-discover — archive the inbox + seed the seen-ledger before a fresh run
    p = sub.add_parser(
        "clean-discover",
        help="dismiss every current job + record their keys so a re-scrape won't resurface them",
    )
    p.set_defaults(func=cmd_clean_discover)

    # handoff — final step: push apply/consider jobs into the built-in tracker
    p = sub.add_parser(
        "handoff",
        help="push apply/consider jobs + resumes into the Track tab's database",
    )
    p.set_defaults(func=cmd_handoff)

    # resume
    p = sub.add_parser("resume", help="generate a tailored .docx resume for one job")
    p.add_argument("--job-id", required=True, help="job_id (or dedup key) of the target role")
    p.add_argument(
        "--family",
        default=None,
        help="override the family classifier (default: software_engineer)",
    )
    p.set_defaults(func=cmd_resume)

    # daily
    p = sub.add_parser(
        "daily",
        help="run scrape → pre-filter → deep-fit → digest → tracker in order",
    )
    p.set_defaults(func=cmd_daily)

    # serve (FastAPI backend; Next.js dev server runs separately on 8766)
    p = sub.add_parser("serve", help="launch the FastAPI backend (Next.js UI talks to this)")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765, help="bind port (default 8765)")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args and dispatch to the chosen subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: CommandHandler = args.func
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
