// "Run Chrome Claude" — queue a browser job-search run.
//
// The web backend can't drive the browser itself (only a Claude Code agent
// session holds the Claude-in-Chrome connection). So this button just
// ENQUEUES a run via POST /api/chrome-claude/run; a Claude Code "watcher
// loop" drains the queue, drives Chrome per prompts/chrome_claude_runbook.md,
// and imports the results.
//
// The modal can also be opened PRE-FILLED by the natural-language chat box
// (ChromeClaudeChat): the parent bumps `prefillNonce` with a `prefill` result
// and we open with those values for the user to confirm/tweak.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type {
  ChromeClaudeParseResult,
  ChromeClaudePreview,
  ChromeClaudeRequest,
  ChromeClaudeScope,
} from "@/lib/types";

interface Props {
  /** Called right after a run is successfully queued. */
  onQueued?: () => void;
  /** Called when a tracked run finishes (status "done") so the parent can
   *  refresh the job list to show newly imported roles. */
  onCompleted?: () => void;
  /** When `prefillNonce` increments, open the modal pre-filled from `prefill`
   *  (set by the natural-language chat box). */
  prefill?: ChromeClaudeParseResult | null;
  prefillNonce?: number;
}

// Terminal statuses stop the status poller.
const TERMINAL = new Set(["done", "error"]);

export default function ChromeClaudeButton({
  onQueued,
  onCompleted,
  prefill,
  prefillNonce,
}: Props) {
  const [preview, setPreview] = useState<ChromeClaudePreview | null>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Modal form state.
  const [scope, setScope] = useState<ChromeClaudeScope>("mix");
  const [companies, setCompanies] = useState<Set<string>>(new Set());
  const [postedWithin, setPostedWithin] = useState<number | null>(7);
  // Default ON: deep-fit is cheap (gpt-4o-mini) now, so auto-score imported
  // jobs to land them actionable instead of unscored 'new'.
  const [scoreAfter, setScoreAfter] = useState(true);
  // Companies named that aren't tracked — the watcher finds their careers page.
  const [adHocCompanies, setAdHocCompanies] = useState<string[]>([]);
  // Free-text extra criteria injected into this run's prompt.
  const [extraNotes, setExtraNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // The run we're currently tracking (after queueing), polled for status.
  const [tracked, setTracked] = useState<ChromeClaudeRequest | null>(null);
  const completedFired = useRef(false);

  // Open the modal — pre-filled from `pf` (chat box) or with sensible defaults.
  const openModal = useCallback(
    async (pf?: ChromeClaudeParseResult | null) => {
      setOpening(true);
      setError(null);
      try {
        const p = await api.chromeClaudePreview();
        setPreview(p);
        if (pf) {
          setScope(pf.scope);
          setCompanies(new Set(pf.companies));
          setPostedWithin(pf.posted_within);
          setScoreAfter(pf.score_after);
          setAdHocCompanies(pf.ad_hoc_companies);
          setExtraNotes(pf.extra_notes);
        } else {
          // Defaults: scrape everything (selected + new), all companies checked.
          setScope("mix");
          setCompanies(new Set(p.selected.map((t) => t.name)));
          const wk = p.posted_within_options.find((o) => o.value === 7);
          setPostedWithin(wk ? 7 : (p.posted_within_options[0]?.value ?? null));
          setScoreAfter(true);
          setAdHocCompanies([]);
          setExtraNotes("");
        }
      } catch (e) {
        setError(`Couldn't load options — ${msgOf(e)}`);
      } finally {
        setOpening(false);
      }
    },
    [],
  );

  const handleOpen = useCallback(() => void openModal(null), [openModal]);

  // Open pre-filled when the chat box parses a request (parent bumps the nonce).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (prefillNonce && prefill) void openModal(prefill);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillNonce]);

  const handleClose = useCallback(() => {
    setPreview(null);
    setError(null);
  }, []);

  const toggleCompany = (name: string) => {
    setCompanies((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };
  const selectAll = () => {
    if (preview) setCompanies(new Set(preview.selected.map((t) => t.name)));
  };
  const selectNone = () => setCompanies(new Set());
  const removeAdHoc = (name: string) =>
    setAdHocCompanies((prev) => prev.filter((n) => n !== name));

  // The company picker only matters when the scope includes "selected".
  const companyPickerShown = scope === "selected" || scope === "mix";
  // Block confirm if a company-scope has nothing to search (no checked
  // companies AND no ad-hoc companies). Empty checked + empty ad-hoc would be
  // ambiguous, so make the user be explicit or switch to "new".
  const confirmDisabled =
    submitting ||
    (companyPickerShown && companies.size === 0 && adHocCompanies.length === 0);

  // How many companies this run covers (given scope + selection + ad-hoc) and
  // how the watcher will batch them. Display only — caps come from the backend.
  const selectedCount =
    scope === "selected" || scope === "mix" ? companies.size : 0;
  const discoveryCount =
    scope === "new" || scope === "mix" ? (preview?.new.length ?? 0) : 0;
  const targetCount = selectedCount + discoveryCount + adHocCompanies.length;
  const batchSize = preview?.batch_size ?? 3;
  const parallel = preview?.max_parallel_batches ?? 3;
  const numBatches = targetCount > 0 ? Math.ceil(targetCount / batchSize) : 0;

  const handleConfirm = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const rec = await api.runChromeClaude({
        scope,
        companies: companyPickerShown ? Array.from(companies) : undefined,
        postedWithin,
        scoreAfter,
        adHocCompanies: adHocCompanies.length ? adHocCompanies : undefined,
        extraNotes: extraNotes.trim() || undefined,
      });
      completedFired.current = false;
      setTracked(rec);
      setPreview(null);
      onQueued?.();
    } catch (e) {
      setError(`Couldn't queue the run — ${msgOf(e)}`);
    } finally {
      setSubmitting(false);
    }
  }, [
    scope,
    companies,
    companyPickerShown,
    postedWithin,
    scoreAfter,
    adHocCompanies,
    extraNotes,
    onQueued,
  ]);

  // Poll the queue for the tracked run until it reaches a terminal status.
  useEffect(() => {
    if (!tracked || TERMINAL.has(tracked.status)) return;
    const timer = setInterval(async () => {
      try {
        const { requests } = await api.chromeClaudeStatus();
        const mine = requests.find((r) => r.id === tracked.id);
        if (mine) setTracked(mine);
      } catch {
        /* transient — keep polling */
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [tracked]);

  // Fire onCompleted once when a tracked run finishes successfully.
  useEffect(() => {
    if (tracked?.status === "done" && !completedFired.current) {
      completedFired.current = true;
      onCompleted?.();
    }
  }, [tracked, onCompleted]);

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        onClick={handleOpen}
        disabled={opening}
        className="rounded-lg border border-violet-300 bg-violet-50 px-3 py-2 text-sm font-semibold text-violet-700 transition hover:bg-violet-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-violet-500/30 dark:bg-violet-500/15 dark:text-violet-300 dark:hover:bg-violet-500/25"
        title="Queue a browser job-search run (Claude drives Chrome to scrape no-API companies and discover new ones)"
      >
        {opening ? "Loading…" : "🌐 Run Chrome Claude…"}
      </button>

      {tracked && <StatusChip rec={tracked} />}

      {error && !preview && (
        <span className="text-xs text-rose-600 dark:text-rose-400">{error}</span>
      )}

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
            className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col overflow-y-auto rounded-xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-800 dark:bg-slate-900"
          >
            <h3 className="mb-1 font-semibold text-slate-900 dark:text-slate-100">
              Run Chrome Claude
            </h3>
            <p className="mb-4 text-xs text-slate-500 dark:text-slate-400">
              Queues a run. A Claude Code watcher loop drives your browser and
              imports the results — keep a loop running for it to start.
            </p>

            {/* Scope */}
            <fieldset className="mb-4">
              <legend className="mb-1 text-xs font-semibold text-slate-700 dark:text-slate-300">
                What to search
              </legend>
              <div className="flex flex-col gap-1.5">
                <ScopeRadio
                  value="selected"
                  current={scope}
                  onChange={setScope}
                  label="Selected companies"
                  hint="Scrape careers pages of companies you already track."
                />
                <ScopeRadio
                  value="new"
                  current={scope}
                  onChange={setScope}
                  label="New companies"
                  hint="Discover roles via your LinkedIn searches (finds companies not on your list)."
                />
                <ScopeRadio
                  value="mix"
                  current={scope}
                  onChange={setScope}
                  label="Mix of both"
                  hint="Known companies plus discovery."
                />
              </div>
            </fieldset>

            {/* Companies */}
            {companyPickerShown && (
              <div className="mb-4">
                <div className="mb-1 flex items-center justify-between text-xs text-slate-600 dark:text-slate-400">
                  <span>
                    Companies —{" "}
                    <strong className="text-slate-900 dark:text-slate-100">
                      {companies.size}
                    </strong>{" "}
                    of {preview.selected.length}
                  </span>
                  <span className="flex gap-3">
                    <button
                      type="button"
                      onClick={selectAll}
                      className="text-violet-600 hover:underline dark:text-violet-400"
                    >
                      All
                    </button>
                    <button
                      type="button"
                      onClick={selectNone}
                      className="text-violet-600 hover:underline dark:text-violet-400"
                    >
                      None
                    </button>
                  </span>
                </div>
                <ul className="max-h-40 divide-y divide-slate-200 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 text-sm text-slate-700 dark:divide-slate-700 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
                  {preview.selected.map((t) => (
                    <li key={t.name}>
                      <label className="flex cursor-pointer items-center gap-3 px-3 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-700/50">
                        <input
                          type="checkbox"
                          checked={companies.has(t.name)}
                          onChange={() => toggleCompany(t.name)}
                          className="h-4 w-4 rounded accent-violet-600"
                        />
                        <span className="flex-1 truncate">{t.name}</span>
                        <span className="text-xs text-slate-400 dark:text-slate-500">
                          {t.platform}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
                {scope === "mix" && (
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    Plus {preview.new.length} discovery search
                    {preview.new.length === 1 ? "" : "es"}.
                  </p>
                )}
              </div>
            )}

            {scope === "new" && (
              <p className="mb-4 text-sm text-slate-600 dark:text-slate-300">
                Runs {preview.new.length} LinkedIn discovery search
                {preview.new.length === 1 ? "" : "es"} from your config.
              </p>
            )}

            {/* Ad-hoc companies (from the chat box; not in config) */}
            {adHocCompanies.length > 0 && (
              <div className="mb-4">
                <div className="mb-1 text-xs font-semibold text-slate-700 dark:text-slate-300">
                  Also search — Claude finds the careers page
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {adHocCompanies.map((name) => (
                    <span
                      key={name}
                      className="inline-flex items-center gap-1 rounded-full border border-violet-300 bg-violet-50 px-2 py-0.5 text-xs text-violet-700 dark:border-violet-500/30 dark:bg-violet-500/15 dark:text-violet-300"
                    >
                      {name}
                      <button
                        type="button"
                        onClick={() => removeAdHoc(name)}
                        className="opacity-60 hover:opacity-100"
                        aria-label={`Remove ${name}`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Posting date + score-after */}
            <div className="mb-4 flex flex-wrap items-center gap-4">
              <label className="text-sm text-slate-700 dark:text-slate-300">
                Posted within{" "}
                <select
                  value={postedWithin === null ? "any" : String(postedWithin)}
                  onChange={(e) =>
                    setPostedWithin(
                      e.target.value === "any" ? null : Number(e.target.value),
                    )
                  }
                  className="ml-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
                >
                  {preview.posted_within_options.map((o) => (
                    <option
                      key={o.label}
                      value={o.value === null ? "any" : String(o.value)}
                    >
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={scoreAfter}
                  onChange={(e) => setScoreAfter(e.target.checked)}
                  className="h-4 w-4 rounded accent-violet-600"
                />
                Score after import
                <span className="text-xs text-slate-400 dark:text-slate-500">(uses API tokens)</span>
              </label>
            </div>

            {/* Extra instructions */}
            <div className="mb-4">
              <label className="mb-1 block text-xs font-semibold text-slate-700 dark:text-slate-300">
                Extra instructions{" "}
                <span className="font-normal text-slate-400">(optional)</span>
              </label>
              <textarea
                value={extraNotes}
                onChange={(e) => setExtraNotes(e.target.value)}
                rows={2}
                placeholder="e.g. prioritize Iceberg + Spark roles; mid-level only"
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
              />
            </div>

            {targetCount > 0 && (
              <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
                {targetCount} {targetCount === 1 ? "company" : "companies"} →{" "}
                {numBatches} batch{numBatches === 1 ? "" : "es"}, {parallel} at a time.
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
                disabled={confirmDisabled}
                className="rounded-md bg-violet-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? "Queueing…" : "Queue run"}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// --- bits ----------------------------------------------------------------

function ScopeRadio({
  value,
  current,
  onChange,
  label,
  hint,
}: {
  value: ChromeClaudeScope;
  current: ChromeClaudeScope;
  onChange: (s: ChromeClaudeScope) => void;
  label: string;
  hint: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2">
      <input
        type="radio"
        name="chrome-claude-scope"
        checked={current === value}
        onChange={() => onChange(value)}
        className="mt-0.5 h-4 w-4 accent-violet-600"
      />
      <span className="text-sm">
        <span className="font-medium text-slate-800 dark:text-slate-200">{label}</span>
        <span className="block text-xs text-slate-500 dark:text-slate-400">{hint}</span>
      </span>
    </label>
  );
}

function StatusChip({ rec }: { rec: ChromeClaudeRequest }) {
  const tone =
    rec.status === "done"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
      : rec.status === "error"
        ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300"
        : rec.status === "running"
          ? "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300"
          : "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300";

  const labels: Record<string, string> = {
    pending: "Queued",
    running: "Running",
    done: "Done",
    error: "Failed",
  };

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs ${tone}`}
      title={summarize(rec)}
    >
      <span className="font-semibold">{labels[rec.status] ?? rec.status}</span>
      <span className="opacity-70">· Chrome Claude</span>
      {rec.status === "pending" && (
        <span className="opacity-70">(start a watcher loop to run)</span>
      )}
      {rec.status === "done" && rec.summary != null && (
        <span className="opacity-80">{summarize(rec)}</span>
      )}
      {rec.status === "error" && rec.error && (
        <span className="opacity-80 truncate max-w-[18rem]">{rec.error}</span>
      )}
    </span>
  );
}

// Render the summary (which the watcher writes as JSON or text) compactly.
function summarize(rec: ChromeClaudeRequest): string {
  const s = rec.summary;
  if (s == null) return "";
  if (typeof s === "string") return s;
  const parts: string[] = [];
  for (const [k, v] of Object.entries(s)) {
    if (v == null || (Array.isArray(v) && v.length === 0)) continue;
    parts.push(`${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`);
  }
  return parts.join(" · ");
}

function msgOf(e: unknown): string {
  return e instanceof ApiError ? `${e.status}: ${e.detail}` : (e as Error).message;
}
