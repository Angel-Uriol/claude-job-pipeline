// Filter bar — posted_within, location, min_score, verdict, search.
// All filters apply live. Multi-select verdict/status is sent via the
// client-side post-filter since the backend's `verdict` param is single-value.

"use client";

import type {
  FilterState,
  LocationFilter,
  PostedWithin,
  Verdict,
} from "@/lib/types";

interface Props {
  filters: FilterState;
  onChange: (next: FilterState) => void;
}

const POSTED_OPTIONS: PostedWithin[] = [8, 15, 23, 30, 60, "all"];
const LOCATION_OPTIONS: { value: LocationFilter; label: string }[] = [
  { value: "preferred", label: "My locations" },
  { value: "remote", label: "Remote" },
  { value: "preferred_or_remote", label: "Both" },
  { value: "all", label: "All" },
];

// Shared input/select styling so light + dark stay consistent.
const FIELD =
  "rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 " +
  "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200";

export default function FilterBar({ filters, onChange }: Props) {
  // Helper to immutably toggle a verdict.
  const toggleVerdict = (v: Verdict) => {
    const next = new Set(filters.verdict);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    onChange({ ...filters, verdict: next });
  };

  return (
    <section className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-sm">
        {/* Posted within */}
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Posted within:</span>
          <div className="flex gap-1">
            {POSTED_OPTIONS.map((opt) => (
              <button
                key={String(opt)}
                type="button"
                onClick={() => onChange({ ...filters, posted_within: opt })}
                className={`rounded-md px-2 py-1 text-xs transition ${
                  filters.posted_within === opt
                    ? "bg-indigo-600 text-white shadow-sm"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
                }`}
              >
                {opt === "all" ? "All" : `${opt}d`}
              </button>
            ))}
          </div>
        </div>

        {/* Location */}
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Location:</span>
          <select
            value={filters.location}
            onChange={(e) =>
              onChange({
                ...filters,
                location: e.target.value as LocationFilter,
              })
            }
            className={FIELD}
          >
            {LOCATION_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {/* Min score */}
        <div className="flex items-center gap-2">
          <label className="text-slate-500 dark:text-slate-400" htmlFor="min-score">
            Min score:
          </label>
          <input
            id="min-score"
            type="range"
            min={0}
            max={100}
            step={5}
            value={filters.min_score}
            onChange={(e) =>
              onChange({ ...filters, min_score: Number(e.target.value) })
            }
            className="w-32 accent-indigo-600"
          />
          <span className="w-8 text-right font-semibold tabular-nums text-slate-700 dark:text-slate-200">
            {filters.min_score}
          </span>
        </div>

        {/* Verdict checkboxes */}
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Verdict:</span>
          {(["apply", "consider", "skip"] as Verdict[]).map((v) => (
            <label
              key={v}
              className="flex cursor-pointer select-none items-center gap-1 text-slate-700 dark:text-slate-300"
            >
              <input
                type="checkbox"
                checked={filters.verdict.has(v)}
                onChange={() => toggleVerdict(v)}
                className="accent-indigo-600"
              />
              <span>{v}</span>
            </label>
          ))}
        </div>

        {/* Starred only */}
        <label className="flex cursor-pointer select-none items-center gap-1 text-slate-700 dark:text-slate-300">
          <input
            type="checkbox"
            checked={filters.starred_only}
            onChange={(e) =>
              onChange({ ...filters, starred_only: e.target.checked })
            }
            className="accent-indigo-600"
          />
          <span>★ starred</span>
        </label>

        {/* Show discarded */}
        <label className="flex cursor-pointer select-none items-center gap-1 text-slate-700 dark:text-slate-300">
          <input
            type="checkbox"
            checked={filters.show_dismissed}
            onChange={(e) =>
              onChange({ ...filters, show_dismissed: e.target.checked })
            }
            className="accent-indigo-600"
          />
          <span>show discarded/applied</span>
        </label>

        {/* Search */}
        <div className="flex min-w-[12rem] flex-1 items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Search:</span>
          <input
            type="search"
            placeholder="title / company / description"
            value={filters.q}
            onChange={(e) => onChange({ ...filters, q: e.target.value })}
            className={`flex-1 ${FIELD} placeholder:text-slate-400 dark:placeholder:text-slate-500`}
          />
        </div>
      </div>
    </section>
  );
}
