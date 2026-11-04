// "What Claude changed": your original resume wording next to the tailored
// version, with changed words highlighted. Numbers must stay the same — if a
// bullet's numbers differ, it's flagged so you can fix it before sending.

"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { ResumeChanges } from "@/lib/types";

/** Word-level highlight of what's new in `after` compared with `before`. */
function Highlight({ before, after }: { before: string; after: string }) {
  const old = new Set(before.toLowerCase().split(/\s+/));
  return (
    <>
      {after.split(/(\s+)/).map((w, i) =>
        /\s+/.test(w) || old.has(w.toLowerCase()) ? (
          <span key={i}>{w}</span>
        ) : (
          <mark key={i} className="rounded bg-emerald-100 px-0.5 text-inherit dark:bg-emerald-500/20">
            {w}
          </mark>
        ),
      )}
    </>
  );
}

export default function ChangesView({ jobKey }: { jobKey: string }) {
  const [data, setData] = useState<ResumeChanges | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .resumeChanges(jobKey)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.detail : (e as Error).message));
  }, [jobKey]);

  if (error) return <p className="text-xs text-rose-600">{error}</p>;
  if (!data) return <p className="text-xs text-slate-500">Loading…</p>;
  if (!data.tailored || !data.changes)
    return (
      <p className="text-xs text-slate-600 dark:text-slate-300">
        This resume uses your original wording — Claude didn&apos;t change anything.
      </p>
    );

  const c = data.changes;
  const changed = c.bullets.filter((b) => b.changed);
  return (
    <div className="space-y-3 text-xs">
      <p className="text-slate-600 dark:text-slate-300">
        Claude reworded {changed.length} of {c.bullets.length} bullet points
        {c.summary.changed ? " and your summary" : ""}. New words are{" "}
        <mark className="rounded bg-emerald-100 px-0.5 dark:bg-emerald-500/20">highlighted</mark>. Facts and
        numbers should be unchanged.
      </p>
      {c.summary.changed && (
        <Pair label="Summary" before={c.summary.before} after={c.summary.after} ok />
      )}
      {changed.map((b) => (
        <Pair key={b.id} label={b.where} before={b.before} after={b.after} ok={b.numbers_kept} />
      ))}
    </div>
  );
}

function Pair({ label, before, after, ok }: { label: string; before: string; after: string; ok: boolean }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900">
      <p className="mb-1 font-semibold text-slate-500">{label}</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <p className="text-slate-500 line-through decoration-slate-300 dark:decoration-slate-600">{before}</p>
        <p className="text-slate-800 dark:text-slate-100">
          <Highlight before={before} after={after} />
        </p>
      </div>
      {!ok && (
        <p className="mt-1 text-amber-700 dark:text-amber-300">
          ⚠ The numbers here differ from your original — check this line before sending.
        </p>
      )}
    </div>
  );
}
