// Today — the home page. Answers "what should I do now?" in plain words:
//   1. one button to find new jobs (runs the daily pipeline, with a progress line)
//   2. your top new matches, each with why it fits, what's missing, and ONE next step
//   3. "Waiting on you": follow-ups due, resumes not sent yet, interviews to prep
// Everything else (filters, every job, pipeline phases) lives on the Jobs page.
// Data: GET /api/today (job_pipeline/ui/today_routes.py).

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import Checklist from "@/components/Checklist";
import Explain from "@/components/Explain";
import { ApiError, api, openEventStream } from "@/lib/api";
import { scoreTone } from "@/lib/plain";
import type { TodayMatch, TodayResponse, TodayWaiting } from "@/lib/types";

const BTN =
  "rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50";
const BTN_GHOST =
  "rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800";
const CARD =
  "rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : (e as Error).message;
}

// Plain wording for the progress events the daily run streams.
const PHASE_WORDS: Record<string, string> = {
  scrape: "Checking company career pages",
  jsearch: "Searching job boards",
  "pre-filter": "Removing jobs that don't match your rules",
  "deep-fit": "Claude is reading and scoring the best ones",
  digest: "Writing your summary",
  tracker: "Updating your tracker",
};

export default function TodayPage() {
  const router = useRouter();
  const [data, setData] = useState<TodayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [demo, setDemo] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<string>("");
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await api.today());
      setError(null);
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  // First run → Setup. Also learn whether this is the demo profile.
  useEffect(() => {
    api
      .setupStatus()
      .then((s) => {
        if (!s.configured) router.replace("/setup");
      })
      .catch(() => {});
    api
      .profiles()
      .then((r) => setDemo(Boolean(r.profiles.find((p) => p.active)?.demo)))
      .catch(() => {});
    api
      .getTaskStatus()
      .then((t) => setRunning(t.running !== null))
      .catch(() => {});
  }, [router]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
  }, [refresh]);

  // Live progress while "Find new jobs" runs.
  useEffect(() => {
    const es = openEventStream((evt) => {
      if (evt.phase === "done" || evt.phase === "error") {
        setRunning(false);
        setProgress(evt.phase === "error" ? `Stopped: ${evt.message}` : "Done — here's what's new.");
        setTimeout(refresh, 500);
        return;
      }
      setRunning(true);
      const words = PHASE_WORDS[evt.phase] ?? evt.message;
      const count = evt.total ? ` (${evt.i ?? 0} of ${evt.total})` : "";
      setProgress(`${words}${count}…`);
    });
    return () => es.close();
  }, [refresh]);

  const findJobs = async () => {
    setProgress("Starting…");
    try {
      await api.runPhase("daily");
      setRunning(true);
    } catch (e) {
      setProgress(e instanceof ApiError && e.status === 409 ? "A search is already running." : errText(e));
    }
  };

  const act = async (m: TodayMatch) => {
    const step = m.next_step;
    if (!step) return;
    setBusyKey(m.key);
    try {
      if (step.id === "resume") {
        await api.generateResume(m.key);
      } else if (step.id === "open") {
        // Open the tab first (synchronously) so pop-up blockers allow it.
        window.open(m.url, "_blank", "noopener");
        await api.markOpened(m.key);
      } else if (step.id === "applied") {
        await api.markApplied(m.key);
      } else if (step.id === "follow_up") {
        await api.followUp({ action: "done", job_key: m.key });
      }
      await refresh();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusyKey(null);
    }
  };

  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  return (
    <main className="mx-auto max-w-[1000px] px-4 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-slate-500 dark:text-slate-400">{today}</p>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-slate-50">Today</h1>
        </div>
        <div className="text-right">
          <button
            type="button"
            className={BTN}
            disabled={running || demo}
            title={demo ? "Switched off in the demo — start your own profile to search for real jobs." : undefined}
            onClick={findJobs}
          >
            {running ? "Searching…" : "Find new jobs"}
          </button>
          {progress && (
            <p role="status" className="mt-2 max-w-xs text-xs text-slate-500 dark:text-slate-400">
              {progress}
            </p>
          )}
          {data?.budget && <BudgetLine budget={data.budget} />}
        </div>
      </header>

      {error && (
        <p className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </p>
      )}

      {/* --- top matches --- */}
      <section className="mt-8">
        <h2 className="flex items-center text-lg font-semibold text-slate-900 dark:text-slate-100">
          Your best matches
          <Explain term="score" />
        </h2>
        {data && data.matches.length === 0 && (
          <div className={`${CARD} mt-3 text-sm text-slate-600 dark:text-slate-300`}>
            No open matches yet. Click <b>Find new jobs</b> — it checks your target companies and job
            boards, then Claude scores what it finds. It usually takes a few minutes.
          </div>
        )}
        <div className="mt-3 grid gap-4">
          {data?.matches.map((m) => (
            <MatchCard key={m.key} m={m} busy={busyKey === m.key} onAct={() => act(m)} />
          ))}
        </div>
        {data && data.total_open > data.matches.length && (
          <p className="mt-3 text-sm">
            <Link href="/jobs" className="text-indigo-600 underline dark:text-indigo-400">
              See all {data.total_open} open jobs →
            </Link>
          </p>
        )}
      </section>

      {/* --- waiting on you --- */}
      <section className="mt-10">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Waiting on you</h2>
        {data && data.waiting.length === 0 && (
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">Nothing right now. Nice.</p>
        )}
        <ul className="mt-3 space-y-2">
          {data?.waiting.map((w, i) => (
            <WaitingRow key={`${w.kind}-${w.tracker_id ?? w.job_key ?? i}`} w={w} onChange={refresh} onError={setError} />
          ))}
        </ul>
      </section>
    </main>
  );
}

const NEXT_LABEL: Record<string, string> = {
  resume: "Make my tailored resume",
  open: "Open the application page",
  applied: "I applied",
  follow_up: "I followed up",
  interview: "Prepare for the interview",
};

function MatchCard({ m, busy, onAct }: { m: TodayMatch; busy: boolean; onAct: () => void }) {
  const step = m.next_step;
  return (
    <article className={CARD}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${scoreTone(m.score)}`}>{m.label}</span>
            {m.is_new && (
              <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-semibold text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-200">
                New
              </span>
            )}
          </div>
          <h3 className="mt-2 text-base font-semibold text-slate-900 dark:text-slate-50">{m.title}</h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {m.company} · {m.location}
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          {step ? (
            step.id === "interview" ? (
              <Link href="/interview" className={BTN}>
                {NEXT_LABEL.interview}
              </Link>
            ) : (
              <button type="button" className={BTN} disabled={busy} onClick={onAct}>
                {busy ? "Working…" : NEXT_LABEL[step.id] ?? step.label}
              </button>
            )
          ) : (
            <span className="text-sm text-emerald-600">All steps done ✓</span>
          )}
          {m.has_resume && (
            <a className="text-xs text-indigo-600 underline dark:text-indigo-400" href={api.downloadResumeUrl(m.key)}>
              Download resume (.docx)
            </a>
          )}
        </div>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Why it fits</p>
          <ul className="mt-1 space-y-1 text-sm text-slate-700 dark:text-slate-300">
            {m.reasons.length ? m.reasons.map((r) => <li key={r}>✓ {r}</li>) : <li className="text-slate-400">—</li>}
          </ul>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">What&apos;s missing</p>
          <ul className="mt-1 space-y-1 text-sm text-slate-700 dark:text-slate-300">
            {m.gaps.length ? m.gaps.map((g) => <li key={g}>△ {g}</li>) : <li className="text-slate-400">No real gaps</li>}
          </ul>
        </div>
      </div>

      <div className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800">
        <Checklist steps={m.steps} compact />
      </div>
    </article>
  );
}

function WaitingRow({ w, onChange, onError }: { w: TodayWaiting; onChange: () => void; onError: (s: string) => void }) {
  const [busy, setBusy] = useState(false);
  const target = w.tracker_id ? { tracker_id: w.tracker_id } : { job_key: w.job_key };
  const run = async (action: "done" | "snooze") => {
    setBusy(true);
    try {
      await api.followUp({ action, days: 3, ...target });
      onChange();
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  };
  const icon = w.kind === "follow_up" ? "✉️" : w.kind === "interview" ? "🎤" : "📄";
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="min-w-0 text-sm">
        <p className="font-medium text-slate-900 dark:text-slate-100">
          <span aria-hidden>{icon} </span>
          {w.title} · {w.company}
          {w.kind === "follow_up" && <Explain term="follow_up" />}
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">{w.detail}</p>
      </div>
      <div className="flex gap-2">
        {w.kind === "follow_up" && (
          <>
            <button type="button" className={BTN_GHOST} disabled={busy} onClick={() => run("done")}>
              Done
            </button>
            <button type="button" className={BTN_GHOST} disabled={busy} onClick={() => run("snooze")}>
              Remind me in 3 days
            </button>
          </>
        )}
        {w.kind === "interview" && (
          <Link href="/interview" className={BTN_GHOST}>
            Prepare
          </Link>
        )}
        {w.kind === "resume_not_sent" && w.job_key && (
          <a className={BTN_GHOST} href={api.downloadResumeUrl(w.job_key)}>
            Download resume
          </a>
        )}
      </div>
    </li>
  );
}

function BudgetLine({ budget }: { budget: NonNullable<TodayResponse["budget"]> }) {
  const pct = budget.budget_usd > 0 ? Math.min(100, (budget.spent_usd / budget.budget_usd) * 100) : 0;
  return (
    <div className="mt-3 w-56 text-left text-xs text-slate-500 dark:text-slate-400">
      <p>
        Claude this month: <b>${budget.spent_usd.toFixed(2)}</b>
        {budget.budget_usd > 0 && <> of ${budget.budget_usd.toFixed(2)}</>}
        {budget.subscription && " (API-equivalent)"}
        {" · "}
        <Link href="/privacy" className="underline">
          {budget.budget_usd > 0 ? "Budget" : "Set a budget"}
        </Link>
      </p>
      {budget.budget_usd > 0 && (
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
          <div
            className={`h-full ${pct >= 90 ? "bg-rose-500" : pct >= 70 ? "bg-amber-500" : "bg-emerald-500"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
