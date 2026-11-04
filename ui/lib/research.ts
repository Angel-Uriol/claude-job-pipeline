import { API_BASE } from "./api";

export type ResearchKind = "comparison" | "interview";
export type ResearchJob = { job_key: string; company: string; title: string; fit_score: number | null };
export type Source = { id: string; url: string; title: string; excerpt: string; accessed_at: string;
  kind: string; published?: string };
export type Finding = { rating: string; explanation: string; source_ids: string[]; confidence: string;
  cohort?: string; evidence_quote?: string };
export type Prospects = { signal: string; reasons: string[]; blockers: string[]; next_steps: string[] };
export type Comparison = {
  summary: string; methodology: string; missing_information: string[];
  rows: { job_key: string; salary: Finding; benefits: Finding; company_reviews: Finding;
    interview_prospects: Prospects; hiring_prospects: Prospects; tradeoff: string }[];
};
export type InterviewPlan = {
  company_brief: string; role_priorities: string[]; process: string; source_ids: string[];
  questions: { question: string; kind: string; purpose: string; answer_outline: string;
    evidence_ids: string[]; source_ids: string[]; gap_to_acknowledge: string; verify_note?: string }[];
  study_plan: { session: string; focus: string; tasks: string[]; success_check: string }[];
  questions_to_ask: string[]; missing_information: string[];
};
export type ResearchReport = {
  id: string; kind: ResearchKind; status: "running" | "complete" | "failed";
  progress: string; created_at: string; jobs: ResearchJob[];
  content?: Comparison | InterviewPlan; sources?: Source[]; evidence?: Record<string, string>;
  warnings?: string[];
};
async function request<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/research${path}`, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : { cache: "no-store" });
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON error page (e.g. proxy / crash) — handled below */
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : `Research request failed (${res.status}).`);
  }
  return data as T;
}
export const researchApi = {
  options: (kind: ResearchKind) => request<{ jobs: ResearchJob[] }>(`/options?kind=${kind}`),
  reports: () => request<{ reports: ResearchReport[] }>(""),
  start: (kind: ResearchKind, job_keys: string[], stage: string, prep_days: number) =>
    request<ResearchReport>("", { kind, job_keys, stage, prep_days }),
};
