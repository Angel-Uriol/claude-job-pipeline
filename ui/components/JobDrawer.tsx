// Slide-out detail panel for one job. Shows full JD, justification,
// honest-bridge notes, sub-score bars, and the action buttons (Generate
// resume / I applied / Open posting / Download resume).

"use client";

import { useState } from "react";

import ApplyKit from "@/components/ApplyKit";
import Explain from "@/components/Explain";
import { ApiError, api } from "@/lib/api";
import { AXIS_LABELS, VERDICT_LABELS, scoreLabel } from "@/lib/plain";
import type { Job } from "@/lib/types";

interface Props {
  job: Job | null;
  onClose: () => void;
  onUpdate: (job: Job) => void;
  /** Soft-discard (not interested). Parent handles the API call + closing. */
  onDismiss: (job: Job) => void;
  /** Hard-delete from the store. Parent handles the API call + closing. */
  onDelete: (job: Job) => void;
}

// Shared secondary-button styling (Open posting / Download / I applied).
const SECONDARY_BTN =
  "rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-100 disabled:opacity-50 " +
  "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700";

export default function JobDrawer({
  job,
  onClose,
  onUpdate,
  onDismiss,
  onDelete,
}: Props) {
  const [busy, setBusy] = useState<"resume" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // ✨ premium read of this one posting (Claude in Chrome).
  const [premiumNote, setPremiumNote] = useState<string | null>(null);

  if (!job) return null;

  const handleDelete = () => {
    if (
      window.confirm(
        `Delete "${job.title}" at ${job.company} permanently?\n\n` +
          "It's removed from the store. If the posting is still live, a " +
          "future scrape may re-discover it — use Discard to keep it hidden instead.",
      )
    ) {
      onDelete(job);
    }
  };

  const pct = job.fit_score_pct;

  const handleGenerateResume = async () => {
    setBusy("resume");
    setError(null);
    try {
      await api.generateResume(job.key);
      // Refresh the job (now has resume_path stamped + maybe status=resume_only).
      const updated = await api.getJob(job.key);
      onUpdate(updated);
      // Trigger the download.
      window.open(api.downloadResumeUrl(job.key), "_blank");
    } catch (e) {
      setError(`Resume generation failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const handleApply = async () => {
    setBusy("apply");
    setError(null);
    try {
      const updated = await api.markApplied(job.key);
      onUpdate(updated);
    } catch (e) {
      setError(`Mark applied failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div
        className="fixed inset-0 z-30 bg-slate-900/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <aside className="fixed bottom-0 right-0 top-0 z-40 w-full overflow-y-auto border-l border-slate-200 bg-white shadow-xl sm:w-[560px] dark:border-slate-800 dark:bg-slate-900">
        <div className="sticky top-0 flex items-start justify-between gap-3 border-b border-slate-200 bg-white/90 px-5 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
          <div className="min-w-0">
            <h2 className="truncate font-semibold text-slate-900 dark:text-slate-100">
              {job.title}
            </h2>
            <div className="truncate text-sm text-slate-500 dark:text-slate-400">
              {job.company} · {job.team || "—"} · {job.location || "—"}
            </div>
            {job.is_target_company && (
              <div className="mt-1">
                <span
                  className="inline-block rounded bg-indigo-100 px-1.5 py-0.5 text-[11px] font-semibold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300"
                  title="On your target company list — its fit score gets a tier-weighted boost."
                >
                  🎯 Target company
                  {job.target_tier ? ` · Tier ${job.target_tier}` : ""} — fit-score boosted
                </span>
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-2xl leading-none text-slate-400 transition hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-200"
            title="Close"
          >
            ×
          </button>
        </div>

        <div className="space-y-4 p-5 text-sm text-slate-700 dark:text-slate-300">
          {/* Score + verdict + source */}
          <div className="grid grid-cols-3 gap-3">
            <Stat label="How well it fits" value={pct == null ? "—" : `${scoreLabel(job.fit_score)} (${pct}/100)`} />
            <Stat label="Suggestion" value={job.fit_verdict ? VERDICT_LABELS[job.fit_verdict] ?? job.fit_verdict : "—"} />
            <Stat label="Source" value={job.source} />
          </div>

          <ApplyKit job={job} onUpdate={onUpdate} />

          {!!(job.fit_reasons?.length || job.fit_gaps?.length) && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Section title="Why it fits">
                <ul className="space-y-1">{(job.fit_reasons ?? []).map((r) => <li key={r}>✓ {r}</li>)}</ul>
              </Section>
              <Section title="What's missing">
                <ul className="space-y-1">
                  {job.fit_gaps?.length ? job.fit_gaps.map((g) => <li key={g}>△ {g}</li>) : <li>No real gaps</li>}
                </ul>
              </Section>
            </div>
          )}

          {job.fit_justification && (
            <Section title="Claude's reasoning">
              <p className="leading-relaxed">{job.fit_justification}</p>
            </Section>
          )}

          {job.honest_bridge_notes && (
            <Section title="How to talk about the gaps (honestly)">
              <p className="rounded-r border-l-2 border-amber-500 bg-amber-50 px-3 py-2 leading-relaxed text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
                {job.honest_bridge_notes}
              </p>
            </Section>
          )}

          {job.fit_subscores && (
            <Section title="Score breakdown">
              <div className="space-y-1">
                {(
                  [
                    ["stack", AXIS_LABELS.stack],
                    ["level", AXIS_LABELS.level],
                    ["function", AXIS_LABELS.function],
                    ["bridgeability", AXIS_LABELS.bridgeability],
                    ["desirability", AXIS_LABELS.desirability],
                  ] as const
                ).map(([k, label]) => {
                  // Cast via unknown so TS doesn't insist on the index
                  // signature shape — the keys are statically checked.
                  const subs = job.fit_subscores as unknown as Record<string, number>;
                  const v = subs[k];
                  const w = Math.round(v * 10); // 1..10 → 10..100%
                  return (
                    <div key={k} className="flex items-center gap-2">
                      <span className="flex w-40 items-center text-xs text-slate-500 dark:text-slate-400">
                        {label}
                        <Explain term={k} />
                      </span>
                      <div className="h-2 flex-1 overflow-hidden rounded bg-slate-200 dark:bg-slate-700">
                        <div
                          className="h-full bg-indigo-600 dark:bg-indigo-500"
                          style={{ width: `${w}%` }}
                        />
                      </div>
                      <span className="w-10 text-right text-xs tabular-nums">
                        {w}
                      </span>
                    </div>
                  );
                })}
              </div>
            </Section>
          )}

          <Section title="Description">
            <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-sans text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
              {job.description}
            </pre>
          </Section>

          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
              {error}
            </div>
          )}

          {(job.description?.length ?? 0) < 1200 && !premiumNote && (
            <p className="rounded-lg bg-fuchsia-50 px-3 py-2 text-xs text-fuchsia-900 dark:bg-fuchsia-500/10 dark:text-fuchsia-200">
              ✨ This description looks incomplete. A premium read opens the posting in your Chrome
              and captures the full text, so scoring and resume tailoring see everything.
            </p>
          )}
          {premiumNote && (
            <p className="rounded-lg bg-fuchsia-50 px-3 py-2 text-xs text-fuchsia-900 dark:bg-fuchsia-500/10 dark:text-fuchsia-200">
              {premiumNote}
            </p>
          )}

          <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3 dark:border-slate-800">
            <button
              type="button"
              onClick={async () => {
                setError(null);
                try {
                  const r = await api.premiumQueue([], [job.key]);
                  setPremiumNote(
                    `✓ Queued a premium read. Launch it from the ✨ card on the Jobs page. ${r.usage.text}`,
                  );
                } catch (e) {
                  setError(e instanceof ApiError ? e.detail : (e as Error).message);
                }
              }}
              disabled={busy !== null || premiumNote !== null}
              title="Claude opens this posting in your Chrome and reads the full description (uses more tokens)."
              className="rounded-md border border-fuchsia-300 bg-fuchsia-50 px-3 py-2 text-sm font-semibold text-fuchsia-700 transition hover:bg-fuchsia-100 disabled:opacity-50 dark:border-fuchsia-500/30 dark:bg-fuchsia-500/15 dark:text-fuchsia-300"
            >
              ✨ Premium read
            </button>
            <button
              type="button"
              onClick={handleGenerateResume}
              disabled={busy !== null}
              className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:opacity-50"
            >
              {busy === "resume" ? "Generating…" : "📄 Generate resume"}
            </button>
            <button
              type="button"
              onClick={handleApply}
              disabled={busy !== null || job.status === "applied"}
              className={SECONDARY_BTN}
            >
              {job.status === "applied"
                ? "✓ Applied"
                : busy === "apply"
                  ? "Saving…"
                  : "✓ I applied"}
            </button>
            <a
              href={job.url}
              target="_blank"
              rel="noopener noreferrer"
              className={SECONDARY_BTN}
              onClick={() => {
                // Checklist: "Open the application page" — record it, quietly.
                api.markOpened(job.key).then(() => api.getJob(job.key)).then(onUpdate).catch(() => {});
              }}
            >
              ↗ Open posting
            </a>
            {job.resume_path && (
              <a
                href={api.downloadResumeUrl(job.key)}
                target="_blank"
                rel="noopener noreferrer"
                className={SECONDARY_BTN}
              >
                ⬇ Download resume
              </a>
            )}

            {/* Discard (soft) + Delete (hard) — pushed to the right. */}
            <button
              type="button"
              onClick={() => onDismiss(job)}
              disabled={busy !== null}
              className="ml-auto rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700 transition hover:bg-amber-100 disabled:opacity-50 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300 dark:hover:bg-amber-500/20"
              title="Not interested — hide from the list (kept so re-scrapes don't resurface it)"
            >
              🗑 Discard
            </button>
            <button
              type="button"
              onClick={handleDelete}
              disabled={busy !== null}
              className="rounded-md border border-rose-200 bg-white px-3 py-2 text-sm text-rose-600 transition hover:bg-rose-50 disabled:opacity-50 dark:border-rose-500/30 dark:bg-slate-800 dark:text-rose-400 dark:hover:bg-rose-500/10"
              title="Delete permanently from the store"
            >
              Delete
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/60">
      <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div className="mt-0.5 truncate font-semibold text-slate-900 dark:text-slate-100">
        {value}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="mb-1 text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {title}
      </h3>
      {children}
    </div>
  );
}
