// Pipeline controls: big "Run daily" + JSearch + collapsible per-phase
// buttons. Progress strip below shows the current SSE event's message + i/total.

"use client";

import type { ProgressEvent } from "@/lib/types";

interface Props {
  running: string | null;
  lastEvent: ProgressEvent | null;
  onRun: (phase: string) => void;
}

const PHASES = ["scrape", "pre-filter", "deep-fit", "digest", "tracker"] as const;

export default function PipelinePanel({ running, lastEvent, onRun }: Props) {
  const isBusy = running !== null;

  // Determinate progress when we have i/total; otherwise indeterminate.
  const pct =
    lastEvent?.i != null && lastEvent?.total && lastEvent.total > 0
      ? Math.min(100, Math.round((lastEvent.i / lastEvent.total) * 100))
      : null;

  return (
    <section className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => onRun("daily")}
          disabled={isBusy}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          ▶ Run daily
        </button>

        <button
          type="button"
          onClick={() => onRun("jsearch")}
          disabled={isBusy}
          title="Open-web discovery via JSearch (Google for Jobs: LinkedIn/Indeed/Glassdoor/ZipRecruiter). Uses RapidAPI quota — run on demand, not daily."
          className="rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-2 text-sm font-semibold text-emerald-700 transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-emerald-500/30 dark:bg-emerald-500/15 dark:text-emerald-300 dark:hover:bg-emerald-500/25"
        >
          🔍 Search web (JSearch)
        </button>

        <button
          type="button"
          onClick={() => onRun("handoff")}
          disabled={isBusy}
          title="Push apply/consider jobs (+ their tailored resumes) into the Track tab. Discarded jobs are excluded."
          className="rounded-lg border border-violet-300 bg-violet-50 px-4 py-2 text-sm font-semibold text-violet-700 transition hover:bg-violet-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-violet-500/30 dark:bg-violet-500/15 dark:text-violet-300 dark:hover:bg-violet-500/25"
        >
          📨 Send to tracker
        </button>

        <button
          type="button"
          onClick={() => {
            if (
              window.confirm(
                "Clean the Discover inbox?\n\nThis dismisses every job currently shown and records their IDs so a fresh discovery run won't resurface them. Dismissed jobs stay searchable via the “show dismissed” filter, and anything already sent to the tracker is unaffected.",
              )
            ) {
              onRun("clean-discover");
            }
          }}
          disabled={isBusy}
          title="Archive the whole inbox before a fresh discovery run: dismiss every current job and remember their IDs so they're never re-surfaced. Nothing is deleted."
          className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-700 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-300 dark:hover:bg-amber-500/25"
        >
          🧹 Clean inbox
        </button>

        <details className="text-sm">
          <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200">
            Advanced phases
          </summary>
          <div className="mt-2 flex flex-wrap gap-2">
            {PHASES.map((phase) => (
              <button
                key={phase}
                type="button"
                onClick={() => onRun(phase)}
                disabled={isBusy}
                className="rounded-md border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
              >
                {phase}
              </button>
            ))}
          </div>
        </details>
      </div>

      {(isBusy || lastEvent) && (
        <div className="mt-3 border-t border-slate-200 pt-3 dark:border-slate-800">
          <div className="mb-1 text-sm tabular-nums text-slate-600 dark:text-slate-300">
            {lastEvent?.message ?? "Idle"}
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
            {pct !== null ? (
              <div
                className="h-full bg-indigo-600 transition-[width] duration-200 ease-out"
                style={{ width: `${pct}%` }}
              />
            ) : (
              // Indeterminate pulse — width animation while we don't have i/total.
              <div className="h-full w-full origin-left animate-pulse bg-indigo-600" />
            )}
          </div>
        </div>
      )}
    </section>
  );
}
