// Privacy & spending, in plain language:
//   - what stays on this computer, what Claude sees, what never leaves
//   - this month's Claude spending by feature + a monthly budget (warn / stop)
//   - "Delete all my data" with a typed confirmation

"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { BudgetStatus, PrivacyInventory } from "@/lib/types";

const CARD = "rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900";
const INPUT =
  "rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";

const FEATURE_WORDS: Record<string, string> = {
  "pre-filter": "Quick check of new jobs",
  scoring: "Scoring jobs",
  resume: "Tailored resumes",
  resumes: "Tailored resumes (batch)",
  "cover-letter": "Cover letters",
  research: "Compare & interview prep",
  "premium-search": "Premium Chrome searches",
  "setup-chat": "Setup chat",
  other: "Other",
};

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : (e as Error).message;
}

function kb(n: number): string {
  return n > 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1000))} KB`;
}

export default function PrivacyPage() {
  const [budget, setBudget] = useState<BudgetStatus | null>(null);
  const [inv, setInv] = useState<PrivacyInventory | null>(null);
  const [amount, setAmount] = useState("");
  const [mode, setMode] = useState<"warn" | "stop">("warn");
  const [msg, setMsg] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState<string | null>(null);

  useEffect(() => {
    api
      .budget()
      .then((b) => {
        setBudget(b);
        setAmount(b.budget_usd ? String(b.budget_usd) : "");
        setMode(b.mode);
      })
      .catch((e) => setMsg(errText(e)));
    api.privacy().then(setInv).catch((e) => setMsg(errText(e)));
  }, []);

  const saveBudget = async () => {
    setMsg(null);
    try {
      const b = await api.setBudget(Number(amount) || 0, mode);
      setBudget(b);
      setMsg(b.budget_usd ? "Budget saved." : "Budget turned off.");
    } catch (e) {
      setMsg(errText(e));
    }
  };

  const deleteAll = async () => {
    setDeleting(true);
    try {
      const r = await api.deleteAll(typed);
      setDeleted(`Deleted ${r.removed.length} folder(s)${r.keys_cleared.length ? " and your saved keys" : ""}.`);
      setTyped("");
      setInv(await api.privacy());
    } catch (e) {
      setDeleted(errText(e));
    } finally {
      setDeleting(false);
    }
  };

  const features = Object.entries(budget?.by_feature ?? {});

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-50">Privacy &amp; spending</h1>

      <section className={CARD}>
        <h2 className="font-semibold text-slate-900 dark:text-slate-100">Where your information goes</h2>
        <dl className="mt-3 space-y-3 text-sm text-slate-700 dark:text-slate-300">
          <div>
            <dt className="font-semibold">Stays on this computer</dt>
            <dd>
              Your profiles, resume, the jobs found, your application tracker, generated resumes and cover
              letters, and your Claude key. Nothing is uploaded to us — there is no &quot;us&quot;; this app runs only
              on your computer.
            </dd>
          </div>
          <div>
            <dt className="font-semibold">What Claude sees</dt>
            <dd>
              To score jobs and write resumes, Claude (Anthropic) receives your resume, your profile answers and
              the job descriptions. Web research for Compare / Interview prep sends only public search terms
              (company and role) — never your resume.
            </dd>
          </div>
          <div>
            <dt className="font-semibold">Never leaves</dt>
            <dd>Your tracker notes, your spending history and anything you don&apos;t ask Claude to work on.</dd>
          </div>
        </dl>
      </section>

      <section id="budget" className={CARD}>
        <h2 className="font-semibold text-slate-900 dark:text-slate-100">Claude spending this month</h2>
        {budget && (
          <>
            <p className="mt-2 text-3xl font-bold tabular-nums text-slate-900 dark:text-slate-50">
              ${budget.spent_usd.toFixed(2)}
              {budget.budget_usd > 0 && (
                <span className="text-base font-normal text-slate-500"> of ${budget.budget_usd.toFixed(2)}</span>
              )}
            </p>
            {budget.subscription && (
              <p className="text-xs text-slate-500">
                You use your Claude subscription, so this is the API-equivalent cost — it counts against your
                plan&apos;s limits rather than being billed.
              </p>
            )}
            {features.length > 0 && (
              <ul className="mt-3 space-y-1 text-sm">
                {features.map(([f, v]) => (
                  <li key={f} className="flex justify-between border-b border-slate-100 py-1 dark:border-slate-800">
                    <span>{FEATURE_WORDS[f] ?? f}</span>
                    <span className="tabular-nums">${v.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <span className="block text-xs text-slate-500">Monthly budget (USD, empty = none)</span>
            <input className={`${INPUT} w-32`} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="e.g. 10" />
          </label>
          <label className="text-sm">
            <span className="block text-xs text-slate-500">When it&apos;s reached</span>
            <select className={INPUT} value={mode} onChange={(e) => setMode(e.target.value as "warn" | "stop")}>
              <option value="warn">Just warn me</option>
              <option value="stop">Stop using Claude</option>
            </select>
          </label>
          <button type="button" onClick={saveBudget} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
            Save budget
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Amounts are estimates from Claude&apos;s list prices. For a hard limit, also set one in the Anthropic Console.
        </p>
        {msg && <p role="status" className="mt-2 text-sm text-slate-600 dark:text-slate-300">{msg}</p>}
      </section>

      <section className={`${CARD} border-rose-200 dark:border-rose-500/30`}>
        <h2 className="font-semibold text-rose-700 dark:text-rose-300">Delete all my data</h2>
        {inv?.pinned ? (
          <p className="mt-2 text-sm">This app is set to use a custom profile folder, so delete that folder yourself.</p>
        ) : (
          <>
            <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">This permanently removes:</p>
            <ul className="mt-1 list-disc pl-5 text-sm text-slate-700 dark:text-slate-300">
              <li>
                {inv?.profiles.length ?? 0} profile(s)
                {inv?.profiles.length ? `: ${inv.profiles.map((p) => p.name).join(", ")}` : ""} — resumes, jobs,
                tracker, cover letters, chats
              </li>
              {inv?.folders.filter((f) => f.exists).map((f) => (
                <li key={f.path}>
                  <code className="text-xs">{f.path}</code> ({kb(f.bytes)})
                </li>
              ))}
              {!!inv?.keys.length && <li>Saved keys: {inv.keys.join(", ")}</li>}
            </ul>
            <p className="mt-3 text-sm">It can&apos;t be undone. Export a profile first (Profiles page) if you might want it back.</p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input aria-label='Type DELETE to confirm' className={`${INPUT} w-40`} value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Type DELETE" />
              <button
                type="button"
                disabled={typed !== "DELETE" || deleting}
                onClick={deleteAll}
                className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:opacity-40"
              >
                {deleting ? "Deleting…" : "Delete everything"}
              </button>
            </div>
          </>
        )}
        {deleted && <p role="status" className="mt-2 text-sm">{deleted}</p>}
      </section>
    </main>
  );
}
