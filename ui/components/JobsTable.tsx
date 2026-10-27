// Table of Jobs — uses CSS-grid for stable column alignment.
// Each row is clickable; clicking opens the drawer. Star toggle and
// score badge live inline. Sort by clicking a header column.

"use client";

import { useMemo } from "react";

import { api } from "@/lib/api";
import type { Job } from "@/lib/types";

interface Props {
  jobs: Job[];
  deadCompanies: Set<string>;
  sort: string;
  onSortChange: (sort: string) => void;
  onJobClick: (job: Job) => void;
  onStarToggle: (job: Job) => void;
  onDiscard: (job: Job) => void;
}

// Trailing 2rem column = the discard (✕) button.
const GRID = "2rem 3rem 2fr 1fr 1fr 6rem 5rem 5rem 2rem";

export default function JobsTable({
  jobs,
  deadCompanies,
  sort,
  onSortChange,
  onJobClick,
  onStarToggle,
  onDiscard,
}: Props) {
  if (jobs.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-8 text-center italic text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
        No jobs match the current filters. Try widening Posted within or
        lowering Min score, or click <span className="font-mono">▶ Run daily</span> to refresh.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div
        className="grid items-center gap-3 border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-400"
        style={{ gridTemplateColumns: GRID }}
      >
        <span />{/* star */}
        <SortHeader col="fit_score" label="Score" sort={sort} onChange={onSortChange} />
        <SortHeader col="title" label="Title · Team" sort={sort} onChange={onSortChange} />
        <SortHeader col="company" label="Company" sort={sort} onChange={onSortChange} />
        <span>Location</span>
        <SortHeader col="posted" label="Posted" sort={sort} onChange={onSortChange} />
        <SortHeader col="status" label="Status" sort={sort} onChange={onSortChange} />
        <span className="text-right">Open</span>
        <span />{/* discard */}
      </div>

      {jobs.map((j) => (
        <Row
          key={j.key}
          job={j}
          isDead={deadCompanies.has(j.company.toLowerCase())}
          onClick={() => onJobClick(j)}
          onStar={() => onStarToggle(j)}
          onDiscard={() => onDiscard(j)}
        />
      ))}
    </div>
  );
}

// ===== sort header =====

function SortHeader({
  col,
  label,
  sort,
  onChange,
}: {
  col: string;
  label: string;
  sort: string;
  onChange: (sort: string) => void;
}) {
  const [activeCol, activeDir] = sort.split(":");
  const isActive = activeCol === col;
  const next = isActive && activeDir === "desc" ? `${col}:asc` : `${col}:desc`;
  return (
    <button
      type="button"
      className="text-left transition hover:text-slate-900 dark:hover:text-slate-100"
      onClick={() => onChange(next)}
    >
      {label}
      {isActive && (
        <span className="ml-1 text-indigo-600 dark:text-indigo-400">
          {activeDir === "asc" ? "↑" : "↓"}
        </span>
      )}
    </button>
  );
}

// ===== row =====

function Row({
  job,
  isDead,
  onClick,
  onStar,
  onDiscard,
}: {
  job: Job;
  isDead: boolean;
  onClick: () => void;
  onStar: () => void;
  onDiscard: () => void;
}) {
  const pct = job.fit_score_pct;
  const scoreClass = useMemo(() => {
    if (pct == null) return "bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500";
    if (pct >= 70) return "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300";
    if (pct >= 50) return "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300";
    return "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300";
  }, [pct]);

  return (
    <div
      className="grid cursor-pointer items-center gap-3 border-b border-slate-100 px-3 py-2.5 text-sm transition last:border-b-0 hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/40"
      style={{ gridTemplateColumns: GRID }}
      onClick={onClick}
    >
      {/* Star toggle — stop click propagation so we don't open the drawer. */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onStar();
        }}
        className={`text-lg leading-none ${
          job.starred
            ? "text-amber-500"
            : "text-slate-300 hover:text-amber-400 dark:text-slate-600 dark:hover:text-amber-400"
        }`}
        title={job.starred ? "Unstar" : "Star"}
      >
        {job.starred ? "★" : "☆"}
      </button>

      {/* Score */}
      <span
        className={`rounded px-1.5 py-0.5 text-center text-sm font-semibold tabular-nums ${scoreClass}`}
      >
        {pct ?? "—"}
      </span>

      {/* Title · Team */}
      <div className="min-w-0">
        <div className="truncate font-medium text-slate-900 dark:text-slate-100">
          {job.title}
        </div>
        {job.team && (
          <div className="truncate text-xs text-slate-500 dark:text-slate-400">
            {job.team}
          </div>
        )}
      </div>

      {/* Company (with TARGET + DEAD badges) */}
      <div className="truncate text-slate-700 dark:text-slate-300">
        {job.company}
        {job.is_target_company && <TargetBadge tier={job.target_tier} />}
        {isDead && (
          <span className="ml-1 inline-block rounded bg-slate-700 px-1 py-0.5 align-middle text-[10px] font-medium text-white dark:bg-slate-600">
            DEAD
          </span>
        )}
      </div>

      {/* Location — colored by CA/remote tag */}
      <LocationTag location={job.location} kind={job.location_kind} />

      {/* Posted (color-coded relative) */}
      <PostedTag days={job.days_since_posted} />

      {/* Status */}
      <StatusBadge status={job.status} />

      {/* Open posting (external link, stop propagation) */}
      <a
        href={job.url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(e) => e.stopPropagation()}
        className="text-right text-xs text-indigo-600 hover:underline dark:text-indigo-400"
      >
        ↗
      </a>

      {/* Discard (✕) — soft dismiss; stop propagation so the drawer doesn't open */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onDiscard();
        }}
        title="Discard (not interested)"
        className="text-center text-sm leading-none text-slate-300 transition hover:text-rose-500 dark:text-slate-600 dark:hover:text-rose-400"
      >
        ✕
      </button>
    </div>
  );
}

// ===== location tag =====

// `kind` is computed server-side from the profile's location tokens.
function LocationTag({ location, kind }: { location: string; kind?: string }) {
  const styles: Record<string, string> = {
    preferred: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    remote: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
    other: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
    unknown: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  };
  return (
    <span
      className={`inline-block max-w-full truncate rounded px-1.5 py-0.5 text-xs ${styles[kind ?? "unknown"] ?? styles.unknown}`}
      title={location || "(unknown)"}
    >
      {location || "?"}
    </span>
  );
}

// ===== posted tag =====

function PostedTag({ days }: { days: number | null }) {
  if (days == null) {
    return (
      <span className="text-xs italic text-slate-400 dark:text-slate-500" title="unknown">
        —
      </span>
    );
  }
  let color = "text-rose-600 dark:text-rose-400";
  if (days <= 8) color = "text-emerald-700 dark:text-emerald-400";
  else if (days <= 15) color = "text-amber-700 dark:text-amber-400";
  else if (days <= 23) color = "text-slate-500 dark:text-slate-400";

  const label =
    days === 0
      ? "today"
      : days === 1
        ? "1d ago"
        : days < 7
          ? `${days}d ago`
          : days < 14
            ? "1w ago"
            : days < 30
              ? `${Math.round(days / 7)}w ago`
              : `${Math.round(days / 30)}mo ago`;

  return <span className={`text-xs tabular-nums ${color}`}>{label}</span>;
}

// ===== target-company badge =====

// Shown next to the company name when the job is at one of the 100 playbook
// target companies. The tier (1 = strongest) also drives the fit-score boost.
function TargetBadge({ tier }: { tier: number | null }) {
  return (
    <span
      className="ml-1 inline-block rounded bg-indigo-100 px-1 py-0.5 align-middle text-[10px] font-semibold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300"
      title={`Target company${tier ? ` · Tier ${tier}` : ""} — on your target list, so it gets a fit-score boost`}
    >
      🎯{tier ? ` T${tier}` : ""}
    </span>
  );
}

// ===== status badge =====

function StatusBadge({ status }: { status: string }) {
  // Matches the Excel tracker palette (master profile §10 / spec §5).
  const palette: Record<string, string> = {
    new: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    pending: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    applied: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
    resume_only: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
    rejected: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
    auto_rejected: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
    dead: "bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300",
    excluded: "bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300",
    skipped: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
    dismissed: "bg-slate-200 text-slate-500 dark:bg-slate-700 dark:text-slate-400",
    mismatch: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
    assessment_passed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  };
  const cls =
    palette[status] || "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
  return (
    <span className={`truncate rounded px-1.5 py-0.5 text-xs ${cls}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

// Re-export api so the parent doesn't need a separate import path.
export { api };
