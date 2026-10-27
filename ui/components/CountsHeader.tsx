// Top-of-page summary strip: title + last-refresh hint + count pills +
// the light/dark theme toggle. Display-only — counts come from /api/state.

import type { StateResponse } from "@/lib/types";

interface Props {
  state: StateResponse | null;
}

function humanizeAgo(iso: string | null): string {
  if (!iso) return "no data yet";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60_000);
  if (mins < 1) return "refreshed just now";
  if (mins < 60) return `refreshed ${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `refreshed ${hours}h ago`;
  return `refreshed ${Math.round(hours / 24)}d ago`;
}

export default function CountsHeader({ state }: Props) {
  const c = state?.counts ?? {
    total: 0,
    apply: 0,
    consider: 0,
    skipped: 0,
    applied: 0,
    starred: 0,
  };

  return (
    <header className="mb-6">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-50">
          Discover
        </h1>
        <span className="text-sm text-slate-500 dark:text-slate-400">
          {humanizeAgo(state?.last_refreshed ?? null)}
        </span>
      </div>
      <div className="mt-3 flex gap-2 flex-wrap text-sm">
        <Pill label="total" value={c.total} accent="neutral" />
        <Pill label="apply" value={c.apply} accent="green" />
        <Pill label="consider" value={c.consider} accent="amber" />
        <Pill label="skipped" value={c.skipped} accent="slate" />
        <Pill label="applied" value={c.applied} accent="indigo" />
        <Pill label="starred" value={c.starred} accent="yellow" />
      </div>
    </header>
  );
}

interface PillProps {
  label: string;
  value: number;
  accent?: "neutral" | "green" | "amber" | "slate" | "indigo" | "yellow";
}

function Pill({ label, value, accent = "neutral" }: PillProps) {
  // Literal class strings (no dynamic construction — Tailwind must see them).
  // Each accent has a soft pastel light tone + a translucent dark tone.
  const accentClasses: Record<NonNullable<PillProps["accent"]>, string> = {
    neutral:
      "bg-white border border-slate-200 text-slate-600 dark:bg-slate-900 dark:border-slate-700 dark:text-slate-300",
    green:
      "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    amber:
      "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    slate:
      "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
    indigo:
      "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300",
    yellow:
      "bg-yellow-100 text-yellow-700 dark:bg-yellow-500/15 dark:text-yellow-200",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${accentClasses[accent]}`}
    >
      <span className="tabular-nums">{value}</span>
      <span className="text-[10px] uppercase tracking-wide">{label}</span>
    </span>
  );
}
