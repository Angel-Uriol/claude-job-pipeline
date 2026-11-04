// "Finish your application" panel inside the job drawer:
//   - the apply checklist
//   - cover letter: write it (Claude, evidence-backed) / download / warnings
//   - review what Claude changed in the tailored resume, or go back to your wording
//   - print-ready page ("Save as PDF") — the .docx stays the main download

"use client";

import Link from "next/link";
import { useState } from "react";

import Checklist from "@/components/Checklist";
import ChangesView from "@/components/ChangesView";
import { ApiError, api } from "@/lib/api";
import type { Job } from "@/lib/types";

const BTN =
  "rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-100 disabled:opacity-50 " +
  "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : (e as Error).message;
}

export default function ApplyKit({ job, onUpdate }: { job: Job; onUpdate: (j: Job) => void }) {
  const [busy, setBusy] = useState<"letter" | "original" | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showChanges, setShowChanges] = useState(false);

  const refresh = async () => onUpdate(await api.getJob(job.key));

  const writeLetter = async () => {
    setBusy("letter");
    setError(null);
    try {
      const r = await api.coverLetter(job.key);
      setWarnings(r.warnings);
      await refresh();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  };

  const useOriginal = async () => {
    setBusy("original");
    setError(null);
    try {
      await api.resumeOriginal(job.key);
      setShowChanges(false);
      await refresh();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-4 dark:border-indigo-500/20 dark:bg-indigo-500/5">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-indigo-700 dark:text-indigo-300">
        Finish your application
      </h3>
      {job.checklist && <Checklist steps={job.checklist} />}

      <div className="mt-3 flex flex-wrap gap-2">
        {job.cover_letter_path ? (
          <a className={BTN} href={api.coverLetterUrl(job.key)}>
            ⬇ Cover letter (.docx)
          </a>
        ) : null}
        <button type="button" className={BTN} disabled={busy !== null} onClick={writeLetter}
          title="Claude writes a short letter using only facts from your resume.">
          {busy === "letter" ? "Writing…" : job.cover_letter_path ? "Rewrite cover letter" : "✉️ Write cover letter"}
        </button>
        {job.resume_path && (
          <>
            <button type="button" className={BTN} onClick={() => setShowChanges((s) => !s)}>
              {showChanges ? "Hide changes" : "🔍 Review what Claude changed"}
            </button>
            <Link className={BTN} href={`/print/resume?job=${encodeURIComponent(job.key)}`} target="_blank">
              🖨 Print / Save as PDF
            </Link>
          </>
        )}
      </div>

      {warnings.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
          {warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      )}
      {error && <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}

      {showChanges && job.resume_path && (
        <div className="mt-3">
          <ChangesView jobKey={job.key} />
          <button type="button" className={`${BTN} mt-2`} disabled={busy !== null} onClick={useOriginal}>
            {busy === "original" ? "Re-making…" : "↩ Use my original wording"}
          </button>
        </div>
      )}
    </div>
  );
}
