// Setup — first-run wizard. Five steps, each backed by /api/setup/*:
//   0. Install            one-click install of the resume renderer + Claude Code CLI
//   1. Connect Claude     API key (default) or your local Claude Code login
//   2. Your profile       one chat: resume (upload or build) + 3 rounds of
//                         questions with live job scans in between
//   3. Companies          pre-filled from the scans; trim, add, find job boards
//   4. Review & save      see the generated YAML, then write profile/
// All progress is saved server-side (profile/.interview.json), so a reload
// or coming back later resumes where you left off.

"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import ProfileChat from "@/components/ProfileChat";
import { ApiError, api } from "@/lib/api";
import type {
  AuthMode,
  SetupCompany,
  SetupInterview,
  SetupStatus,
  SystemCheck,
  LoginStatus,
} from "@/lib/types";

type Step = 0 | 1 | 2 | 3 | 4;

const STEPS: { n: Step; label: string }[] = [
  { n: 0, label: "Install" },
  { n: 1, label: "Connect Claude" },
  { n: 2, label: "Your profile" },
  { n: 3, label: "Companies" },
  { n: 4, label: "Review & save" },
];

const CARD =
  "rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900";
const BTN =
  "rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50";
const BTN_GHOST =
  "rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800";
const INPUT =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : (e as Error).message;
}

/** Pick the first step that still needs doing. */
function firstOpenStep(s: SetupStatus, checks: SystemCheck[]): Step {
  if (checks.some((c) => !c.ok && !c.optional)) return 0;
  if (!s.auth.connected) return checks.every((c) => c.ok) ? 1 : 0;
  const iv = s.interview;
  if (!iv.done) return 2;
  if (iv.companies.length === 0 || !iv.companies.some((c) => c.platform !== undefined)) return 3;
  return 4;
}

export default function SetupPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [interview, setInterview] = useState<SetupInterview | null>(null);
  const [step, setStep] = useState<Step>(1);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await api.setupStatus();
      setStatus(s);
      setInterview(s.interview);
      return s;
    } catch (e) {
      setError(`Cannot reach the backend on port 8765 — is \`make dev\` running? (${errText(e)})`);
      return null;
    }
  }, []);

  useEffect(() => {
    // Initial load; setState happens after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void Promise.all([load(), api.setupSystem().catch(() => ({ checks: [] }))]).then(
      ([s, sys]) => s && setStep(firstOpenStep(s, sys.checks)),
    );
  }, [load]);

  if (!status || !interview) {
    return (
      <main className="mx-auto w-full max-w-3xl px-4 py-10">
        {error ? <p className="text-sm text-rose-600">{error}</p> : <p className="text-sm text-slate-500">Loading…</p>}
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8">
      <h1 className="text-2xl font-bold tracking-tight">Set up your job search</h1>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        Everything stays on this machine in <code>{status.profile_dir}</code>. Claude only sees what
        it needs for each step.
      </p>

      <ol className="mt-6 flex flex-wrap gap-2">
        {STEPS.map((s) => (
          <li key={s.n}>
            <button
              type="button"
              onClick={() => setStep(s.n)}
              className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
                step === s.n
                  ? "bg-indigo-600 text-white"
                  : "bg-slate-200 text-slate-600 hover:bg-slate-300 dark:bg-slate-800 dark:text-slate-300"
              }`}
            >
              {s.n + 1}. {s.label}
            </button>
          </li>
        ))}
      </ol>

      {error && (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
          <button type="button" className="ml-2 underline" onClick={() => setError(null)}>
            dismiss
          </button>
        </div>
      )}

      <div className="mt-6">
        {step === 0 && <InstallStep onError={setError} onNext={() => setStep(1)} />}
        {step === 1 && (
          <AuthStep
            status={status}
            onError={setError}
            onDone={async () => {
              await load();
              setStep(2);
            }}
          />
        )}
        {step === 2 && (
          <ProfileChat
            interview={interview}
            onError={setError}
            onUpdate={setInterview}
            onNext={() => setStep(3)}
          />
        )}
        {step === 3 && (
          <CompaniesStep
            // Remount (fresh local edits) whenever the server's list changes.
            key={JSON.stringify(interview.companies)}
            interview={interview}
            onError={setError}
            onUpdate={setInterview}
            onNext={() => setStep(4)}
          />
        )}
        {step === 4 && <ReviewStep configured={status.configured} onError={setError} onSaved={load} />}
      </div>
    </main>
  );
}

// ===== step 0: install =====

function InstallStep({
  onError,
  onNext,
}: {
  onError: (m: string | null) => void;
  onNext: () => void;
}) {
  const [checks, setChecks] = useState<SystemCheck[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<string | null>(null);

  useEffect(() => {
    api
      .setupSystem()
      .then((r) => setChecks(r.checks))
      .catch((e) => onError(errText(e)));
  }, [onError]);

  const installOne = async (c: SystemCheck) => {
    setBusy(c.id);
    onError(null);
    try {
      const r = await api.setupInstall(c.id);
      setChecks(r.checks);
      if (!r.ok) {
        onError(`Installing ${c.label} failed — see the log below.`);
        setLog(r.log);
      }
      return r.ok;
    } catch (e) {
      onError(errText(e));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const installAll = async () => {
    setLog(null);
    for (const c of checks ?? []) {
      if (c.installable && !(await installOne(c))) return;
    }
  };

  const missing = (checks ?? []).filter((c) => c.installable);
  const blocking = (checks ?? []).some((c) => !c.ok && !c.optional);

  return (
    <section className={CARD}>
      <h2 className="text-lg font-semibold">Install what&apos;s needed</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        One click sets up the pieces the pipeline uses on this computer. The Claude Code CLI is only
        needed if you want to use your Claude login instead of an API key.
      </p>

      {checks === null ? (
        <p className="mt-4 text-sm text-slate-400">Checking this machine…</p>
      ) : (
        <ul className="mt-4 divide-y divide-slate-100 dark:divide-slate-800">
          {checks.map((c) => (
            <li key={c.id} className="flex items-center gap-3 py-3">
              <span className={`text-lg ${c.ok ? "text-emerald-600" : c.optional ? "text-amber-500" : "text-rose-600"}`}>
                {c.ok ? "✓" : c.optional ? "○" : "✗"}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold">
                  {c.label}
                  {c.optional && !c.ok && <span className="ml-2 text-xs font-normal text-slate-400">optional</span>}
                </div>
                <div className="break-all text-xs text-slate-500 dark:text-slate-400">{c.detail}</div>
              </div>
              {c.installable && (
                <button type="button" className={BTN_GHOST} disabled={busy !== null} onClick={() => void installOne(c)}>
                  {busy === c.id ? "Installing…" : "Install"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {log && (
        <pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100">{log}</pre>
      )}

      <div className="mt-5 flex flex-wrap gap-3">
        {missing.length > 0 && (
          <button type="button" className={BTN} disabled={busy !== null} onClick={() => void installAll()}>
            {busy ? "Installing… (can take a minute)" : `Install missing (${missing.length})`}
          </button>
        )}
        <button type="button" className={missing.length ? BTN_GHOST : BTN} disabled={blocking || busy !== null} onClick={onNext}>
          Continue →
        </button>
      </div>
    </section>
  );
}

// ===== step 1: connect Claude =====

function AuthStep({
  status,
  onError,
  onDone,
}: {
  status: SetupStatus;
  onError: (m: string | null) => void;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<AuthMode>(status.auth.mode);
  const [key, setKey] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const connect = async () => {
    setBusy(true);
    onError(null);
    setResult(null);
    try {
      const check = await api.setupAuth(mode, key.trim(), token.trim());
      if (check.ok) {
        setResult(`✓ ${check.detail}`);
        setKey("");
        setToken("");
        onDone();
      } else {
        onError(check.detail);
      }
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={CARD}>
      <h2 className="text-lg font-semibold">Connect Claude</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        Claude writes the questionnaire, scores every job against your profile, and tailors your resume.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className={`cursor-pointer rounded-xl border p-4 ${mode === "api_key" ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-500/30" : "border-slate-200 dark:border-slate-700"}`}>
          <input type="radio" className="sr-only" checked={mode === "api_key"} onChange={() => setMode("api_key")} />
          <div className="text-sm font-semibold">Anthropic API key <span className="ml-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">recommended</span></div>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Pay-as-you-go. A typical daily run costs cents. Create a key at console.anthropic.com.
          </p>
        </label>
        <label className={`cursor-pointer rounded-xl border p-4 ${mode === "claude_code" ? "border-indigo-500 ring-2 ring-indigo-200 dark:ring-indigo-500/30" : "border-slate-200 dark:border-slate-700"}`}>
          <input type="radio" className="sr-only" checked={mode === "claude_code"} onChange={() => setMode("claude_code")} />
          <div className="text-sm font-semibold">My Claude Code login</div>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Uses the Claude Code CLI already logged in on this computer — no key to paste.
          </p>
        </label>
      </div>

      {mode === "api_key" ? (
        <div className="mt-4">
          <label className="text-xs font-semibold text-slate-600 dark:text-slate-300" htmlFor="api-key">
            API key {status.auth.api_key_preview && <span className="font-normal text-slate-400">(saved: {status.auth.api_key_preview} — leave blank to keep)</span>}
          </label>
          <input
            id="api-key"
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="sk-ant-…"
            className={`mt-1 ${INPUT}`}
          />
          <p className="mt-1 text-xs text-slate-400">Saved only to <code>.env</code> in this folder (never committed).</p>
        </div>
      ) : (
        <div className="mt-4 space-y-2 text-sm text-slate-600 dark:text-slate-300">
          <p>
            {status.auth.cli.cli_found ? (
              <>✓ Found the Claude Code CLI at <code className="break-all text-xs">{status.auth.cli.cli_path}</code></>
            ) : (
              <>✗ Claude Code CLI not found. Install it from claude.com/claude-code.</>
            )}
          </p>
          <ClaudeLogin
            disabled={!status.auth.cli.cli_found || busy}
            onError={onError}
            onLoggedIn={() => void connect()}
          />
          <details className="text-sm">
            <summary className="cursor-pointer text-xs text-slate-500">Paste a token manually instead</summary>
            <p className="mt-2 text-xs text-slate-500">
              Run <code>claude setup-token</code> in a terminal, then paste the <code>sk-ant-oat…</code> token it prints.
            </p>
          <div>
            <label className="text-xs font-semibold text-slate-600 dark:text-slate-300" htmlFor="oauth-token">
              Token from <code>claude setup-token</code>{" "}
              <span className="font-normal text-slate-400">
                (optional{status.auth.cli.oauth_token_env ? " — one is saved; leave blank to keep it" : ""})
              </span>
            </label>
            <input
              id="oauth-token"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="sk-ant-oat…"
              className={`mt-1 ${INPUT}`}
            />
            <p className="mt-1 text-xs text-slate-400">Saved only to <code>.env</code> in this folder (never committed).</p>
          </div>
          </details>
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
            For personal use on your own machine only. Anthropic&apos;s terms ask third-party tools to use
            API keys rather than claude.ai logins, so don&apos;t host this app for other people in this mode.
          </p>
        </div>
      )}

      <div className="mt-5 flex items-center gap-3">
        <button type="button" className={BTN} disabled={busy} onClick={() => void connect()}>
          {busy ? "Checking…" : "Save & test connection"}
        </button>
        {result && <span className="text-sm text-emerald-600 dark:text-emerald-400">{result}</span>}
      </div>
    </section>
  );
}

// One-click login: the backend runs `claude setup-token`, the browser opens
// for the Claude login, and the token is captured + saved automatically.
function ClaudeLogin({
  disabled,
  onError,
  onLoggedIn,
}: {
  disabled: boolean;
  onError: (m: string | null) => void;
  onLoggedIn: () => void;
}) {
  const [login, setLogin] = useState<LoginStatus | null>(null);
  const [code, setCode] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const done = useRef(false);

  const stopPolling = () => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
  };

  const poll = useCallback(() => {
    stopPolling();
    timer.current = setInterval(async () => {
      try {
        const s = await api.setupLoginStatus();
        setLogin(s);
        if (s.state !== "running") stopPolling();
        if (s.state === "done" && !done.current) {
          done.current = true;
          onLoggedIn();
        }
      } catch (e) {
        stopPolling();
        onError(errText(e));
      }
    }, 1500);
  }, [onError, onLoggedIn]);

  useEffect(() => stopPolling, []);

  const start = async () => {
    onError(null);
    done.current = false;
    try {
      setLogin(await api.setupLoginStart());
      poll();
    } catch (e) {
      onError(errText(e));
    }
  };

  const running = login?.state === "running";

  return (
    <div className="space-y-2 rounded-xl border border-slate-200 p-4 dark:border-slate-700">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className={BTN} disabled={disabled || running} onClick={() => void start()}>
          {running ? "Waiting for login…" : login?.state === "done" ? "Logged in ✓" : "Log in with Claude"}
        </button>
        {running && (
          <button
            type="button"
            className="text-xs text-slate-500 underline"
            onClick={async () => {
              stopPolling();
              setLogin(await api.setupLoginCancel());
            }}
          >
            cancel
          </button>
        )}
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Opens your browser to log in to your Claude account. When the page says “You’re all set”, come
        back here — this page continues by itself.
      </p>
      {login?.message && (
        <p
          className={`text-xs ${
            login.state === "error" ? "text-rose-600" : login.state === "done" ? "text-emerald-600" : "text-indigo-600 dark:text-indigo-300"
          }`}
        >
          {login.message}
        </p>
      )}
      {running && login?.auth_url && (
        <p className="text-xs">
          Browser didn&apos;t open?{" "}
          <a href={login.auth_url} target="_blank" rel="noreferrer" className="text-indigo-600 underline">
            Open the login page
          </a>
        </p>
      )}
      {running && login?.prompt && (
        <form
          className="flex gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            setLogin(await api.setupLoginInput(code));
            setCode("");
          }}
        >
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={login.prompt}
            className={INPUT}
            aria-label="Code from the browser"
          />
          <button type="submit" className={BTN_GHOST} disabled={!code.trim()}>
            Send
          </button>
        </form>
      )}
      {login?.state === "error" && login.output && (
        <pre className="max-h-40 overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] text-slate-200">{login.output}</pre>
      )}
    </div>
  );
}

// ===== step 3: companies =====

const PLATFORM_BADGE: Record<string, string> = {
  greenhouse: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300",
  lever: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  ashby: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
};

function CompaniesStep({
  interview,
  onError,
  onUpdate,
  onNext,
}: {
  interview: SetupInterview;
  onError: (m: string | null) => void;
  onUpdate: (iv: SetupInterview) => void;
  onNext: () => void;
}) {
  const [companies, setCompanies] = useState<SetupCompany[]>(interview.companies);
  const [busy, setBusy] = useState<string | null>(null);
  const [extra, setExtra] = useState("");

  const run = async (label: string, fn: () => Promise<SetupInterview>) => {
    setBusy(label);
    onError(null);
    try {
      onUpdate(await fn());
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(null);
    }
  };

  const verified = companies.some((c) => c.platform !== undefined);
  const toggle = (i: number) =>
    setCompanies((cs) => cs.map((c, j) => (j === i ? { ...c, selected: !(c.selected ?? true) } : c)));
  const add = () => {
    const names = extra.split(",").map((s) => s.trim()).filter(Boolean);
    if (!names.length) return;
    setCompanies((cs) => [
      ...cs,
      ...names.map((name) => ({ name, tier: 1, why: "added by you", selected: true })),
    ]);
    setExtra("");
  };

  return (
    <section className={CARD}>
      <h2 className="text-lg font-semibold">Target companies</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        Claude suggests employers that fit your profile. Uncheck any you don&apos;t want, add your own,
        then we check which have a public job board we can scrape directly. The rest get a ready-made
        Chrome Claude search prompt in your daily digest.
      </p>

      {companies.length === 0 ? (
        <button
          type="button"
          className={`mt-4 ${BTN}`}
          disabled={busy !== null}
          onClick={() => void run("suggest", api.setupSuggestCompanies)}
        >
          {busy === "suggest" ? "Asking Claude…" : "Suggest companies"}
        </button>
      ) : (
        <>
          <ul className="mt-4 divide-y divide-slate-100 dark:divide-slate-800">
            {companies.map((c, i) => (
              <li key={`${c.name}-${i}`} className="flex items-start gap-3 py-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={c.selected ?? true}
                  onChange={() => toggle(i)}
                  aria-label={`Include ${c.name}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                    {c.name}
                    <span className="rounded bg-slate-100 px-1.5 text-[10px] text-slate-500 dark:bg-slate-800">tier {c.tier}</span>
                    {c.platform ? (
                      <span className={`rounded px-1.5 text-[10px] ${PLATFORM_BADGE[c.platform]}`}>
                        {c.platform} · {c.jobs} jobs
                      </span>
                    ) : c.platform === null ? (
                      <span className="rounded bg-amber-100 px-1.5 text-[10px] text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                        no public board → Chrome Claude
                      </span>
                    ) : null}
                  </div>
                  {c.why && <p className="text-xs text-slate-500 dark:text-slate-400">{c.why}</p>}
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            <input
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
              placeholder="Add companies, comma-separated"
              className={INPUT}
            />
            <button type="button" className={BTN_GHOST} onClick={add}>
              Add
            </button>
          </div>
          {verified && <PremiumSearch companies={companies} onError={onError} />}
          <div className="mt-5 flex flex-wrap gap-3">
            <button
              type="button"
              className={BTN}
              disabled={busy !== null}
              onClick={() => void run("verify", () => api.setupVerifyCompanies(companies))}
            >
              {busy === "verify" ? "Checking job boards…" : verified ? "Re-check job boards" : "Find job boards"}
            </button>
            <button
              type="button"
              className={BTN_GHOST}
              disabled={busy !== null}
              onClick={() => void run("suggest", api.setupSuggestCompanies)}
            >
              {busy === "suggest" ? "Asking Claude…" : "Re-suggest"}
            </button>
            {verified && (
              <button type="button" className={BTN_GHOST} onClick={onNext}>
                Review →
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}

// Premium search: Claude in Chrome browses chosen companies' own careers
// sites. Opt-in, because it costs more tokens and needs browser control.
function PremiumSearch({
  companies,
  onError,
}: {
  companies: SetupCompany[];
  onError: (m: string | null) => void;
}) {
  const candidates = companies.filter((c) => c.selected ?? true);
  const [picked, setPicked] = useState<string[]>(
    candidates.filter((c) => c.platform === null).map((c) => c.name),
  );
  const [queued, setQueued] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [chrome, setChrome] = useState<{ connected: boolean; detail: string } | null>(null);
  const [launched, setLaunched] = useState(false);

  const toggle = (name: string) =>
    setPicked((p) => (p.includes(name) ? p.filter((x) => x !== name) : [...p, name]));

  const queue = async () => {
    setBusy(true);
    onError(null);
    try {
      const r = await api.setupPremium(picked);
      setQueued(r.watcher_command);
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-6 rounded-xl border border-violet-300/60 bg-violet-50/60 p-4 dark:border-violet-500/30 dark:bg-violet-500/10">
      <div className="flex items-center gap-2 text-sm font-semibold text-violet-800 dark:text-violet-200">
        ✨ Premium search <span className="rounded bg-violet-200/70 px-1.5 text-[10px] dark:bg-violet-500/20">optional</span>
      </div>
      <p className="mt-1 text-xs text-violet-900/80 dark:text-violet-100/80">
        The app launches Claude, which opens each company&apos;s own careers site in your Chrome and
        searches it like a person would (read-only: it never logs in, applies or fills forms) — the most thorough and up-to-date search, and the only way to cover companies without
        a public job board. It <b>uses many more tokens</b> than the regular scrape and needs
        <b> permission to control Chrome on your computer</b> (the Claude in Chrome extension).
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {candidates.map((c) => {
          const on = picked.includes(c.name);
          return (
            <button
              key={c.name}
              type="button"
              onClick={() => toggle(c.name)}
              className={`rounded-full border px-3 py-1 text-xs transition ${
                on
                  ? "border-violet-500 bg-violet-600 text-white"
                  : "border-violet-300 text-violet-800 hover:bg-violet-100 dark:border-violet-500/40 dark:text-violet-200 dark:hover:bg-violet-500/20"
              }`}
            >
              {c.name}
              {c.platform === null && " · no public board"}
            </button>
          );
        })}
      </div>
      {queued ? (
        <div className="mt-3 space-y-3 text-xs text-violet-900 dark:text-violet-100">
          <p>✓ Queued. The app can run it for you — Claude opens and controls Chrome:</p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={checking}
              onClick={async () => {
                setChecking(true);
                try {
                  setChrome(await api.chromeCheck());
                } catch (e) {
                  onError(errText(e));
                } finally {
                  setChecking(false);
                }
              }}
              className="rounded-lg border border-violet-400 px-3 py-1.5 font-semibold transition hover:bg-violet-100 disabled:opacity-50 dark:hover:bg-violet-500/20"
            >
              {checking ? "Checking Chrome… (~10s)" : "1 · Check Chrome connection"}
            </button>
            <button
              type="button"
              disabled={!chrome?.connected || launched}
              onClick={async () => {
                try {
                  await api.runPhase("premium");
                  setLaunched(true);
                } catch (e) {
                  onError(errText(e));
                }
              }}
              className="rounded-lg bg-violet-600 px-3 py-1.5 font-semibold text-white transition hover:bg-violet-700 disabled:opacity-40"
            >
              {launched ? "Running — watch Discover" : "2 · ▶ Launch premium search"}
            </button>
          </div>
          {chrome && (
            <p className={chrome.connected ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}>
              {chrome.connected ? "✓ " : "✗ "}
              {chrome.detail}
            </p>
          )}
          {launched && (
            <p>
              Progress shows on <Link href="/" className="underline">Discover</Link> (“Visiting
              careers.…”). Found jobs are imported into this profile and scored automatically.
            </p>
          )}
          <details>
            <summary className="cursor-pointer opacity-70">Or run it from your own Claude Code session</summary>
            <code className="mt-1 block select-all break-all rounded bg-white/70 p-2 dark:bg-slate-900">{queued}</code>
          </details>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => void queue()}
          disabled={busy || picked.length === 0}
          className="mt-3 rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-violet-700 disabled:opacity-50"
        >
          {busy ? "Queuing…" : `Queue premium search (${picked.length})`}
        </button>
      )}
    </div>
  );
}

// ===== step 4: review & save =====

function ReviewStep({
  configured,
  onError,
  onSaved,
}: {
  configured: boolean;
  onError: (m: string | null) => void;
  onSaved: () => Promise<unknown>;
}) {
  const [files, setFiles] = useState<Record<string, string> | null>(null);
  const [tab, setTab] = useState("config.yaml");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string[] | null>(null);

  useEffect(() => {
    api
      .setupPreview()
      .then((r) => setFiles(r.files))
      .catch((e) => onError(errText(e)));
  }, [onError]);

  const save = async () => {
    setBusy(true);
    onError(null);
    try {
      const r = await api.setupCommit();
      setSaved(r.written);
      await onSaved();
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={CARD}>
      <h2 className="text-lg font-semibold">Review & save</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        These three files drive everything. You can edit them by hand any time.
        {configured && " Saving again backs up your current files to *.bak first."}
      </p>

      {files ? (
        <>
          <div className="mt-4 flex gap-1">
            {Object.keys(files).map((name) => (
              <button
                key={name}
                type="button"
                onClick={() => setTab(name)}
                className={`rounded-md px-2 py-1 text-xs font-semibold ${
                  tab === name ? "bg-slate-800 text-white dark:bg-slate-200 dark:text-slate-900" : "text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                }`}
              >
                {name}
              </button>
            ))}
          </div>
          <pre className="mt-2 max-h-[50vh] overflow-auto rounded-lg bg-slate-950 p-4 text-xs leading-relaxed text-slate-100">
            {files[tab]}
          </pre>
        </>
      ) : (
        <p className="mt-4 text-sm text-slate-400">Building your files…</p>
      )}

      {saved ? (
        <div className="mt-5 space-y-3">
          <p className="text-sm text-emerald-700 dark:text-emerald-400">✓ Saved {saved.length} files. You&apos;re ready.</p>
          <Link href="/" className={BTN}>
            Go to Discover and run the pipeline →
          </Link>
        </div>
      ) : (
        <button type="button" className={`mt-5 ${BTN}`} disabled={busy || !files} onClick={() => void save()}>
          {busy ? "Saving…" : "Save my profile"}
        </button>
      )}
    </section>
  );
}
