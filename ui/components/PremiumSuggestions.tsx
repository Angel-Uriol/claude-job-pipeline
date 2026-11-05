// PremiumSuggestions — the app recommending a ✨ premium (Claude in Chrome)
// search only where it adds something: companies the regular scrape can't
// see, dream companies whose board came up empty, strong jobs with an
// incomplete description. Shows what it will cost in the user's terms —
// live Claude-plan usage on a Claude Code login, dollars on an API key —
// then Check Chrome → Launch, all from here.

"use client";

import { useCallback, useEffect, useState } from "react";

import AskJobiButton from "@/components/AskJobiButton";

import { ApiError, api } from "@/lib/api";
import type { ChromeCheck, PremiumRecommendations, PremiumRec } from "@/lib/types";

const PRIORITY_STYLE: Record<PremiumRec["priority"], string> = {
  high: "bg-fuchsia-600 text-white",
  medium: "bg-fuchsia-200 text-fuchsia-800 dark:bg-fuchsia-500/25 dark:text-fuchsia-200",
  low: "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300",
};

const HIDE_KEY = "premium-suggestions-hidden";

function recKey(r: PremiumRec): string {
  return r.kind === "job" ? `job:${r.job_key}` : `co:${r.company}`;
}

export default function PremiumSuggestions({
  refreshToken,
  onLaunched,
}: {
  /** Bump to re-fetch (e.g. after a pipeline run finishes). */
  refreshToken: number;
  onLaunched: () => void;
}) {
  const [data, setData] = useState<PremiumRecommendations | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [hidden, setHidden] = useState(false);
  const [step, setStep] = useState<"pick" | "queued" | "launched">("pick");
  const [chrome, setChrome] = useState<ChromeCheck | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Read the per-tab "Not now" choice after mount (sessionStorage is client-only).
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setHidden(sessionStorage.getItem(HIDE_KEY) === "1");
    } catch {
      /* storage unavailable — show the card */
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const d = await api.premiumRecommendations();
      setData(d);
      setPicked(new Set(d.recommendations.filter((r) => r.priority === "high").map(recKey)));
      if (d.pending.length) setStep("queued");
    } catch {
      setData(null); // no profile yet, etc. — just don't show the card
    }
  }, []);

  useEffect(() => {
    // Fetch on mount and whenever a run finishes; setState after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load, refreshToken]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  if (!data || hidden) return null;
  const recs = data.recommendations;
  if (!recs.length && !data.pending.length) return null;

  const toggle = (k: string) =>
    setPicked((p) => {
      const n = new Set(p);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });

  const chosen = recs.filter((r) => picked.has(recKey(r)));

  return (
    <section className="mb-4 rounded-xl border border-fuchsia-300/70 bg-fuchsia-50/60 p-4 dark:border-fuchsia-500/30 dark:bg-fuchsia-500/10">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-fuchsia-900 dark:text-fuchsia-100">
            ✨ Premium search suggestions
          </h2>
          <p className="mt-0.5 text-xs text-fuchsia-900/70 dark:text-fuchsia-100/70">
            Places where the regular scrape is blind or thin. Claude opens these in your Chrome and
            reads them like a person would — read-only, never applies or logs in.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setHidden(true);
            try {
              sessionStorage.setItem(HIDE_KEY, "1");
            } catch {
              /* ignore */
            }
          }}
          className="text-xs text-fuchsia-700/70 hover:underline dark:text-fuchsia-200/70"
        >
          Not now
        </button>
      </div>

      {step === "pick" && (
        <ul className="mt-3 space-y-2">
          {recs.map((r) => (
            <li key={recKey(r)} className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={picked.has(recKey(r))}
                onChange={() => toggle(recKey(r))}
                aria-label={`Include ${r.title ?? r.company}`}
              />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-slate-800 dark:text-slate-100">
                    {r.kind === "job" ? `${r.company} · ${r.title}` : r.company}
                  </span>
                  <span className={`rounded px-1.5 text-[10px] font-semibold uppercase ${PRIORITY_STYLE[r.priority]}`}>
                    {r.priority}
                  </span>
                  {r.kind === "job" && (
                    <span className="text-[10px] uppercase tracking-wide text-slate-400">read posting</span>
                  )}
                </div>
                <p className="text-xs text-slate-600 dark:text-slate-300">{r.reason}</p>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p
        className={`mt-3 rounded-lg px-3 py-2 text-xs ${
          data.usage.warn
            ? "bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-200"
            : "bg-white/70 text-slate-700 dark:bg-slate-900/60 dark:text-slate-300"
        }`}
      >
        {data.usage.mode === "claude_code" ? "🔋 " : "💳 "}
        {data.usage.text}
      </p>

      {error && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">
          {error}
          <AskJobiButton message={`My premium Chrome search had a problem: "${error}". Can you help?`} />
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {step === "pick" && (
          <button
            type="button"
            disabled={!chosen.length || busy !== null}
            onClick={() =>
              void run("queue", async () => {
                await api.premiumQueue(
                  chosen.filter((r) => r.kind === "company").map((r) => r.company),
                  chosen.filter((r) => r.kind === "job").map((r) => r.job_key ?? ""),
                );
                setStep("queued");
              })
            }
            className="rounded-lg bg-fuchsia-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-fuchsia-700 disabled:opacity-40"
          >
            {busy === "queue" ? "Queuing…" : `Queue ${chosen.length} premium search${chosen.length === 1 ? "" : "es"}`}
          </button>
        )}
        {step !== "pick" && (
          <>
            <span className="text-xs text-fuchsia-900 dark:text-fuchsia-100">
              ✓ Queued{data.pending.length ? ` (${data.pending.length} waiting)` : ""}.
            </span>
            <button
              type="button"
              disabled={busy !== null || step === "launched"}
              onClick={() => void run("check", async () => setChrome(await api.chromeCheck()))}
              className="rounded-lg border border-fuchsia-400 px-3 py-1.5 text-xs font-semibold text-fuchsia-800 transition hover:bg-fuchsia-100 disabled:opacity-50 dark:text-fuchsia-200 dark:hover:bg-fuchsia-500/20"
            >
              {busy === "check" ? "Checking Chrome… (~15s)" : "1 · Check Chrome"}
            </button>
            <button
              type="button"
              disabled={!chrome?.connected || busy !== null || step === "launched"}
              onClick={() =>
                void run("launch", async () => {
                  await api.runPhase("premium");
                  setStep("launched");
                  onLaunched();
                })
              }
              className="rounded-lg bg-fuchsia-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-fuchsia-700 disabled:opacity-40"
            >
              {step === "launched" ? "Running — progress above" : "2 · ▶ Launch"}
            </button>
          </>
        )}
      </div>
      {chrome && (
        <p className={`mt-2 text-xs ${chrome.connected ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}`}>
          {chrome.connected ? "✓ " : "✗ "}
          {chrome.detail}
          {chrome.code === "chrome_needs_login" && (
            <a href="/setup" className="ml-2 font-semibold underline">Open Setup → Connect Claude</a>
          )}
          {!chrome.connected && (
            <AskJobiButton message={`My Chrome search can't connect. The check said: "${chrome.detail}". Can you help me fix it?`} />
          )}
        </p>
      )}
    </section>
  );
}
