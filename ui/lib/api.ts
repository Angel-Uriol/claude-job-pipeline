// Typed API client for the FastAPI backend.
//
// Convention: every function does ONE network call, returns the parsed
// JSON (or throws on non-2xx). Callers do error handling at the call
// site so the UI can show meaningful messages.
//
// Backend base URL — defaults to localhost:8765 (per the spec) but
// `NEXT_PUBLIC_API_BASE` overrides for deployment environments.

import type {
  AuthCheck,
  AuthMode,
  ChromeClaudeParseResult,
  ChromeClaudePreview,
  ChromeClaudeRequest,
  ChromeClaudeScope,
  ChromeClaudeStatusResponse,
  CompaniesResponse,
  FilterState,
  Job,
  JobListResponse,
  ProgressEvent,
  ResumeResponse,
  RunResponse,
  SetupCompany,
  SetupInterview,
  SetupStatus,
  StateResponse,
  TaskStatusResponse,
  TrackerJobsResponse,
  TrackerStats,
} from "./types";

/**
 * Backend base URL.
 *
 * Default behavior:
 * - In the browser, use the same hostname the page was loaded from (so
 *   `localhost:8766` → `localhost:8765` and `127.0.0.1:8766` → `127.0.0.1:8765`).
 *   This sidesteps the localhost-vs-127.0.0.1 origin mismatch that
 *   breaks CORS preflight in some browsers.
 * - On the server (SSR/build), fall back to 127.0.0.1.
 * - Override either with `NEXT_PUBLIC_API_BASE` for deployments.
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ??
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8765`
    : "http://127.0.0.1:8765");

// ===== fetch helper =====

class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    // FastAPI returns {detail: "..."} on errors. Fall back to status text.
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* ignore parse errors */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export { ApiError };

// ===== endpoints =====

export const api = {
  getState: () => request<StateResponse>("/api/state"),

  // ===== Setup page (first run) =====
  setupStatus: () => request<SetupStatus>("/api/setup/status"),
  setupAuth: (mode: AuthMode, apiKey = "") =>
    request<AuthCheck>("/api/setup/auth", {
      method: "POST",
      body: JSON.stringify({ mode, api_key: apiKey }),
    }),
  /** Multipart upload — no JSON content-type, so not via `request`. */
  setupResume: async (file: File): Promise<SetupInterview> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/api/setup/resume`, { method: "POST", body: form });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = String((await res.json())?.detail ?? detail);
      } catch {
        /* ignore */
      }
      throw new ApiError(res.status, detail);
    }
    return res.json() as Promise<SetupInterview>;
  },
  setupInterview: (message = "") =>
    request<SetupInterview>("/api/setup/interview", {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  setupSuggestCompanies: () =>
    request<SetupInterview>("/api/setup/companies/suggest", { method: "POST" }),
  setupVerifyCompanies: (companies: SetupCompany[]) =>
    request<SetupInterview>("/api/setup/companies/verify", {
      method: "POST",
      body: JSON.stringify({ companies }),
    }),
  setupPreview: () => request<{ files: Record<string, string> }>("/api/setup/preview"),
  setupCommit: () =>
    request<{ written: string[]; configured: boolean }>("/api/setup/commit", { method: "POST" }),
  setupReset: () => request<SetupInterview>("/api/setup/reset", { method: "POST" }),

  /** List jobs with filter params. Maps our `FilterState` to URL query params. */
  getJobs: (filters: Partial<FilterState> = {}) => {
    const params = new URLSearchParams();
    if (filters.min_score !== undefined) {
      params.set("min_score", String(filters.min_score));
    }
    if (filters.posted_within && filters.posted_within !== "all") {
      params.set("posted_within", String(filters.posted_within));
    }
    if (filters.location && filters.location !== "all") {
      params.set("location", filters.location);
    }
    if (filters.verdict && filters.verdict.size === 1) {
      // Single-select: pass as URL param so the server filters. Multi-
      // select happens client-side (less efficient, but works).
      params.set("verdict", Array.from(filters.verdict)[0]);
    }
    if (filters.status && filters.status.size === 1) {
      params.set("status", Array.from(filters.status)[0]);
    }
    if (filters.source && filters.source.size === 1) {
      params.set("source", Array.from(filters.source)[0]);
    }
    if (filters.company) {
      params.set("company", filters.company);
    }
    if (filters.starred_only) {
      params.set("starred_only", "true");
    }
    if (filters.q) {
      params.set("q", filters.q);
    }
    if (filters.sort) {
      params.set("sort", filters.sort);
    }
    return request<JobListResponse>(`/api/jobs?${params.toString()}`);
  },

  getJob: (key: string) => request<Job>(`/api/jobs/${encodeURIComponent(key)}`),

  patchJob: (key: string, patch: Partial<Job>) =>
    request<Job>(`/api/jobs/${encodeURIComponent(key)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  /** Stamp status=applied + applied_at=now. */
  markApplied: (key: string) =>
    request<Job>(`/api/jobs/${encodeURIComponent(key)}/apply`, {
      method: "POST",
    }),

  /** Toggle star. The backend doesn't have a dedicated endpoint — uses PATCH. */
  toggleStar: (key: string, starred: boolean) =>
    request<Job>(`/api/jobs/${encodeURIComponent(key)}`, {
      method: "PATCH",
      body: JSON.stringify({ starred }),
    }),

  /** Soft-discard: mark a job dismissed (not interested). Survives re-scrapes. */
  dismissJob: (key: string) =>
    request<Job>(`/api/jobs/${encodeURIComponent(key)}/dismiss`, {
      method: "POST",
    }),

  /** Hard-delete a job from the store. May reappear on next scrape if still live. */
  deleteJob: (key: string) =>
    request<{ deleted: number }>(`/api/jobs/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),

  generateResume: (key: string, family?: string) => {
    const qs = family ? `?family=${encodeURIComponent(family)}` : "";
    return request<ResumeResponse>(
      `/api/jobs/${encodeURIComponent(key)}/resume${qs}`,
      { method: "POST" },
    );
  },

  downloadResumeUrl: (key: string) =>
    `${API_BASE}/api/jobs/${encodeURIComponent(key)}/resume/download`,

  runPhase: (phase: string) =>
    request<RunResponse>(`/api/run/${encodeURIComponent(phase)}`, {
      method: "POST",
    }),

  getTaskStatus: () => request<TaskStatusResponse>("/api/tasks/current"),

  getCompanies: () => request<CompaniesResponse>("/api/companies"),

  getConfig: () => request<Record<string, unknown>>("/api/config"),

  /** Preview the bulk-resume action — returns the FULL list (up to 100)
   *  of eligible jobs, each with a stable `key`, company, title, and
   *  score. The dialog renders this list with per-job checkboxes so the
   *  user can pick exactly which ones to render.
   *
   *  By default the server filters to apply-verdict jobs (backward-compat).
   *  Pass `verdicts: ["apply", "consider"]` and/or `minScore: 70` (0-100)
   *  to broaden the eligible list to match the on-screen verdict filter. */
  previewResumes: (opts?: { verdicts?: string[]; minScore?: number }) =>
    request<{
      count: number;
      jobs: { key: string; company: string; title: string; score: number }[];
      max_per_run: number;
      verdicts: string[];
      min_score: number | null;
    }>(`/api/resumes/preview${_resumeQuery(opts)}`),

  /** Kick off the `resumes` phase.
   *
   *  Two selection modes (mirroring the backend):
   *    - `jobs` non-empty → render exactly those keys (bypasses
   *      verdict + min_score + max-per-run cap).
   *    - Otherwise → filter-based with `verdicts` + `minScore`. */
  runResumesBatch: (opts?: {
    verdicts?: string[];
    minScore?: number;
    jobs?: string[];
  }) =>
    request<RunResponse>(`/api/run/resumes${_resumeQuery(opts)}`, {
      method: "POST",
    }),

  // ===== chrome-claude (browser search) =====

  /** Targets + posting-date options for the Run Chrome Claude modal. */
  chromeClaudePreview: () =>
    request<ChromeClaudePreview>("/api/chrome-claude/preview"),

  /** Queue a browser-search run; returns the queued request record.
   *
   *  This only ENQUEUES — a Claude Code watcher loop drives the browser
   *  and imports the results. `companies` only applies to the selected
   *  portion of the scope. */
  runChromeClaude: (opts: {
    scope: ChromeClaudeScope;
    companies?: string[];
    postedWithin?: number | null;
    scoreAfter?: boolean;
    adHocCompanies?: string[];
    extraNotes?: string;
  }) => {
    const params = new URLSearchParams();
    params.set("scope", opts.scope);
    for (const c of opts.companies ?? []) params.append("company", c);
    for (const c of opts.adHocCompanies ?? [])
      params.append("ad_hoc_company", c);
    if (opts.postedWithin != null) {
      params.set("posted_within", String(opts.postedWithin));
    }
    if (opts.scoreAfter) params.set("score_after", "true");
    if (opts.extraNotes) params.set("extra_notes", opts.extraNotes);
    return request<ChromeClaudeRequest>(
      `/api/chrome-claude/run?${params.toString()}`,
      { method: "POST" },
    );
  },

  /** Poll the queue state (newest first) for the status chip. */
  chromeClaudeStatus: () =>
    request<ChromeClaudeStatusResponse>("/api/chrome-claude/status"),

  /** Parse a free-text request into a proposed run via gpt-4o-mini. */
  chromeClaudeParse: (text: string) =>
    request<ChromeClaudeParseResult>("/api/chrome-claude/parse", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  // ===== Track tab (built-in data/tracker.db) =====

  /** List tracked applications with filters. */
  trackerJobs: (params: TrackerFilters = {}) => {
    const qs = _trackerQuery(params);
    return request<TrackerJobsResponse>(
      `/api/tracker/jobs${qs ? `?${qs}` : ""}`,
    );
  },

  /** URL for the filtered CSV export (open in a new tab to download). */
  trackerCsvUrl: (params: TrackerFilters = {}) => {
    const qs = _trackerQuery(params);
    return `${API_BASE}/api/tracker/jobs.csv${qs ? `?${qs}` : ""}`;
  },

  trackerStats: () => request<TrackerStats>("/api/tracker/stats"),

  trackerSetStatus: (id: string, status: string) =>
    request<{ ok: boolean; status: string }>(
      `/api/tracker/jobs/${encodeURIComponent(id)}/status`,
      { method: "POST", body: JSON.stringify({ status }) },
    ),

  trackerToggleStar: (id: string) =>
    request<{ ok: boolean; starred: number }>(
      `/api/tracker/jobs/${encodeURIComponent(id)}/star`,
      { method: "POST" },
    ),

  trackerToggleHide: (id: string) =>
    request<{ ok: boolean; hidden: number }>(
      `/api/tracker/jobs/${encodeURIComponent(id)}/hide`,
      { method: "POST" },
    ),

  trackerSetNotes: (id: string, notes: string) =>
    request<{ ok: boolean }>(
      `/api/tracker/jobs/${encodeURIComponent(id)}/notes`,
      { method: "POST", body: JSON.stringify({ notes }) },
    ),

  trackerDelete: (id: string) =>
    request<{ ok: boolean }>(`/api/tracker/jobs/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  trackerBulkStatus: (ids: string[], status: string) =>
    request<{ updated: number }>("/api/tracker/bulk/status", {
      method: "POST",
      body: JSON.stringify({ ids, status }),
    }),

  trackerResumeDownloadUrl: (id: string) =>
    `${API_BASE}/api/tracker/jobs/${encodeURIComponent(id)}/resume/download`,
};

/** Tracker-tab filter params (mirror /api/tracker/jobs query args). */
export interface TrackerFilters {
  search?: string;
  location?: string;
  verdict?: string;
  status?: string;
  company?: string;
  sort?: string;
  hidden?: string;
}

/** Build the query string for the tracker endpoints; drops "all"/empty. */
function _trackerQuery(params: TrackerFilters): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v && v !== "all") q.set(k, v);
  }
  return q.toString();
}

/** Build the query string shared by /api/resumes/preview and /api/run/resumes.
 *
 *  Both endpoints accept `verdict`, `min_score`, and (for run) `jobs`.
 *  `min_score` is on the 0-100 UI scale; the server divides by 10. */
function _resumeQuery(opts?: {
  verdicts?: string[];
  minScore?: number;
  jobs?: string[];
}): string {
  if (!opts) return "";
  const params = new URLSearchParams();
  for (const v of opts.verdicts ?? []) {
    params.append("verdict", v);
  }
  if (opts.minScore !== undefined) {
    params.set("min_score", String(opts.minScore));
  }
  for (const j of opts.jobs ?? []) {
    params.append("jobs", j);
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

// ===== SSE stream =====

/** Open a server-sent events stream of pipeline progress.
 *
 * Returns the underlying EventSource so the caller can `.close()` on
 * unmount. Callers attach `onmessage` / event-named handlers themselves.
 */
export function openEventStream(
  onProgress: (e: ProgressEvent) => void,
  onError?: (e: Event) => void,
): EventSource {
  const es = new EventSource(`${API_BASE}/api/stream`);
  es.addEventListener("progress", (raw) => {
    try {
      const data = JSON.parse((raw as MessageEvent).data) as ProgressEvent;
      onProgress(data);
    } catch {
      /* malformed — drop */
    }
  });
  if (onError) es.onerror = onError;
  return es;
}
