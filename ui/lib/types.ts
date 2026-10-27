// TypeScript interfaces mirroring the FastAPI backend's pydantic models.
//
// Backend source of truth: job_pipeline/ui/server.py + job_pipeline/models.py.
// When you add a field to a Job in Python, add it here too.

// ===== filter values =====

export type Verdict = "apply" | "consider" | "skip";

export type Status =
  | "new"
  | "pending"
  | "applied"
  | "resume_only"
  | "assessment_passed"
  | "rejected"
  | "auto_rejected"
  | "dead"
  | "mismatch"
  | "skipped"
  | "excluded"
  | "dismissed";

export type Source =
  | "greenhouse"
  | "lever"
  | "ashby"
  | "remoteok"
  | "jsearch"
  | "chrome_claude"
  | "manual";

export type LocationFilter = "preferred" | "remote" | "preferred_or_remote" | "all";

export type PostedWithin = 8 | 15 | 23 | 30 | 60 | "all";

// ===== tracker (Track tab — backed by the built-in SQLite tracker) =====

/** The 12 application states the tracker recognizes. */
export type TrackStatus =
  | "new"
  | "to_apply"
  | "applied"
  | "recruiter_screen"
  | "phone_screen"
  | "take_home"
  | "onsite"
  | "offer"
  | "rejected"
  | "auto_rejected"
  | "withdrawn"
  | "skip";

export const TRACK_STATUSES: TrackStatus[] = [
  "new",
  "to_apply",
  "applied",
  "recruiter_screen",
  "phone_screen",
  "take_home",
  "onsite",
  "offer",
  "rejected",
  "auto_rejected",
  "withdrawn",
  "skip",
];

/** A row from the tracker DB (mirrors tracker_db.serialize_job). */
export interface TrackedJob {
  id: string;
  company: string;
  role: string;
  role_tier: string | null;
  role_category: string | null;
  job_id: string | null;
  location: string | null;
  location_pass: string | null;
  posted: string | null;
  level: string | null;
  salary: string | null;
  h1b: string | null;
  url: string | null;
  fit_score: number | null;
  fit_breakdown: {
    stack: number | null;
    level: number | null;
    function: number | null;
    bridgeability: number | null;
    desirability: number | null;
  };
  verdict: string | null;
  bridge_notes: string | null;
  key_requirements: string[];
  key_nice_to_haves: string[];
  full_jd_text: string | null;
  resume_number: number | null;
  resume_variant: string | null;
  starred: number; // 0 | 1
  status: TrackStatus;
  notes: string | null;
  applied_at: string | null;
  added_at: string | null;
  hidden: number; // 0 | 1
  resume_file_path: string | null;
}

export interface TrackerJobsResponse {
  jobs: TrackedJob[];
  tracker_ready: boolean;
}

export interface TrackerStats {
  total: number;
  by_status: Record<string, number>;
  by_verdict: Record<string, number>;
  by_company: Record<string, number>;
  applied_count: number;
  hidden_count: number;
  tracker_ready: boolean;
}

// ===== job =====

/** Per-axis sub-scores from deep-fit. Each is on the 1.0–10.0 scale. */
export interface ScoreResult {
  stack: number;
  level: number;
  function: number;
  bridgeability: number;
  desirability: number;
}

/** One Job as returned by /api/jobs. The backend adds `key` and
 *  `fit_score_pct` (0-100 view of fit_score) to its `_job_to_ui`. */
export interface Job {
  // identity
  key: string;                    // stable lookup key for URLs
  company: string;
  job_id: string | null;
  title: string;
  team: string | null;
  source: Source;

  // posting metadata
  url: string;
  location: string;
  location_kind?: "preferred" | "remote" | "other" | "unknown";
  posted_date: string | null;     // ISO 8601
  discovered_at: string;          // ISO 8601
  days_since_posted: number | null;

  // raw / extracted
  description: string;
  extracted: Record<string, unknown> | null;

  // scoring
  pre_filter_verdict: "pass" | "skip" | null;
  pre_filter_reason: string | null;
  fit_score: number | null;       // 1.0–10.0 (backend-native scale)
  fit_score_pct: number | null;   // 0–100 (UI-friendly)
  fit_verdict: Verdict | null;
  fit_justification: string | null;
  fit_subscores: ScoreResult | null;
  honest_bridge_notes: string | null;

  // target-company overlay (your target_companies.yaml)
  is_target_company: boolean;
  target_tier: number | null;   // 1 (strongest) .. 5

  // lifecycle
  status: Status;
  resume_path: string | null;
  applied_at: string | null;
  notes: string | null;
  starred: boolean;
}

// ===== responses =====

export interface StateResponse {
  counts: {
    total: number;
    apply: number;
    consider: number;
    skipped: number;
    applied: number;
    starred: number;
  };
  last_refreshed: string | null;
  pipeline_running: string | null;     // phase name when running
  last_run_phase: string | null;
  last_run_error: string | null;
}

export interface JobListResponse {
  jobs: Job[];
  total: number;
}

export interface RunResponse {
  started: string;
  task_id: string;
}

export interface TaskStatusResponse {
  task_id: string;
  running: string | null;
  last_run_phase: string | null;
  last_run_at: string | null;
  last_run_error: string | null;
  last_run_result: Record<string, unknown>;
}

export interface ResumeResponse {
  path: string;
}

// ===== SSE event =====

/** Progress event from the pipeline runner. Matches runner.ProgressEvent
 *  exactly — `phase`+`message` are always present, `i/total/item` are
 *  optional and only set inside per-job loops. */
export interface ProgressEvent {
  phase: string;
  message: string;
  i: number | null;
  total: number | null;
  item: string | null;
  extra: Record<string, unknown>;
}

// ===== companies =====

export interface CompanyEntry {
  name: string;
  slug?: string;
  careers_url?: string;
  platform?: string;
}

export interface CompaniesResponse {
  greenhouse: CompanyEntry[];
  lever: CompanyEntry[];
  no_api: CompanyEntry[];
  excluded: string[];
  dead: string[];
}

// ===== chrome-claude (browser search) =====

export type ChromeClaudeScope = "selected" | "new" | "mix";

export interface ChromeClaudeTarget {
  name: string;
  careers_url: string;
  platform: string;
}

export interface ChromeClaudePostedOption {
  label: string;
  value: number | null;
}

/** GET /api/chrome-claude/preview — what the modal offers. */
export interface ChromeClaudePreview {
  selected: ChromeClaudeTarget[]; // real companies with a careers page
  new: ChromeClaudeTarget[]; // linkedin_search_* discovery entries
  posted_within_options: ChromeClaudePostedOption[];
  batch_size: number; // companies per batch
  max_parallel_batches: number; // batches (tabs) in flight at once
}

/** POST /api/chrome-claude/parse — a proposed run parsed from free text. */
export interface ChromeClaudeParseResult {
  scope: ChromeClaudeScope;
  companies: string[]; // matched to tracked no_api companies
  ad_hoc_companies: string[]; // named but not tracked — watcher finds careers page
  posted_within: number | null;
  score_after: boolean;
  extra_notes: string;
}

/** One queued run. Mirrors a record in data/chrome_runs/. */
export interface ChromeClaudeRequest {
  id: string;
  scope: ChromeClaudeScope;
  companies: string[];
  ad_hoc_companies: string[];
  extra_notes: string;
  posted_within: number | null;
  score_after: boolean;
  status: "pending" | "running" | "done" | "error";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  summary: Record<string, unknown> | string | null;
  error: string | null;
}

export interface ChromeClaudeStatusResponse {
  requests: ChromeClaudeRequest[];
}

// ===== UI-local filter state =====

/** What the filter bar holds. Persisted to URL search params. */
export interface FilterState {
  posted_within: PostedWithin;
  location: LocationFilter;
  min_score: number;             // 0..100
  verdict: Set<Verdict>;
  status: Set<Status>;
  source: Set<Source>;
  company: string | null;
  starred_only: boolean;
  show_dismissed: boolean;       // when false (default), hide status=dismissed rows
  q: string;
  sort: string;                  // "fit_score:desc" etc.
}

// ===== setup (first-run questionnaire) =====

export type AuthMode = "api_key" | "claude_code";

export interface SetupMessage {
  role: "assistant" | "user";
  text: string;
}

export interface SetupCompany {
  name: string;
  tier: number;
  why?: string;
  careers_url?: string;
  aliases?: string[];
  selected?: boolean;
  platform?: "greenhouse" | "lever" | "ashby" | null;
  slug?: string | null;
  jobs?: number;
}

export interface SetupInterview {
  messages: SetupMessage[];
  draft: Record<string, unknown>;
  missing: { field: string; label: string }[];
  required_total: number;
  optional: Record<string, string>;
  done: boolean;
  resume: { parsed: boolean; name: string | null; roles: number; bullets: number };
  companies: SetupCompany[];
}

export interface SetupStatus {
  configured: boolean;
  profile_dir: string;
  auth: {
    mode: AuthMode;
    connected: boolean;
    api_key_preview: string;
    cli: { cli_found: boolean; cli_path: string | null; oauth_token_env: boolean };
  };
  interview: SetupInterview;
}

export interface AuthCheck {
  auth_mode: AuthMode;
  model: string;
  ok: boolean;
  detail: string;
}
