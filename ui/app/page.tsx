// Dashboard — the only page in v0 of the Next.js UI.
//
// Composition:
//   <CountsHeader />     top-line totals + last-refreshed
//   <PipelinePanel />    Run daily + per-phase buttons + progress strip
//   <FilterBar />        posted_within, location, min_score, verdict, search
//   <JobsTable />        the work surface
//   <JobDrawer />        slide-out detail panel when a row is clicked
//
// State lives at this top level so a SSE-driven pipeline finish can
// trigger a single refetch that updates counts AND the table together.

"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import BulkResumesButton from "@/components/BulkResumesButton";
import ChromeClaudeButton from "@/components/ChromeClaudeButton";
import ChromeClaudeChat from "@/components/ChromeClaudeChat";
import CountsHeader from "@/components/CountsHeader";
import FilterBar from "@/components/FilterBar";
import JobDrawer from "@/components/JobDrawer";
import JobsTable from "@/components/JobsTable";
import PipelinePanel from "@/components/PipelinePanel";
import { ApiError, api, openEventStream } from "@/lib/api";
import type {
  ChromeClaudeParseResult,
  FilterState,
  Job,
  ProgressEvent,
  StateResponse,
  Verdict,
} from "@/lib/types";

const DEFAULT_FILTERS: FilterState = {
  posted_within: 15,
  location: "preferred_or_remote",
  min_score: 70,
  verdict: new Set<Verdict>(["apply", "consider"]),
  status: new Set(),
  source: new Set(),
  company: null,
  starred_only: false,
  show_dismissed: false,
  q: "",
  sort: "fit_score:desc",
};

export default function Dashboard() {
  const [state, setState] = useState<StateResponse | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [selected, setSelected] = useState<Job | null>(null);
  const [lastEvent, setLastEvent] = useState<ProgressEvent | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const sseRef = useRef<EventSource | null>(null);
  // Chat box → pre-fill the Run Chrome Claude modal (bump the nonce to open it).
  const [ccPrefill, setCcPrefill] = useState<ChromeClaudeParseResult | null>(null);
  const [ccNonce, setCcNonce] = useState(0);

  // --- data loading -------------------------------------------------

  const refreshState = useCallback(async () => {
    try {
      const next = await api.getState();
      setState(next);
      setLoadError(null);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `${e.status}: ${e.detail}`
          : (e as Error).message;
      setLoadError(
        `Cannot reach backend at ${process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8765"} — ${msg}`,
      );
    }
  }, []);

  const refreshJobs = useCallback(async () => {
    try {
      const resp = await api.getJobs(filters);
      // Server-side handles min_score / posted_within / location / q /
      // starred / single-verdict. Multi-verdict needs a client pass:
      const checked = filters.verdict;
      let rows =
        checked.size === 0 || checked.size === 3
          ? resp.jobs
          : resp.jobs.filter((j) =>
              j.fit_verdict ? checked.has(j.fit_verdict) : false,
            );
      // Hide jobs you've already dealt with — discarded OR applied — unless
      // the user opts to show them. Applied jobs live on the Track tab, so
      // they shouldn't clutter the Discover work surface.
      if (!filters.show_dismissed) {
        rows = rows.filter(
          (j) => j.status !== "dismissed" && j.status !== "applied",
        );
      }
      setJobs(rows);
      setLoadError(null);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `${e.status}: ${e.detail}`
          : (e as Error).message;
      setLoadError(`Job list failed — ${msg}`);
    }
  }, [filters]);

  // First run: no profile yet → send the user to the Setup page.
  const router = useRouter();
  useEffect(() => {
    api
      .setupStatus()
      .then((s) => {
        if (!s.configured) router.replace("/setup");
      })
      .catch(() => {
        /* backend down — the load error banner covers it */
      });
  }, [router]);

  useEffect(() => {
    // Initial + filter-change data load; setState happens after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshState();
  }, [refreshState]);

  // --- SSE -----------------------------------------------------------

  useEffect(() => {
    const es = openEventStream(
      (evt) => {
        setLastEvent(evt);
        // When a run finishes (done / error), refresh everything.
        if (evt.phase === "done" || evt.phase === "error") {
          setTimeout(() => {
            refreshState();
            refreshJobs();
          }, 500);
        }
      },
      () => {
        // EventSource auto-retries; the browser handles backoff.
        console.warn("SSE connection error; auto-reconnect will retry.");
      },
    );
    sseRef.current = es;
    return () => {
      es.close();
      sseRef.current = null;
    };
  }, [refreshState, refreshJobs]);

  // --- actions -------------------------------------------------------

  const handleRun = useCallback(async (phase: string) => {
    try {
      await api.runPhase(phase);
      // SSE will start sending events; PipelinePanel reads lastEvent.
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        alert("A pipeline phase is already running. Wait for it to finish.");
      } else {
        alert(`Failed to start ${phase}: ${(e as Error).message}`);
      }
    }
  }, []);

  const handleJobClick = useCallback((job: Job) => {
    setSelected(job);
  }, []);

  const handleStarToggle = useCallback(
    async (job: Job) => {
      try {
        const updated = await api.toggleStar(job.key, !job.starred);
        setJobs((prev) =>
          prev.map((j) => (j.key === updated.key ? updated : j)),
        );
        if (selected?.key === updated.key) setSelected(updated);
        refreshState(); // counts include starred.
      } catch (e) {
        alert(`Star toggle failed: ${(e as Error).message}`);
      }
    },
    [refreshState, selected],
  );

  const handleJobUpdate = useCallback(
    (updated: Job) => {
      setJobs((prev) =>
        prev.map((j) => (j.key === updated.key ? updated : j)),
      );
      setSelected(updated);
      refreshState();
    },
    [refreshState],
  );

  // Soft-discard: mark dismissed, drop from the visible list (it's hidden
  // by default), close the drawer if it was open on this job.
  const handleDismiss = useCallback(
    async (job: Job) => {
      try {
        await api.dismissJob(job.key);
        setJobs((prev) => prev.filter((j) => j.key !== job.key));
        setSelected((cur) => (cur?.key === job.key ? null : cur));
        refreshState();
      } catch (e) {
        alert(`Discard failed: ${(e as Error).message}`);
      }
    },
    [refreshState],
  );

  // Hard-delete: remove from the store entirely (may reappear next scrape
  // if still live — the drawer's confirm dialog warns about this).
  const handleDelete = useCallback(
    async (job: Job) => {
      try {
        await api.deleteJob(job.key);
        setJobs((prev) => prev.filter((j) => j.key !== job.key));
        setSelected((cur) => (cur?.key === job.key ? null : cur));
        refreshState();
      } catch (e) {
        alert(`Delete failed: ${(e as Error).message}`);
      }
    },
    [refreshState],
  );

  // --- dead-company set for the table badge --------------------------

  const [deadCompanies, setDeadCompanies] = useState<Set<string>>(new Set());
  useEffect(() => {
    api
      .getCompanies()
      .then((c) => setDeadCompanies(new Set(c.dead.map((s) => s.toLowerCase()))))
      .catch(() => {
        /* non-critical */
      });
  }, []);

  const visibleCount = useMemo(() => jobs.length, [jobs]);

  return (
    <main className="max-w-[1200px] mx-auto px-4 py-6">
      <CountsHeader state={state} />

      {loadError && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          {loadError}
        </div>
      )}

      <PipelinePanel
        running={state?.pipeline_running ?? null}
        lastEvent={lastEvent}
        onRun={handleRun}
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <BulkResumesButton
          isBusy={state?.pipeline_running !== null && state?.pipeline_running !== undefined}
          filters={filters}
          onStarted={() => refreshState()}
        />
        <ChromeClaudeButton
          prefill={ccPrefill}
          prefillNonce={ccNonce}
          onCompleted={() => {
            refreshState();
            refreshJobs();
          }}
        />
        <ChromeClaudeChat
          onParsed={(r) => {
            setCcPrefill(r);
            setCcNonce((n) => n + 1);
          }}
        />
      </div>

      <FilterBar filters={filters} onChange={setFilters} />

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            Jobs{" "}
            <span className="text-sm font-normal text-slate-500 dark:text-slate-400">
              ({visibleCount})
            </span>
          </h2>
        </div>

        <JobsTable
          jobs={jobs}
          deadCompanies={deadCompanies}
          sort={filters.sort}
          onSortChange={(sort) => setFilters({ ...filters, sort })}
          onJobClick={handleJobClick}
          onStarToggle={handleStarToggle}
          onDiscard={handleDismiss}
        />
      </section>

      <JobDrawer
        job={selected}
        onClose={() => setSelected(null)}
        onUpdate={handleJobUpdate}
        onDismiss={handleDismiss}
        onDelete={handleDelete}
      />
    </main>
  );
}
