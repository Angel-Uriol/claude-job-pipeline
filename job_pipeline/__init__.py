"""job-pipeline-v2 — automated job-search pipeline.

Package entry points:
    job_pipeline.cli       — argparse-based command runner
    job_pipeline.config    — config.yaml loader + .env secrets
    job_pipeline.models    — Job, Company, ScoreResult, enums
    job_pipeline.store     — atomic jobs.json read/write + dedup
"""

__version__ = "2.0.0"
