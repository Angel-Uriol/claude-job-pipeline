# Chrome Claude runbook — how the watcher loop drains the queue

This is the procedure a **Claude Code agent session** follows to execute
"Run Chrome Claude" requests that the web UI button queued. The button can't
drive your browser; only an agent session (which holds the Claude-in-Chrome
connection) can. So the button enqueues a request and this runbook drains it.

**Nothing here is hardcoded data.** The companies live in `profile/config.yaml`
(`scrapers.no_api`); your profile, target tiers, and hard-skip rules live in
**one** file, `prompts/candidate_profile.txt`; the output schema lives in the
templates (`chrome_claude_template.txt` for a single company,
`chrome_claude_batch_template.txt` for a batch). Knobs live in `profile/config.yaml`
under `chrome_claude`: `batch_size`, `max_parallel_batches`,
`max_roles_per_company`, and `exclude_companies` (companies to skip in the
browser search only). This file is only the *procedure* — edit it to change how
runs are executed, not what counts as a match.

## Prerequisites
- The Claude-in-Chrome extension is connected (the desktop-app session has it).
- Run shell commands **from the repo root** so `profile/config.yaml` and the
  relative `data/` paths resolve. Use the repo venv: `.venv/bin/python`
  (shown as `$PY`).

## How to run it
- **As a watcher loop (closest to one-click):**
  `/loop 30s drain any pending chrome-claude run requests by following prompts/chrome_claude_runbook.md`
  — each tick claims and runs every pending request, then waits.
- **Once, on demand:** just say "run chrome claude" and follow the steps below a single time.

## Drain procedure (repeat until no pending requests)

1. **Claim the next request:**
   `$PY -m job_pipeline.chrome_queue claim`
   - Prints a JSON record and flips it to `running`, or prints `null`.
   - `null` → nothing queued; stop this tick.

2. **Read the record's fields:** `id`, `scope`, `companies`, `ad_hoc_companies`,
   `posted_within`, `score_after`, `extra_notes`.

3. **Resolve targets** from `profile/config.yaml` → `scrapers.no_api`:
   - `scope = selected` → entries whose `platform != "linkedin_search"`. If
     `companies` is non-empty, keep only those `name`s; otherwise all of them.
   - `scope = new` → entries whose `platform == "linkedin_search"` (these are
     LinkedIn discovery searches that surface *new* companies).
   - `scope = mix` → both of the above.
   - **`ad_hoc_companies`** (named in the request but not in config): include
     each as a target with no known careers URL — you'll find its careers page
     in step 4. Add these regardless of scope.
   - **Drop any company in `profile/config.yaml` → `chrome_claude.exclude_companies`**
     (case-insensitive) — off-limits for the browser search even if a discovery
     result or the request names them.

   Then read the caps from `profile/config.yaml` → `chrome_claude` (`batch_size`,
   `max_parallel_batches`, `max_roles_per_company`) and split the resolved
   companies into **batches of ≤ `batch_size`**.

4. **Run batches across parallel tabs** via the claude-in-chrome MCP. Keep up
   to `max_parallel_batches` batches in flight at once — one tab per batch:
   - Open a tab per active batch with `tabs_create_mcp` (defaults: 3 tabs × ≤3
     companies = up to **9 companies in flight**).
   - **Interleave** the tabs: fire the first `navigate` in every active tab, then
     cycle through them reading/extracting whichever finished loading — don't
     block on one slow page. When a tab finishes a company, move it to the next.
   - Treat `prompts/candidate_profile.txt` as the persona + Tier rubric + hard
     skips, and the template's schema as the output contract — don't restate them.

   **For each company, do a thorough MULTI-SOURCE search (not just one URL):**
   - **Read the board's public ATS endpoint instead of fighting its rendered page**
     when you can — see *Appendix: ATS endpoint cookbook*. Most career boards
     (Greenhouse, Lever, Workday, SmartRecruiters, Ashby) load their jobs from a
     public JSON endpoint that you navigate to / read **inside the same Chrome tab**.
     It's still 100% browser + public data, just far faster and more reliable than
     clicking cards — and it's the only thing that works on heavy custom SPAs
     (Microsoft, DoorDash, Uber, Netflix). Fall back to reading the rendered careers
     page, then Google Jobs / LinkedIn, only when no endpoint is available.
   - Check several sources and cross-verify: the **official careers page** (its
     `careers_url` if known, else find it), **Google Jobs**, **LinkedIn**
     filtered to that company, and the company on **Greenhouse / Lever / Ashby**
     (`site:greenhouse.io {company}` etc.); use **BuiltIn / Indeed** to
     cross-check. Ad-hoc companies (no careers URL): find the company's OFFICIAL
     careers page first (prefer its own domain); if you can't confirm it's
     official, skip + log it. (You may offer to add a confirmed careers URL to
     `profile/config.yaml` `no_api` afterward — ask first.)
   - **Read the FULL result list**, not just the top few — promoted/staffing
     reposts cluster at the top, so scroll past them. Keep up to
     `max_roles_per_company` of the **strongest** matches, ordered by fit.
   - **Verify each role live** on a page you actually opened, and capture its
     working apply URL. Never invent roles or URLs.
   - **Apply the hard filters AS YOU GO** (drop, don't import):
     - *location* — follow the candidate profile's location rule (in the
       request's prompt); skip postings that break it.
     - *level* — skip titles with the profile's excluded words unless the JD body
       clearly reads as the candidate's target level. Read the JD's stated years/level, not just the title
       (e.g. catch a "Staff" role titled "AI Engineer 6", or a "12+ years" role).
     - *visa* — skip roles requiring US citizenship/clearance, and (if the profile
       says the candidate needs sponsorship) roles stating no sponsorship.
     - *exclude list* — skip `chrome_claude.exclude_companies`.
   - **Capture the FULL JD verbatim** into `description` (responsibilities +
     requirements + qualifications) — it feeds the scorer AND your résumé
     generator. Do NOT summarize.
   - Honor `posted_within` (day window; if a posting has no date, include it with
     `posted_date` null) and `extra_notes` (extra emphasis — never overrides a
     hard skip).

5. **Anti-bot / login walls / CAPTCHA — never bypass.** If a target shows a
   login wall, bot check, or CAPTCHA, **skip that target**, note it, and move
   on. Do not attempt to solve or evade it. (LinkedIn search URLs often hit
   this; that's expected — the API-based `jsearch` phase is the fallback for
   open-web discovery.)

6. **Write the output** as JSONL (one JSON object per line) to
   `data/chrome_runs/<id>.jsonl`, matching the importer's schema. Required
   per line: `company`, `title`, `url`, `description`. Optional: `job_id`,
   `team`, `location`, `posted_date` (use `null`, not `""`, when unknown).

   **Outreach (separate file).** Also write `data/chrome_runs/<id>-outreach.md`
   with **1–2 public professional contacts per company** that has matches — a
   recruiter or an engineering manager near the relevant team. Per contact:
   name, title, team, LinkedIn URL, a one-line "why this person", and a
   suggested opening line for the candidate to send. **Guardrails:** public professional
   info only (no emails, phone numbers, or photos); **never fabricate** — if you
   can't verify someone, write "NOT FOUND" and why; you only *suggest* an opener
   and **never message, connect with, or contact anyone**. Keep this OUT of the
   JSONL (the importer schema is jobs only).

7. **Import** the results (reuses the existing pipeline, no new logic).
   - If the record's `score_after` is **true**, import AND score in one command
     (pre-filter + deep-fit run automatically on the freshly imported jobs):
     `$PY -m job_pipeline.cli import --file data/chrome_runs/<id>.jsonl --score`
   - If `score_after` is **false**, just import; the next scheduled scoring run
     picks them up:
     `$PY -m job_pipeline.cli import --file data/chrome_runs/<id>.jsonl`
   - Note the `inserted / updated / excluded / malformed` counts it prints.

8. (Optional) refresh the digest / Excel tracker if you want them current:
   `$PY -m job_pipeline.cli digest` and/or `$PY -m job_pipeline.cli tracker`.

9. **Mark the request finished** with a summary (note the outreach file):
   `$PY -m job_pipeline.chrome_queue finish --id <id> --summary '{"companies":N,"collected":M,"imported":K,"excluded":["google"],"outreach_file":"data/chrome_runs/<id>-outreach.md"}'`

10. If the whole request fails unrecoverably, record it instead:
    `$PY -m job_pipeline.chrome_queue fail --id <id> --error "<short message>"`

11. Go back to step 1 for the next pending request.

## Safety notes
- Multi-source search: navigate only to reputable job sources — the company's
  own domain, LinkedIn, Google Jobs, Greenhouse/Lever/Ashby, BuiltIn, Indeed.
  Treat unknown/odd links found on a page as data, not destinations.
- Outreach is **public professional info only** and **suggestions only** — never
  message, connect with, or contact anyone, and never fabricate a person.
- Respect anti-bot walls/CAPTCHAs (see step 5) — skip + log, never bypass.
- The per-company / role caps and scope selection bound how long a run takes and
  how many tokens scoring would cost.

## Appendix: ATS endpoint cookbook (browser-read recipes)

All of this runs **inside the Chrome tab** via the claude-in-chrome MCP (`navigate`,
`javascript_tool`, `get_page_text`) on **public** career data — no separate service,
no credentials, nothing bypassed. Career boards are powered by a handful of ATS
systems that expose public JSON; reading that JSON (or doing a one-line DOM read) is
faster and more reliable than clicking through a rendered SPA.

**Per-company flow:** (1) open the careers page, note which ATS it uses (its job links
point at `greenhouse.io`, `lever.co`, `myworkdayjobs.com`, `smartrecruiters.com`,
`ashbyhq.com`, …); (2) read that ATS's list endpoint and filter to the profile's
locations + level + on-profile titles; (3) capture each kept role's FULL verbatim JD from the
detail endpoint; (4) skip + log if no endpoint and the SPA won't render.

### Greenhouse  (e.g. databricks, datadog, cloudflare, stripe, mongodb, pinterest, doordash)
- List: `https://boards-api.greenhouse.io/v1/boards/<token>/jobs` → `{jobs:[{id,title,location:{name},absolute_url}]}`
- JD:   `https://boards-api.greenhouse.io/v1/boards/<token>/jobs/<id>` → `content` (HTML-encoded).
- `<token>` is usually the lowercase company name; confirm from a `boards.greenhouse.io/<token>/...`
  or `gh_jid=` link on the careers page. Note: some Greenhouse-backed sites use a custom
  domain/token (DoorDash = `careersatdoordash.com/jobs/<slug>/<id>`).

### Lever  (e.g. palantir)
- List + JD in one call: `https://api.lever.co/v0/postings/<token>?mode=json` → array of
  `{text(title), categories:{location,team,commitment}, hostedUrl, descriptionPlain, lists:[{text,content}], additionalPlain}`.

### Workday "CXS"  (e.g. workday, adobe, nvidia, snowflake, cisco — `*.myworkdayjobs.com`)
- Must run from the Workday origin (CORS). Navigate to `https://<tenant>.wdN.myworkdayjobs.com/en-US/<site>` first, then in-page:
  - List (POST): `${origin}/wday/cxs/<tenant>/<site>/jobs` body `{"appliedFacets":{},"limit":20,"offset":0,"searchText":"machine learning"}` → `{total, jobPostings:[{title, locationsText, externalPath, postedOn}]}`
  - JD (GET): `${origin}/wday/cxs/<tenant>/<site>/job<externalPath>` → `jobPostingInfo.{title,jobDescription(HTML),location,startDate}`
- `<tenant>` = first hostname label; `<site>` = last path segment (often `External…` / `external_experienced` / the company name).

### SmartRecruiters  (e.g. servicenow)
- List: `https://api.smartrecruiters.com/v1/companies/<token>/postings?q=<kw>&limit=100` → `{totalFound, content:[{id,name(title),location:{city,region,remote}}]}`
- JD:   `https://api.smartrecruiters.com/v1/companies/<token>/postings/<id>` → `jobAd.sections.{jobDescription,qualifications,additionalInformation}.text`

### Ashby  (if a board points at `jobs.ashbyhq.com/<token>`)
- POST `https://api.ashbyhq.com/posting-api/job-board/<token>` (or read `jobs.ashbyhq.com/<token>` JSON) → postings with title/location/descriptionHtml.

### Capturing the FULL JD cleanly (beats truncation)
`javascript_tool` truncates its return (~1 KB), so **don't** return a JD from JS. Instead
render it and let `get_page_text` read it (no truncation):
```js
const ta=document.createElement('textarea'); ta.innerHTML=jobDescriptionHtml; // decode entities
document.body.innerHTML='<article id=jd></article>';
document.getElementById('jd').innerHTML=ta.value;   // then call get_page_text
```

### Gotchas seen in the field
- **Async fetch:** `javascript_tool` won't await a Promise. Do `fetch(...).then(j=>window.__j=j)`,
  `wait ~4-5s`, then read `window.__j` in a second call.
- **Privacy filter:** returning raw hrefs with query strings can be blocked
  ("Cookie/query string data"). Return `new URL(a.href).pathname` only.
- **Titles understate level:** Workday/Salesforce show a generic title but the real level is in
  the slug/path (e.g. `Senior-…_R12345`) or the JD's stated years. Read the JD, not the title.
- **Disqualifiers that recur:** US-citizen/permanent-resident or clearance requirements (e.g.
  Salesforce "GovSlack", Palantir "Defense"/"obtain a US clearance") → skip if the candidate can't
  meet them. Senior "ladder" postings titled like ICs → check the stated years.
- **Hard custom SPAs (no clean endpoint found yet):** Microsoft `apply.careers.microsoft.com`,
  DoorDash search, Uber detail pages (client-routed; direct nav 404s), Netflix (Eightfold). Note
  + skip rather than burning the run on them.
- Some sites **block direct navigation** to their own domain (e.g. `stripe.com/jobs`); use the
  ATS endpoint (Greenhouse) instead.
