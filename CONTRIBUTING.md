# Contributing

Thanks for helping! A few ground rules keep this tool trustworthy.

## Setup

```bash
make install
make check        # ruff + mypy --strict + pytest — must pass
```

## Principles

- **Claude goes through `job_pipeline/llm/`.** Feature code depends on the
  `ClaudeClient` protocol (`structured()` / `text()`), never on an SDK directly,
  so both auth modes keep working and tests can inject fakes.
- **No personal data in the repo.** Your profile lives in `profile/` (gitignored).
  Examples and test fixtures use the fictional candidate in `examples/profile/`.
- **Prompts are templates.** Describe the candidate with Jinja variables from the
  profile (`{{ candidate }}`, `{{ p.* }}`). Per-job values use single braces
  (`{title}`) and are filled with `str.replace`.
- **Honesty is a feature.** Never add prompt or code paths that invent experience,
  inflate metrics, or hide a gap.
- **Additive changes.** Prefer a new module plus a small hook over rewriting a
  working phase.

## Tests

Tests must run offline. Use the fake clients in the existing tests, and never call
the real Claude API or job boards from `pytest`.
