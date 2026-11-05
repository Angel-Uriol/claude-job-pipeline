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

### What you need

| | What | Why |
|---|---|---|
| **Required** | A **Claude account** (Pro or Max), or an Anthropic API key | Claude reads jobs, scores them and tailors your resume |
| **Required** | A Mac or Windows computer | The app runs locally; your data stays on it |
| Installed for you | Python, Node.js, the resume builder, **Claude Code** | Setup's **Set up everything** button installs whatever is missing |
| Optional | **Google Chrome** + the [Claude in Chrome](https://claude.ai/chrome) extension | Premium searches of company careers sites in your browser |

### The easy way (no terminal)

1. On GitHub, click **Code → Download ZIP**, then unzip it anywhere.
2. Double-click **Start.command** (Mac) or **Start.bat** (Windows).
   - **Mac, first time only:** macOS blocks files downloaded from the internet. Right-click
     `Start.command` → **Open** → **Open**. After that a normal double-click works.
   - **Windows:** if SmartScreen appears, click **More info → Run anyway**.
3. The first start installs everything it needs into the folder (a few minutes). Later
   starts take seconds. Your browser opens at **http://localhost:8766**.
4. Leave the small window open while you use the app; close it to stop.

Want to look around first? On the Setup page click **Try the demo** — it loads made-up
jobs so you can explore before signing in.

### For developers

You need **Python 3.11+**, **Node 20+** and **[uv](https://docs.astral.sh/uv/)**.

```bash
git clone https://github.com/Angel-Uriol/claude-job-pipeline.git
cd claude-job-pipeline
make start          # installs what's missing, then starts the app
```

Open **http://localhost:8766**. The first visit takes you to the **Setup** page:

| Step | What happens |
|---|---|
| 1. Before you start | Lists everything the app needs. **Set up everything** installs what's missing (the resume builder, and Claude Code via Anthropic's official installer), then moves you on to sign in. Chrome and the Claude in Chrome extension are linked, since they can't be installed from the app. |
| 2. Connect Claude | Sign in with your Claude account (default), **or** paste an Anthropic API key under Advanced (see below). |
| 3. Your profile | One chat with Claude, mostly tap-to-answer options. It starts with "Do you have a resume?" — upload it right in the chat, or say no and Claude builds one with you (industry, experience, projects). Then three rounds: **About you** → a live mini-pipeline (scrape the job boards → apply your location/title rules → Claude rates the top candidates → shows the best with reasons) → **What the market asks** ("3 of the 8 roles I found ask for Kafka — have you used it?") → a second, sharper scan → **Sharpen**. |
| 4. Companies | Pre-filled with the employers found during the scans (job boards already verified); trim, add, re-check. |
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
| **Sign in with Claude** (default) | Click **Install** on step 1 (or install [Claude Code](https://claude.com/claude-code) yourself), then **Log in with Claude**: your browser opens for the Claude login, and when it says "You're all set" the page continues by itself (it runs `claude auth login` for you; the CLI keeps the sign-in in your system keychain). The app drives that CLI through the [Claude Agent SDK](https://docs.claude.com/en/docs/agent-sdk/overview), so you don't paste a key. | Claude Pro/Max users running the tool for themselves, on their own machine. No extra bill; counts against your plan's limits. |
| **API key** (Advanced) | Create a key at [console.anthropic.com](https://console.anthropic.com/settings/keys) and paste it on the Setup page. It's saved to `.env`, which is gitignored. | No Claude plan, separate billing, or sharing the app with others. Pay-as-you-go. |

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
call, so most postings never cost anything. Each scoring phase also stops at
`scoring.max_jobs_per_run` (default 40) jobs per run — target companies and the
newest postings first — so a big first scrape can't run up a surprise bill; run
again for the next batch. Change models and the cap in your profile's `config.yaml`.

## What lives where

```
profiles/<name>/            ONE PERSON'S data (gitignored) — written by Setup, edit freely
  config.yaml               profile + scrapers + filters + models
  resume_content.yaml       every bullet, summary, job, degree (source of all resumes)
  target_companies.yaml     companies to boost, by tier
  data/                     jobs.json, tracker.db, digests
  outputs/resumes/          generated .docx files
.env                        API key + auth mode (gitignored, shared by all profiles)

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

## Profiles — one complete memory per person

Several people (or several job searches) can share one install. Each **profile**
is its own folder under `profiles/` with that person's config, resume, target
companies, setup chat **and history** — Discover inbox, tracker of what they
applied to, digests and generated resumes. Nothing is shared between profiles
except your Claude connection.

- **Switch** from the chip in the top bar, or manage everything on the
  **Profiles** page (create, rename, delete).
- **Export** a profile to one JSON file (optionally with its applied-jobs
  history) and **import** it on another machine — it's restored exactly.
- **Import any other JSON about a person** (a resume export, notes, an old
  context file): Claude reads it and pre-fills a new profile's setup chat, which
  then only asks about what's missing.

```bash
uv run job-pipeline profiles list
uv run job-pipeline profiles create "Sam Lee"
uv run job-pipeline profiles use sam-lee
uv run job-pipeline profiles export sam-lee -o sam.json
uv run job-pipeline profiles import sam.json
uv run job-pipeline --profile sam-lee daily      # run one command as Sam
```

Upgrading from a version with a single `profile/` folder? It's moved into
`profiles/` automatically on first start.

## Already applied? It won't come back

The Track tab is the record of what you've acted on, and the pipeline respects it:
- Scrapes, open-web search and Chrome Claude imports **skip any job already in
  your tracker**. That includes re-posts of the same role at the same company
  under a new job ID.
- Moving a job to *applied*, *rejected* or *withdrawn* on the Track tab updates
  the matching Discover row, so it leaves your inbox.
- Jobs you dismiss in Discover are remembered too (`data/seen_jobs.json`), so a
  later scrape doesn't bring them back.

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
- **✨ Premium search (Claude in Chrome), launched by the app:** on the Companies
  step pick companies (by default the ones with no public job board) and queue a
  premium search; then **Check Chrome connection** → **Launch** (or the ✨ button on
  Discover). The app starts your Claude Code CLI with `--chrome`, and Claude browses
  each company's own careers site in your Chrome, live progress on Discover
  ("Visiting careers.nvidia.com…"). Found roles are imported into the profile
  (skipping ones you already track) and scored. It's the most thorough search but
  uses many more tokens. Safety: the session may only use Chrome's browsing tools —
  no shell, no file writes, no forms or uploads — and is told never to log in,
  apply or contact anyone.
  **The app recommends it for you.** A ✨ suggestions card on Discover (and a
  "Premium read" button on any job) proposes premium search only where it adds
  something: companies with no public job board, dream companies whose board
  returned nothing for you, and strong matches whose description looks cut off
  (or, if you need sponsorship, doesn't mention it). Before you launch it says
  what it costs in your terms: on a Claude Code login, your current 5-hour /
  weekly plan usage (when Claude exposes it) and how much of a session a
  premium search uses — measured on your own past runs; on an API key, a dollar
  estimate learned from your past runs.
  *Needs:* the Claude Code CLI with a **full Claude sign-in** (Setup → Connect
  Claude → "Log in with Claude" / "Sign in for Chrome", which runs
  `claude auth login`), and the [Claude in Chrome](https://claude.ai/chrome)
  extension signed into the same account, with Chrome open. Setup's "Connect
  Chrome" checklist checks each one and tests the connection.
  *Why the full sign-in:* Claude Code reaches the extension through Anthropic's
  browser bridge, and the bridge rejects the quick `claude setup-token` token
  ("OAuth token forbidden (403) — missing scope"). Claude Desktop being open
  doesn't get in the way.
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

## Compare recommendations and prepare for interviews

**Compare** (also linked from Discover) researches up to five final `apply` or
`consider` recommendations. It shows salary, benefits and company reviews as
**below / around / above market / unknown**, with confidence, source links and
comparison limits. Comparable location, level, currency and pay period matter;
a saved posting alone cannot establish a market rating. Interview and hiring
prospects are separate **qualitative signals**, with reasons, barriers and next
steps — not calibrated probabilities or a conversion of the fit score.

After marking a tracked application **applied**, use its **Interview prep** link
(or the Interview prep tab). Choose the stage and days available. The report
includes company/role research, process information to confirm, practice and
source-backed reported questions, truthful answer outlines, original resume
bullet evidence, a preparation schedule and questions to ask the interviewer.
Missing experience stays a gap; suggested practice is never presented as a
prediction of the actual questions.

Research is on demand, not part of the daily pipeline. It uses the existing
Claude connection: API web search in API-key mode, or bounded WebSearch/WebFetch
through the Agent SDK in Claude Code mode. No Chrome extension is needed.
Each selected job allows at most six searches (and eight page fetches in Code
mode); the structured analysis is a separate model call. Usage consumes API
credits or plan capacity, as shown before starting. Candidate identity, resume
and application notes are excluded from public search queries; profile and
resume evidence are supplied to Claude only for the subsequent analysis.

Reports are timestamped snapshots saved under `profiles/<slug>/data/research/`.
They can be reopened from the workspace and refreshed by starting another run.
They do not overwrite fit scores or tracker history. Live-research failures
produce explicit warnings and limited-evidence output, or a retryable failure.
Evidence rules, checked in code (not just asked of the model):
- Citations must point to sources actually retrieved in that run (or the saved
  posting) and to your real resume bullets.
- Sources are labelled **web page** (text was read) or **search listing** (a
  link and title only — proves the page exists, not what it says). Failed page
  fetches are dropped and reported.
- A salary / benefits / reviews rating needs a stated comparison cohort, a cited
  web page, and a short **exact quote** that really appears in that page's
  stored text. Otherwise it's shown as *unknown — not verified* with the reason.
  A saved posting or a search listing alone never sets a market rating.
- A "reported" interview question needs a readable source; otherwise it's shown
  as practice. Any figure in an answer outline that isn't in your resume or
  profile is flagged "check before using".
This still isn't a guarantee every interpretation is right — read the quotes
and sources, especially self-selected reviews and interview anecdotes.

Saved reports travel with **profile export/import** (with history): finished
reports are included and validated on import; running ones are skipped. Tests
use fictional profiles and mocked research, never live Claude calls.
