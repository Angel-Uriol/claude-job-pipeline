// The apply checklist: resume → cover letter → open the application →
// mark applied → follow up → interview prep. The steps and their "done"
// state come from the backend (job_pipeline/checklist.py).

import type { ChecklistStep } from "@/lib/types";

function dueText(step: ChecklistStep): string {
  if (!step.due_at) return "";
  const d = new Date(step.due_at);
  if (Number.isNaN(d.getTime())) return "";
  return step.due_now ? " — due now" : ` — ${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

export default function Checklist({ steps, compact = false }: { steps: ChecklistStep[]; compact?: boolean }) {
  return (
    <ol className={compact ? "flex flex-wrap gap-x-3 gap-y-1 text-xs" : "space-y-1.5 text-sm"} aria-label="Application checklist">
      {steps.map((s) => (
        <li key={s.id} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className={`grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] font-bold ${
              s.done
                ? "bg-emerald-500 text-white"
                : s.due_now
                  ? "border-2 border-amber-500"
                  : "border border-slate-300 dark:border-slate-600"
            }`}
          >
            {s.done ? "✓" : ""}
          </span>
          <span className={s.done ? "text-slate-400 line-through dark:text-slate-500" : "text-slate-700 dark:text-slate-200"}>
            {s.label}
            {s.optional && !s.done ? " (optional)" : ""}
            {!s.done && dueText(s)}
          </span>
          <span className="sr-only">{s.done ? "done" : "not done"}</span>
        </li>
      ))}
    </ol>
  );
}
