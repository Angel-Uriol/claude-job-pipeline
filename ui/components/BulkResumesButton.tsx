// Confirm-before-run bulk resume generator with per-job selection.
//
// Flow:
//   1. Click the button → call /api/resumes/preview to fetch the FULL
//      list of eligible jobs (verdict + min_score honor the on-screen
//      filter the user set in the FilterBar).
//   2. Show a modal listing every eligible job with a checkbox. All are
//      checked by default — the user unticks ones they don't want.
//      "Select all / none" controls live in the modal header.
//   3. On confirm, POST /api/run/resumes?jobs=k1&jobs=k2&... — progress
//      streams via SSE and PipelinePanel shows it like any other phase.
//
// The button is disabled while a pipeline phase is running.

"use client";

import { useCallback, useMemo, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { FilterState } from "@/lib/types";

interface Props {
  /** True when ANY pipeline phase is in flight — disables the button. */
  isBusy: boolean;
  /** Current on-screen filter state. We use `verdict` + `min_score` to
   *  scope the eligible set so the dialog matches what the user expects
   *  to see. When `null`, the dialog falls back to apply-only. */
  filters?: FilterState | null;
  /** Called when a run successfully starts so the parent can update UI state. */
  onStarted?: () => void;
}

interface PreviewJob {
  key: string;
  company: string;
  title: string;
  score: number; // 0-100
}

interface Preview {
  count: number;
  jobs: PreviewJob[];
  max_per_run: number;
  verdicts: string[];
  min_score: number | null;
}

export default function BulkResumesButton({
  isBusy,
  filters,
  onStarted,
}: Props) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set of job keys the user wants resumes for. Initialized to "all of
  // them" when the preview loads; the user trims it down via checkboxes.
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Build the verdict + min_score scoping payload from the on-screen
  // filter. `skip` is intentionally never sent — we don't generate
  // resumes for skip-verdict jobs from this button.
  const resumeOpts = useMemo<
    { verdicts?: string[]; minScore?: number } | undefined
  >(() => {
    if (!filters) return undefined;
    const verdicts: string[] = [];
    if (filters.verdict.has("apply")) verdicts.push("apply");
    if (filters.verdict.has("consider")) verdicts.push("consider");
    const opts: { verdicts?: string[]; minScore?: number } = {};
    if (verdicts.length > 0) opts.verdicts = verdicts;
    if (filters.min_score > 0) opts.minScore = filters.min_score;
    return Object.keys(opts).length > 0 ? opts : undefined;
  }, [filters]);

  const handleOpen = useCallback(async () => {
    setOpening(true);
    setError(null);
    try {
      const p = await api.previewResumes(resumeOpts);
      setPreview(p);
      // Default: pre-select all eligible jobs. The user un-checks ones
      // they don't want. This matches the "bulk by default, refinable"
      // pattern most users expect from a confirm dialog.
      setSelected(new Set(p.jobs.map((j) => j.key)));
    } catch (e) {
      const msg =
        e instanceof ApiError ? `${e.status}: ${e.detail}` : (e as Error).message;
      setError(`Preview failed — ${msg}`);
    } finally {
      setOpening(false);
    }
  }, [resumeOpts]);

  const handleClose = useCallback(() => {
    setPreview(null);
    setSelected(new Set());
  }, []);

  const handleConfirm = useCallback(async () => {
    setError(null);
    const keys = Array.from(selected);
    if (keys.length === 0) {
      setError("Select at least one job to generate.");
      return;
    }
    try {
      // Pass the explicit selection — the backend will bypass the
      // verdict/min_score filter AND the max-per-run cap for explicit
      // selections, because the user has chosen the set deliberately.
      await api.runResumesBatch({ jobs: keys });
      setPreview(null);
      setSelected(new Set());
      onStarted?.();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError("Another phase is running. Wait for it to finish.");
      } else {
        const msg =
          e instanceof ApiError
            ? `${e.status}: ${e.detail}`
            : (e as Error).message;
        setError(`Start failed — ${msg}`);
      }
    }
  }, [onStarted, selected]);

  // Toggle one row's checkbox. We don't memoize this because the
  // resulting closure depends on `selected`, which changes on every
  // toggle anyway — memoization would just add noise.
  const toggleOne = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  // "Select all / none" shortcuts. All = check every row currently
  // listed; None = uncheck everything. These don't touch jobs that
  // aren't visible (currently we show up to 100 — the same upper
  // bound the backend sends).
  const selectAll = () => {
    if (preview) setSelected(new Set(preview.jobs.map((j) => j.key)));
  };
  const selectNone = () => setSelected(new Set());

  // Note: selection is (re)initialized in handleOpen whenever the preview
  // loads, so it always matches the visible set — no pruning effect needed.

  return (
    <>
      <button
        type="button"
        onClick={handleOpen}
        disabled={isBusy || opening}
        className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm font-semibold text-indigo-700 transition hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-500/30 dark:bg-indigo-500/15 dark:text-indigo-300 dark:hover:bg-indigo-500/25"
        title="Pick which jobs to generate resumes for"
      >
        {opening ? "Counting…" : "📄 Generate resumes…"}
      </button>

      {preview && (
        <>
          <div
            className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm"
            onClick={handleClose}
            aria-hidden
          />
          <div
            role="dialog"
            aria-modal="true"
            className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-800 dark:bg-slate-900"
          >
            <h3 className="mb-2 font-semibold text-slate-900 dark:text-slate-100">
              Generate resumes
            </h3>

            {preview.count === 0 ? (
              <>
                <p className="mb-4 text-sm text-slate-600 dark:text-slate-300">
                  No{" "}
                  <span className="font-mono">{preview.verdicts.join("/")}</span>
                  -verdict jobs are missing a resume
                  {preview.min_score !== null && preview.min_score > 0
                    ? ` at score ≥ ${preview.min_score}`
                    : ""}
                  . Loosen the filter, run{" "}
                  <span className="font-mono">deep-fit</span> to score new
                  jobs, or generate per-job from the drawer.
                </p>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={handleClose}
                    className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    Close
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="mb-3 text-sm text-slate-600 dark:text-slate-300">
                  Pick which{" "}
                  <span className="font-mono">
                    {preview.verdicts.join("/")}
                  </span>
                  -verdict jobs
                  {preview.min_score !== null && preview.min_score > 0
                    ? ` (score ≥ ${preview.min_score})`
                    : ""}{" "}
                  should get a fresh resume. {preview.count} total eligible.
                  Each one is tailored by Claude — summary + bullets honestly
                  rewritten for the role, numbers preserved (~10s and a few
                  cents per file; falls back to the base resume if Claude is
                  unavailable).
                </p>

                {/* Select-all / none header */}
                <div className="mb-2 flex items-center justify-between text-xs text-slate-600 dark:text-slate-400">
                  <span>
                    <strong className="text-slate-900 dark:text-slate-100">
                      {selected.size}
                    </strong>{" "}
                    of {preview.jobs.length} selected
                  </span>
                  <span className="flex gap-3">
                    <button
                      type="button"
                      onClick={selectAll}
                      className="text-indigo-600 hover:underline dark:text-indigo-400"
                    >
                      Select all
                    </button>
                    <button
                      type="button"
                      onClick={selectNone}
                      className="text-indigo-600 hover:underline dark:text-indigo-400"
                    >
                      Select none
                    </button>
                  </span>
                </div>

                <ul className="mb-4 flex-1 space-y-0 divide-y divide-slate-200 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 text-sm text-slate-700 dark:divide-slate-700 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
                  {preview.jobs.map((j) => {
                    const checked = selected.has(j.key);
                    return (
                      <li key={j.key}>
                        <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-slate-100 dark:hover:bg-slate-700/50">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleOne(j.key)}
                            className="h-4 w-4 rounded accent-indigo-600"
                          />
                          <span
                            className={
                              "inline-block min-w-[2.5rem] rounded px-1.5 py-0.5 text-center text-xs font-semibold " +
                              (j.score >= 80
                                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                                : j.score >= 50
                                  ? "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
                                  : "bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300")
                            }
                            title="Fit score (0-100)"
                          >
                            {j.score}
                          </span>
                          <span className="flex-1 truncate">
                            <span className="font-medium">{j.company}</span> ·{" "}
                            {j.title}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>

                {preview.count > preview.jobs.length && (
                  <p className="mb-3 text-xs italic text-slate-500 dark:text-slate-400">
                    Showing first {preview.jobs.length} of {preview.count}.
                    Narrow the filter to see fewer at once.
                  </p>
                )}

                {error && (
                  <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-2 py-1.5 text-xs text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
                    {error}
                  </div>
                )}

                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={handleClose}
                    className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleConfirm}
                    disabled={selected.size === 0}
                    className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Generate {selected.size}
                  </button>
                </div>
              </>
            )}
          </div>
        </>
      )}

      {error && !preview && (
        <span className="ml-2 text-xs text-rose-600 dark:text-rose-400">{error}</span>
      )}
    </>
  );
}
