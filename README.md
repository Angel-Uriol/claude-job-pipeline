# claude-job-pipeline

A local, Claude-powered job search that runs on your own machine. Answer a short
questionnaire (Claude asks the questions), and the pipeline will:

1. **Scrape** open roles from the companies you care about (Greenhouse, Lever and
   Ashby job boards, plus RemoteOK and optional open-web search).
2. **Score** every posting against *your* profile: a cheap Claude pre-filter, then a
   five-axis deep-fit rubric (stack · level · function · bridgeability · desirability).
3. **Digest** the results into a daily HTML report, with ready-made browser-agent
   prompts for companies that don't have a public job board.
4. **Tailor** a `.docx` resume per job, honestly. Claude can rephrase and reorder
   what's on your resume but never invents skills or changes a metric.
5. **Track** applications in a built-in tracker (status, notes, stars, the resume you
   sent).

Everything stays on your computer. Claude only sees what each step needs.

```
 ┌──────────── Setup (once) ────────────┐      ┌──────────── Daily ────────────┐
 │ connect Claude → upload resume →      │      │ scrape → pre-filter → deep-fit │
 │ Claude interview → pick companies →   │ ───▶ │ → digest → tailored resumes →  │
 │ profile/ (config + resume + targets)  │      │ tracker                        │
 └───────────────────────────────────────┘      └────────────────────────────────┘
```

## Quick start

You need **Python 3.11+**, **Node 20+** and **[uv](https://docs.astral.sh/uv/)**.

```bash
git clone https://github.com/Angel-Uriol/claude-job-pipeline.git
cd claude-job-pipeline
make install
make dev
```

Open **http://localhost:8766**. The first visit takes you to the **Setup** page:

| Step | What happens |
|---|---|
| 1. Connect Claude | Paste an Anthropic API key, **or** use your local Claude Code login (see below). |
| 2. Resume | Upload a PDF, DOCX or TXT. Claude parses it into `profile/resume_content.yaml`. |
| 3. Questionnaire | Claude interviews you one question at a time: target roles (ranked), level, locations/remote, work authorization, core skills, honest gaps, deal-breakers, dream companies. |
| 4. Companies | Claude suggests 20–40 employers that fit you; you prune or add, and the app checks which have a public job board. |
| 5. Review & save | See the three generated YAML files, then save. |

Then go to **Discover** and press **Run daily**.

Prefer the terminal? Run each step from the CLI instead:

```bash
uv run job-pipeline auth      # connect Claude
uv run job-pipeline setup     # the same questionnaire, in the terminal
uv run job-pipeline doctor    # check everything is wired up
uv run job-pipeline daily     # scrape → pre-filter → deep-fit → digest → tracker
```

## Connecting Claude

| Mode | How | Best for |
|---|---|---|
| **API key** (default) | Create a key at [console.anthropic.com](https://console.anthropic.com/settings/keys) and paste it on the Setup page. It's saved to `.env`, which is gitignored. | Everyone. Pay-as-you-go. |
| **Claude Code login** | Install [Claude Code](https://claude.com/claude-code) and run `claude` once to log in. The app drives that CLI through the [Claude Agent SDK](https://docs.claude.com/en/docs/agent-sdk/overview), so you don't paste a key. | Running the tool for yourself, on your own machine. |

> **About Claude Code login mode:** Anthropic's terms ask third-party tools to use
> API keys rather than claude.ai logins. This mode exists so you can run this
> open-source tool *for yourself, with your own Claude Code install*. Don't host this
> app for other people in this mode. If you share it, have each person use their own
> API key.

How the Claude Code mode works:
- It clears `ANTHROPIC_API_KEY` (and similar variables) from its own process, so
  the CLI uses your login instead of a key.
- Calls run one at a time with extended thinking off, which keeps them quick and
  light on your plan's limits.
- Your personal `CLAUDE.md` files are **not** loaded.

### What it costs (API key mode)

Rough numbers per run with the default models:

| Step | Model | Approx. |
|---|---|---|
| Pre-filter | Haiku-class (`llm.fast_model`) | well under 1¢ per job |
| Deep-fit | Sonnet-class | ~1–2¢ per job that passes pre-filter |
| Resume tailoring | Sonnet-class | ~2–3¢ per resume |
| Setup (one time) | both | a few cents |

The scrape filters (location, posting age, excluded titles) run **before** any LLM
call, so most postings never cost anything. Change the models in `profile/config.yaml`.

## What lives where

```
profile/                    YOUR data (gitignored) — written by Setup, edit freely
  config.yaml               profile + scrapers + filters + models
  resume_content.yaml       every bullet, summary, job, degree (source of all resumes)
  target_companies.yaml     companies to boost, by tier
.env                        API key + auth mode (gitignored)
data/                       jobs.json, tracker.db, digests (gitignored)
outputs/resumes/            generated .docx files (gitignored)

examples/profile/           a complete fictional profile — read it to see every option
prompts/                    the prompt templates (Jinja, rendered from your profile)
job_pipeline/
  llm/                      the one Claude interface: API-key client + Claude Code client
  onboarding/               Setup: interview, resume intake, company/ATS discovery, writer
  scrapers/                 Greenhouse, Lever, Ashby, RemoteOK, JSearch + scrape filters
  scoring/                  pre-filter, deep-fit, rubric
  digest/                   HTML digest + Chrome Claude prompt builder
  resume/                   spec builder → Claude tailor → Node .docx renderer → validator
  tracker_db.py             built-in SQLite application tracker (Track tab)
  ui/                       FastAPI backend (incl. setup_routes.py)
ui/                         Next.js frontend: Discover, Track, Setup
```

## The scoring rubric

Deep-fit scores each axis from 0 to 10 and combines them with these weights:

| Axis | Weight | Meaning |
|---|---|---|
| stack | 0.30 | how many of your core skills the JD asks for |
| level | 0.25 | seniority vs. your target levels |
| function | 0.20 | Tier A / B / C role type from your questionnaire |
| bridgeability | 0.15 | can you honestly bridge any gaps? (your listed honest gaps count here) |
| desirability | 0.10 | dream company / well-known / unknown |

`fit ≥ 8.0` → **apply**, `≥ 5.0` → **consider**, otherwise skip. You can change the
thresholds in config. Jobs at your target companies get a tier bump (+0.3 to +0.7).

## Optional extras

- **Open-web search (JSearch):** add a free RapidAPI key as `JSEARCH_API_KEY` in
  `.env`. Setup writes a few queries for you, and the **Search web** button runs them.
- **Chrome Claude (browser agent):** companies without a public board get a
  copy-paste prompt in the digest. Or use **Run Chrome Claude** in Discover to queue
  a run, which a Claude Code session with the
  [Claude in Chrome](https://claude.com/chrome) extension works through by following
  `prompts/chrome_claude_runbook.md`. Import the results with
  `job-pipeline import --file run.jsonl --score`.
- **Excel tracker:** `job-pipeline tracker` writes a color-coded `data/tracker.xlsx`.

## Honesty rules (built in)

These are enforced in the prompts and in code:
- Resume parsing copies your resume as written, with metrics verbatim.
- Tailoring may only rephrase and reorder. It keeps every number and bullet ID, and
  never adds a skill, tool, employer or claim.
- Scoring uses your **honest gaps** to judge bridgeability, and its "bridge notes"
  suggest truthful framing, never fabrication.
- The questionnaire records only what you actually said.

## Re-running setup

The Setup page is always in the top nav. Saving again backs up your current files
to `*.bak`. You can also edit `profile/*.yaml` by hand; the app validates them at
startup, so a typo fails loudly. To keep several profiles (for example, a friend's),
point `JOB_PIPELINE_PROFILE_DIR` at another folder.

## Development

```bash
make check          # ruff + mypy --strict + pytest
cd ui && npx tsc --noEmit && npx eslint app components lib
```

Tests run against `examples/profile/` and a fake Claude client, so they never call
the network or need a key. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
