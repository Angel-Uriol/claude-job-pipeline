// Setup — first-run wizard. Five steps, each backed by /api/setup/*:
//   1. Connect Claude     API key (default) or your local Claude Code login
//   2. Resume             upload → Claude parses it into resume_content
//   3. Questionnaire      Claude interviews you until the profile is complete
//   4. Companies          Claude suggests targets → we find their job boards
//   5. Review & save      see the generated YAML, then write profile/
// All progress is saved server-side (profile/.interview.json), so a reload
// or coming back later resumes where you left off.

"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { AuthMode, SetupCompany, SetupInterview, SetupStatus } from "@/lib/types";

type Step = 1 | 2 | 3 | 4 | 5;

const STEPS: { n: Step; label: string }[] = [
  { n: 1, label: "Connect Claude" },
  { n: 2, label: "Resume" },
  { n: 3, label: "Questionnaire" },
  { n: 4, label: "Companies" },
  { n: 5, label: "Review & save" },
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
function firstOpenStep(s: SetupStatus): Step {
  if (!s.auth.connected) return 1;
  const iv = s.interview;
  if (!iv.resume.parsed && iv.messages.length === 0) return 2;
  if (!iv.done) return 3;
  if (iv.companies.length === 0 || !iv.companies.some((c) => c.platform !== undefined)) return 4;
  return 5;
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
    void load().then((s) => s && setStep(firstOpenStep(s)));
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
              {s.n}. {s.label}
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
          <ResumeStep
            interview={interview}
            onError={setError}
            onUpdate={setInterview}
            onNext={() => setStep(3)}
          />
        )}
        {step === 3 && (
          <InterviewStep
            interview={interview}
            onError={setError}
            onUpdate={setInterview}
            onNext={() => setStep(4)}
          />
        )}
        {step === 4 && (
          <CompaniesStep
            // Remount (fresh local edits) whenever the server's list changes.
            key={JSON.stringify(interview.companies)}
            interview={interview}
            onError={setError}
            onUpdate={setInterview}
            onNext={() => setStep(5)}
          />
        )}
        {step === 5 && <ReviewStep configured={status.configured} onError={setError} onSaved={load} />}
      </div>
    </main>
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
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const connect = async () => {
    setBusy(true);
    onError(null);
    setResult(null);
    try {
      const check = await api.setupAuth(mode, key.trim());
      if (check.ok) {
        setResult(`✓ ${check.detail}`);
        setKey("");
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
          <p>Make sure it&apos;s logged in: run <code>claude</code> in a terminal once and use <code>/login</code>.</p>
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

// ===== step 2: resume =====

function ResumeStep({
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
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = async (file: File) => {
    setBusy(true);
    onError(null);
    try {
      onUpdate(await api.setupResume(file));
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={CARD}>
      <h2 className="text-lg font-semibold">Upload your resume</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        Claude extracts your experience, bullets, education and skills exactly as written — no
        invented facts, metrics kept verbatim. It becomes the source for every tailored resume.
      </p>

      <div
        className="mt-4 flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 p-8 text-center dark:border-slate-700"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files?.[0];
          if (f) void upload(f);
        }}
      >
        <p className="text-sm text-slate-600 dark:text-slate-300">Drop a PDF, DOCX or TXT here</p>
        <button type="button" className={`mt-3 ${BTN_GHOST}`} disabled={busy} onClick={() => fileRef.current?.click()}>
          {busy ? "Reading with Claude… (up to a minute)" : "Choose file"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void upload(f);
          }}
        />
      </div>

      {interview.resume.parsed && (
        <p className="mt-4 text-sm text-emerald-700 dark:text-emerald-400">
          ✓ Parsed {interview.resume.name ? `${interview.resume.name}'s` : "your"} resume:{" "}
          {interview.resume.roles} roles, {interview.resume.bullets} bullets.
        </p>
      )}

      <div className="mt-5 flex gap-3">
        <button type="button" className={BTN} onClick={onNext} disabled={busy}>
          {interview.resume.parsed ? "Continue" : "Skip — no resume"}
        </button>
      </div>
    </section>
  );
}

// ===== step 3: questionnaire =====

function InterviewStep({
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
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  const send = useCallback(
    async (message: string) => {
      setBusy(true);
      onError(null);
      try {
        onUpdate(await api.setupInterview(message));
        setText("");
      } catch (e) {
        onError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [onError, onUpdate],
  );

  // Kick off (or resume) the interview with Claude's next question.
  useEffect(() => {
    const last = interview.messages[interview.messages.length - 1];
    if (!started.current && (!last || last.role === "user")) {
      started.current = true;
      void send("");
    }
  }, [interview.messages, send]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [interview.messages.length, busy]);

  const total = interview.required_total;
  const filled = total - interview.missing.length;

  return (
    <section className={CARD}>
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">A few questions</h2>
        <span className="text-xs text-slate-500">
          {interview.done ? "Complete" : `${filled}/${total} essentials`}
        </span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800">
        <div className="h-full bg-indigo-500 transition-all" style={{ width: `${(filled / total) * 100}%` }} />
      </div>

      <div className="mt-4 max-h-[50vh] space-y-3 overflow-y-auto pr-1">
        {interview.messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm ${
                m.role === "user"
                  ? "bg-indigo-600 text-white"
                  : "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-100"
              }`}
            >
              {m.text}
            </div>
          </div>
        ))}
        {busy && <div className="text-xs text-slate-400">Claude is thinking…</div>}
        <div ref={bottomRef} />
      </div>

      {interview.done ? (
        <div className="mt-4 flex gap-3">
          <button type="button" className={BTN} onClick={onNext}>
            Choose target companies →
          </button>
        </div>
      ) : (
        <form
          className="mt-4 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim() && !busy) void send(text);
          }}
        >
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (text.trim() && !busy) void send(text);
              }
            }}
            rows={2}
            disabled={busy}
            placeholder="Type your answer… (Shift+Enter for a new line, “skip” to skip)"
            className={INPUT}
          />
          <button type="submit" className={BTN} disabled={busy || !text.trim()}>
            Send
          </button>
        </form>
      )}

      {interview.missing.length > 0 && (
        <p className="mt-3 text-xs text-slate-400">
          Still needed: {interview.missing.map((m) => m.label).join(" · ")}
        </p>
      )}
    </section>
  );
}

// ===== step 4: companies =====

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

// ===== step 5: review & save =====

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
