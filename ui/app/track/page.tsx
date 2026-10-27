// Track tab — application tracker over the built-in SQLite tracker.
// Reads/updates via /api/tracker/*. Resume GENERATION lives in Discover;
// here we only download the tailored resume the handoff carried over.

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "@/lib/api";
import {
  TRACK_STATUSES,
  type TrackStatus,
  type TrackedJob,
  type TrackerStats,
} from "@/lib/types";

const FIELD =
  "rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 " +
  "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200";

// Interview stages that roll up into the "in flight" stat card.
const IN_FLIGHT = ["recruiter_screen", "phone_screen", "take_home", "onsite", "offer"];

function statusTone(status: string): string {
  const green = "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300";
  const blue = "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300";
  const amber = "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300";
  const rose = "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300";
  const slate = "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300";
  if (status === "offer" || status === "applied") return green;
  if (IN_FLIGHT.includes(status)) return blue;
  if (status === "to_apply" || status === "new") return amber;
  if (["rejected", "auto_rejected"].includes(status)) return rose;
  return slate;
}

export default function TrackPage() {
  const [jobs, setJobs] = useState<TrackedJob[]>([]);
  const [ready, setReady] = useState(true);
  const [stats, setStats] = useState<TrackerStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [search, setSearch] = useState("");
  const [location, setLocation] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [verdictFilter, setVerdictFilter] = useState("all");
  const [company, setCompany] = useState("all");
  const [sort, setSort] = useState("score");
  const [showHidden, setShowHidden] = useState(false);

  const [selected, setSelected] = useState<Set<string>>(new Set());

  const filters = useMemo(
    () => ({
      search,
      location,
      verdict: verdictFilter,
      status: statusFilter,
      company,
      sort,
      hidden: showHidden ? "all" : "exclude",
    }),
    [search, location, verdictFilter, statusFilter, company, sort, showHidden],
  );

  const load = useCallback(async () => {
    try {
      const [res, st] = await Promise.all([
        api.trackerJobs(filters),
        api.trackerStats(),
      ]);
      setJobs(res.jobs);
      setReady(res.tracker_ready);
      setStats(st);
      setError(null);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status}: ${e.detail}` : (e as Error).message;
      setError(`Couldn't load the tracker — ${msg}`);
    }
  }, [filters]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  // --- per-job actions (optimistic) ---
  const onStatus = useCallback(async (id: string, status: TrackStatus) => {
    setJobs((prev) => prev.map((j) => (j.id === id ? { ...j, status } : j)));
    try {
      await api.trackerSetStatus(id, status);
    } catch (e) {
      setError(`Status update failed: ${(e as Error).message}`);
    }
  }, []);

  const onStar = useCallback(async (id: string) => {
    setJobs((prev) => prev.map((j) => (j.id === id ? { ...j, starred: j.starred ? 0 : 1 } : j)));
    try {
      await api.trackerToggleStar(id);
    } catch (e) {
      setError(`Star failed: ${(e as Error).message}`);
    }
  }, []);

  const onHide = useCallback(
    async (id: string) => {
      if (!showHidden) setJobs((prev) => prev.filter((j) => j.id !== id));
      try {
        await api.trackerToggleHide(id);
      } catch (e) {
        setError(`Hide failed: ${(e as Error).message}`);
      }
    },
    [showHidden],
  );

  const onDelete = useCallback(async (id: string, label: string) => {
    if (!window.confirm(`Delete "${label}" from the tracker permanently?`)) return;
    setJobs((prev) => prev.filter((j) => j.id !== id));
    try {
      await api.trackerDelete(id);
    } catch (e) {
      setError(`Delete failed: ${(e as Error).message}`);
    }
  }, []);

  const onNotes = useCallback(async (id: string, notes: string) => {
    try {
      await api.trackerSetNotes(id, notes);
    } catch (e) {
      setError(`Notes save failed: ${(e as Error).message}`);
    }
  }, []);

  // --- bulk ---
  const toggleSelect = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const bulkStatus = useCallback(
    async (status: TrackStatus) => {
      const ids = Array.from(selected);
      if (ids.length === 0) return;
      setJobs((prev) => prev.map((j) => (selected.has(j.id) ? { ...j, status } : j)));
      setSelected(new Set());
      try {
        await api.trackerBulkStatus(ids, status);
      } catch (e) {
        setError(`Bulk update failed: ${(e as Error).message}`);
      }
    },
    [selected],
  );

  // --- derived ---
  const statCards = useMemo(() => {
    const s = stats?.by_status ?? {};
    const inFlight = IN_FLIGHT.reduce((acc, k) => acc + (s[k] ?? 0), 0);
    const rejected = (s["rejected"] ?? 0) + (s["auto_rejected"] ?? 0);
    return [
      { label: "Total", value: stats?.total ?? 0, tone: "neutral" },
      { label: "to_apply", value: s["to_apply"] ?? 0, tone: "amber" },
      { label: "applied", value: s["applied"] ?? 0, tone: "blue" },
      { label: "in flight", value: inFlight, tone: "green" },
      { label: "rejected", value: rejected, tone: "rose" },
    ] as const;
  }, [stats]);

  const companyChips = useMemo(() => {
    const bc = stats?.by_company ?? {};
    return Object.entries(bc).sort((a, b) => b[1] - a[1]);
  }, [stats]);

  return (
    <main className="mx-auto max-w-[1200px] px-4 py-6">
      <header className="mb-4 flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-50">
            Track
          </h1>
          <span className="text-sm text-slate-500 dark:text-slate-400">
            {jobs.length} shown
          </span>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={api.trackerCsvUrl(filters)}
            className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            ⬇ Export CSV
          </a>
          <button
            type="button"
            onClick={() => load()}
            className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            ↻ Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      {!ready ? (
        <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
          The tracker isn&apos;t set up yet. Go to the{" "}
          <span className="font-semibold">Discover</span> tab and hit{" "}
          <span className="font-mono">📨 Send to tracker</span> to populate it.
        </div>
      ) : (
        <>
          {/* Stat cards */}
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
            {statCards.map((c) => (
              <StatCard key={c.label} label={c.label} value={c.value} tone={c.tone} />
            ))}
          </div>

          {/* Filters */}
          <section className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-3 rounded-xl border border-slate-200 bg-white p-4 text-sm shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <input
              type="search"
              placeholder="search company / role / notes…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className={`min-w-[14rem] flex-1 ${FIELD} placeholder:text-slate-400 dark:placeholder:text-slate-500`}
            />
            <select value={location} onChange={(e) => setLocation(e.target.value)} className={FIELD}>
              <option value="all">All locations</option>
              <option value="preferred">My locations</option>
              <option value="remote">Remote</option>
            </select>
            <select value={sort} onChange={(e) => setSort(e.target.value)} className={FIELD}>
              <option value="score">Sort: fit score</option>
              <option value="applied_at">Sort: applied date</option>
              <option value="company">Sort: company</option>
            </select>

            <PillGroup
              label="Verdict"
              value={verdictFilter}
              onChange={setVerdictFilter}
              options={["all", "APPLY", "CONSIDER", "SKIP"]}
            />
            <PillGroup
              label="Status"
              value={statusFilter}
              onChange={setStatusFilter}
              options={["all", "new", "to_apply", "applied", "rejected"]}
            />

            <label className="flex cursor-pointer items-center gap-1 text-slate-700 dark:text-slate-300">
              <input
                type="checkbox"
                checked={showHidden}
                onChange={(e) => setShowHidden(e.target.checked)}
                className="accent-indigo-600"
              />
              show hidden
            </label>
          </section>

          {/* Company chips */}
          {companyChips.length > 0 && (
            <div className="mb-4 flex flex-wrap gap-1.5">
              {company !== "all" && (
                <button
                  type="button"
                  onClick={() => setCompany("all")}
                  className="rounded-full bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white"
                >
                  {company} ✕
                </button>
              )}
              {companyChips
                .filter(([name]) => name !== company)
                .map(([name, n]) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => setCompany(name)}
                    className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 transition hover:border-indigo-300 hover:bg-indigo-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-indigo-500/40 dark:hover:bg-indigo-500/10"
                  >
                    {name} <span className="text-slate-400 dark:text-slate-500">{n}</span>
                  </button>
                ))}
            </div>
          )}

          {/* Bulk bar */}
          {selected.size > 0 && (
            <div className="mb-3 flex items-center gap-3 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm dark:border-indigo-500/30 dark:bg-indigo-500/10">
              <span className="font-medium text-indigo-700 dark:text-indigo-300">
                {selected.size} selected
              </span>
              <select
                className={FIELD}
                defaultValue=""
                onChange={(e) => {
                  if (e.target.value) bulkStatus(e.target.value as TrackStatus);
                  e.target.value = "";
                }}
              >
                <option value="">set status…</option>
                {TRACK_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => setSelected(new Set())}
                className="text-xs text-slate-500 hover:underline dark:text-slate-400"
              >
                clear
              </button>
            </div>
          )}

          {/* List */}
          {jobs.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white p-8 text-center italic text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
              No applications match the current filters.
            </div>
          ) : (
            <div className="space-y-2">
              {jobs.map((j) => (
                <TrackCard
                  key={j.id}
                  job={j}
                  selected={selected.has(j.id)}
                  onSelect={() => toggleSelect(j.id)}
                  onStatus={(s) => onStatus(j.id, s)}
                  onStar={() => onStar(j.id)}
                  onHide={() => onHide(j.id)}
                  onDelete={() => onDelete(j.id, `${j.company} — ${j.role}`)}
                  onNotes={(n) => onNotes(j.id, n)}
                />
              ))}
            </div>
          )}
        </>
      )}
    </main>
  );
}

// --- stat card ---

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "neutral" | "amber" | "blue" | "green" | "rose";
}) {
  const tones: Record<string, string> = {
    neutral: "border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900",
    amber: "border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10",
    blue: "border-sky-200 bg-sky-50 dark:border-sky-500/30 dark:bg-sky-500/10",
    green: "border-emerald-200 bg-emerald-50 dark:border-emerald-500/30 dark:bg-emerald-500/10",
    rose: "border-rose-200 bg-rose-50 dark:border-rose-500/30 dark:bg-rose-500/10",
  };
  return (
    <div className={`rounded-xl border px-4 py-3 text-center ${tones[tone]}`}>
      <div className="text-2xl font-bold tabular-nums text-slate-900 dark:text-slate-100">
        {value}
      </div>
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
    </div>
  );
}

// --- filter pill group ---

function PillGroup({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <div className="flex flex-wrap gap-1">
        {options.map((o) => (
          <button
            key={o}
            type="button"
            onClick={() => onChange(o)}
            className={`rounded-full px-2 py-0.5 text-xs transition ${
              value === o
                ? "bg-indigo-600 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
            }`}
          >
            {o === "all" ? "All" : o.replace(/_/g, " ")}
          </button>
        ))}
      </div>
    </div>
  );
}

// --- one application card ---

function TrackCard({
  job,
  selected,
  onSelect,
  onStatus,
  onStar,
  onHide,
  onDelete,
  onNotes,
}: {
  job: TrackedJob;
  selected: boolean;
  onSelect: () => void;
  onStatus: (s: TrackStatus) => void;
  onStar: () => void;
  onHide: () => void;
  onDelete: () => void;
  onNotes: (notes: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [notes, setNotesLocal] = useState(job.notes ?? "");
  const score = job.fit_score == null ? null : Math.round(job.fit_score * 10);
  const scoreTone =
    score == null
      ? "bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500"
      : score >= 80
        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
        : score >= 50
          ? "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
          : "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300";

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-3">
        <input type="checkbox" checked={selected} onChange={onSelect} className="accent-indigo-600" />
        <button
          type="button"
          onClick={onStar}
          className={`text-lg leading-none ${job.starred ? "text-amber-500" : "text-slate-300 hover:text-amber-400 dark:text-slate-600"}`}
          title={job.starred ? "Unstar" : "Star"}
        >
          {job.starred ? "★" : "☆"}
        </button>
        <span className={`min-w-[2.5rem] rounded px-1.5 py-0.5 text-center text-sm font-semibold tabular-nums ${scoreTone}`}>
          {score ?? "—"}
        </span>

        <button type="button" onClick={() => setOpen((o) => !o)} className="min-w-0 flex-1 text-left">
          <div className="truncate font-medium text-slate-900 dark:text-slate-100">
            {job.company} <span className="text-slate-400">·</span> {job.role}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
            {job.role_tier && <Badge>{`Tier ${job.role_tier}`}</Badge>}
            {job.location_pass && <Badge>{job.location_pass}</Badge>}
            {job.verdict && <Badge>{job.verdict}</Badge>}
            <span className={`rounded px-1.5 py-0.5 ${statusTone(job.status)}`}>
              {job.status.replace(/_/g, " ")}
            </span>
            {job.applied_at && <span>applied {job.applied_at.slice(0, 10)}</span>}
          </div>
        </button>

        <select
          value={job.status}
          onChange={(e) => onStatus(e.target.value as TrackStatus)}
          className={`${FIELD} font-medium`}
          title="Application status"
        >
          {TRACK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>

        {job.url && (
          <a href={job.url} target="_blank" rel="noopener noreferrer" className="text-xs text-indigo-600 hover:underline dark:text-indigo-400" title="Open posting">
            ↗
          </a>
        )}
        {job.resume_file_path && (
          <a href={api.trackerResumeDownloadUrl(job.id)} target="_blank" rel="noopener noreferrer" className="text-xs text-indigo-600 hover:underline dark:text-indigo-400" title="Download resume">
            ⬇
          </a>
        )}
        <button type="button" onClick={onHide} title="Hide from tracker" className="text-sm text-slate-300 hover:text-slate-500 dark:text-slate-600 dark:hover:text-slate-400">
          ⊘
        </button>
        <button type="button" onClick={onDelete} title="Delete permanently" className="text-sm text-slate-300 hover:text-rose-500 dark:text-slate-600 dark:hover:text-rose-400">
          ✕
        </button>
      </div>

      {open && (
        <div className="mt-3 space-y-3 border-t border-slate-200 pt-3 text-sm dark:border-slate-800">
          <div className="space-y-1">
            {(
              [
                ["stack", "Stack"],
                ["level", "Level"],
                ["function", "Function"],
                ["bridgeability", "Bridge"],
                ["desirability", "Desire"],
              ] as const
            ).map(([k, label]) => {
              const v = job.fit_breakdown[k];
              const w = v == null ? 0 : Math.round(v * 10);
              return (
                <div key={k} className="flex items-center gap-2">
                  <span className="w-16 text-xs text-slate-500 dark:text-slate-400">{label}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded bg-slate-200 dark:bg-slate-700">
                    <div className="h-full bg-indigo-600 dark:bg-indigo-500" style={{ width: `${w}%` }} />
                  </div>
                  <span className="w-8 text-right text-xs tabular-nums">{w}</span>
                </div>
              );
            })}
          </div>

          {job.bridge_notes && (
            <p className="rounded-r border-l-2 border-amber-500 bg-amber-50 px-3 py-2 leading-relaxed text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
              {job.bridge_notes}
            </p>
          )}

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Notes
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotesLocal(e.target.value)}
              onBlur={() => {
                if (notes !== (job.notes ?? "")) onNotes(notes);
              }}
              rows={2}
              className={`w-full ${FIELD}`}
              placeholder="your notes…"
            />
          </div>
        </div>
      )}
    </div>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
      {children}
    </span>
  );
}
