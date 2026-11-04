// ProfileChat — the merged "resume + questionnaire" setup step.
//
// One minimal, futuristic chat with Claude in three rounds:
//   01 About you → (live job scan) → 02 What the market asks → (scan) → 03 Sharpen
// Answers are mostly tap-to-answer option chips (multi-select where it makes
// sense); typing your own answer always works. The resume upload happens
// inline in the chat. All state lives server-side (/api/setup/*), so a
// reload resumes exactly where you were.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { MarketJob, SetupInterview, SetupMessage } from "@/lib/types";

type Busy = null | "thinking" | "scanning" | "reading";

const SCAN_LINES = [
  "Finding companies that hire for roles like yours…",
  "Checking which ones have open postings…",
  "Reading live job descriptions…",
  "Comparing what they ask for with your profile…",
];

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : (e as Error).message;
}

export default function ProfileChat({
  interview,
  onUpdate,
  onError,
  onNext,
}: {
  interview: SetupInterview;
  onUpdate: (iv: SetupInterview) => void;
  onError: (m: string | null) => void;
  onNext: () => void;
}) {
  const [busy, setBusy] = useState<Busy>(null);
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [scanLine, setScanLine] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const autoRan = useRef<string>("");

  const run = useCallback(
    async (kind: Exclude<Busy, null>, fn: () => Promise<SetupInterview>) => {
      setBusy(kind);
      onError(null);
      try {
        onUpdate(await fn());
        setText("");
        setPicked([]);
      } catch (e) {
        onError(errText(e));
      } finally {
        setBusy(null);
      }
    },
    [onError, onUpdate],
  );

  const send = useCallback(
    (message: string) => {
      if (!message.trim() || busy) return;
      void run("thinking", () => api.setupInterview(message.trim()));
    },
    [busy, run],
  );

  const last = interview.messages[interview.messages.length - 1];
  const phase = interview.phase;

  // Drive the automatic parts: opening line, live scans, resuming a round.
  // (setState happens after an await inside `run`.)
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (busy) return;
    const key = `${phase}:${interview.messages.length}`;
    if (autoRan.current === key) return;
    if (interview.messages.length === 0) {
      autoRan.current = key;
      void run("thinking", () => api.setupInterview(""));
    } else if (phase === "market1" || phase === "market2") {
      autoRan.current = key;
      void run("scanning", api.setupMarket);
    } else if (phase.startsWith("round") && (last?.role === "user" || last?.kind === "note")) {
      // Resume a round after a reload, or start one after an import / status note.
      autoRan.current = key;
      void run("thinking", () => api.setupInterview(""));
    }
  }, [busy, phase, interview.messages.length, last?.role, last?.kind, run]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Rotate the scanning status lines.
  useEffect(() => {
    if (busy !== "scanning") return;
    const t = setInterval(() => setScanLine((n) => (n + 1) % SCAN_LINES.length), 3500);
    return () => clearInterval(t);
  }, [busy]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [interview.messages.length, busy]);

  const upload = (file: File) =>
    void run("reading", () => api.setupResume(file));

  const showChoices = !busy && last?.role === "assistant" && !interview.done;

  return (
    <section className="overflow-hidden rounded-3xl border border-white/10 bg-slate-950 text-slate-100 shadow-2xl shadow-indigo-950/40">
      <RoundBar interview={interview} />

      <div className="max-h-[62vh] min-h-[360px] space-y-5 overflow-y-auto px-5 py-6 sm:px-8">
        {interview.messages.map((m, i) => (
          <Bubble key={i} m={m} />
        ))}

        {showChoices && last.kind === "upload" && (
          <div
            className="ml-11 flex flex-col items-center justify-center rounded-2xl border border-dashed border-cyan-400/40 bg-cyan-400/5 p-6 text-center transition hover:border-cyan-300/70"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = e.dataTransfer.files?.[0];
              if (f) upload(f);
            }}
          >
            <p className="text-sm text-slate-300">Drop your resume here</p>
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="mt-3 rounded-full bg-gradient-to-r from-indigo-500 to-cyan-500 px-4 py-1.5 text-sm font-semibold text-white shadow-lg shadow-cyan-500/20 transition hover:brightness-110"
            >
              Choose file
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload(f);
              }}
            />
          </div>
        )}

        {showChoices && last.options.length > 0 && (
          <div className="ml-11 flex flex-wrap gap-2">
            {last.options.map((opt) => {
              const on = picked.includes(opt);
              return (
                <button
                  key={opt}
                  type="button"
                  onClick={() =>
                    last.multi
                      ? setPicked((p) => (on ? p.filter((x) => x !== opt) : [...p, opt]))
                      : send(opt)
                  }
                  className={`rounded-full border px-4 py-1.5 text-sm transition duration-200 ${
                    on
                      ? "border-transparent bg-gradient-to-r from-indigo-500 to-cyan-500 text-white shadow-lg shadow-cyan-500/20"
                      : "border-white/15 bg-white/5 text-slate-200 hover:-translate-y-0.5 hover:border-cyan-300/60 hover:bg-white/10"
                  }`}
                >
                  {opt}
                </button>
              );
            })}
            {last.multi && picked.length > 0 && (
              <button
                type="button"
                onClick={() => send(picked.join(", "))}
                className="rounded-full bg-white px-4 py-1.5 text-sm font-semibold text-slate-900 transition hover:bg-cyan-100"
              >
                Continue ({picked.length}) →
              </button>
            )}
          </div>
        )}

        {busy && <Thinking busy={busy} line={SCAN_LINES[scanLine]} />}
        <div ref={bottomRef} />
      </div>

      {interview.done ? (
        <div className="flex items-center justify-between gap-3 border-t border-white/10 px-5 py-4 sm:px-8">
          <span className="text-sm text-slate-400">Profile complete.</span>
          <button
            type="button"
            onClick={onNext}
            className="rounded-full bg-gradient-to-r from-indigo-500 to-cyan-500 px-5 py-2 text-sm font-semibold text-white shadow-lg shadow-cyan-500/20 transition hover:brightness-110"
          >
            Choose companies →
          </button>
        </div>
      ) : (
        <form
          className="flex items-center gap-3 border-t border-white/10 px-5 py-4 sm:px-8"
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={!!busy || phase.startsWith("market")}
            placeholder={last?.options.length ? "Or type your own answer…" : "Type your answer…"}
            className="flex-1 bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none disabled:opacity-40"
            aria-label="Your answer"
          />
          <button
            type="submit"
            disabled={!!busy || !text.trim()}
            className="grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br from-indigo-500 to-cyan-500 text-white transition hover:brightness-110 disabled:opacity-30"
            aria-label="Send"
          >
            ↑
          </button>
        </form>
      )}
    </section>
  );
}

// ===== pieces =====

function RoundBar({ interview }: { interview: SetupInterview }) {
  return (
    <div className="flex items-center gap-2 border-b border-white/10 px-5 py-4 sm:px-8">
      {[1, 2, 3].map((n) => {
        const active = interview.round === n && !interview.done;
        const past = interview.round > n || interview.done;
        return (
          <div key={n} className="flex flex-1 flex-col gap-1.5">
            <div className="h-1 overflow-hidden rounded-full bg-white/10">
              <div
                className={`h-full rounded-full bg-gradient-to-r from-indigo-500 to-cyan-400 transition-all duration-700 ${
                  past ? "w-full" : active ? "w-1/2 animate-pulse" : "w-0"
                }`}
              />
            </div>
            <span className={`text-[11px] tracking-wide ${active ? "text-cyan-300" : past ? "text-slate-300" : "text-slate-500"}`}>
              0{n} · {interview.round_names[String(n)]}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Orb({ pulse = false }: { pulse?: boolean }) {
  return (
    <div
      className={`mt-0.5 h-7 w-7 shrink-0 rounded-full bg-gradient-to-br from-indigo-400 via-violet-500 to-cyan-400 shadow-lg shadow-cyan-500/30 ${
        pulse ? "animate-pulse" : ""
      }`}
    />
  );
}

function Bubble({ m }: { m: SetupMessage }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md border border-white/10 bg-white/10 px-4 py-2 text-sm text-slate-100">
          {m.text}
        </div>
      </div>
    );
  }
  if (m.kind === "note") {
    return (
      <div className="flex items-center gap-3 pl-11 text-xs text-cyan-300/90">
        <span className="h-px w-6 bg-cyan-400/40" />
        {m.text}
      </div>
    );
  }
  return (
    <div className="flex gap-4">
      <Orb />
      <div className="min-w-0 flex-1 space-y-3">
        <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-slate-100">{m.text}</p>
        {m.jobs.length > 0 && <JobStrip jobs={m.jobs} />}
      </div>
    </div>
  );
}

function JobStrip({ jobs }: { jobs: MarketJob[] }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {jobs.map((j) => (
        <a
          key={j.url}
          href={j.url}
          target="_blank"
          rel="noreferrer"
          className="group rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 transition hover:border-cyan-300/50 hover:bg-white/[0.06]"
        >
          <div className="text-[10px] uppercase tracking-widest text-slate-400">{j.company}</div>
          <div className="truncate text-sm text-slate-100">{j.title}</div>
          <div className="truncate text-[11px] text-slate-500">
            {j.location || "—"} <span className="opacity-0 transition group-hover:opacity-100">↗</span>
          </div>
          {j.reason && (
            <div className="mt-1 flex items-start gap-1.5 text-[11px] text-cyan-200/80">
              {typeof j.score === "number" && j.score > 0 && (
                <span className="rounded bg-cyan-400/10 px-1 font-semibold text-cyan-300">
                  {Math.round(j.score * 10)}
                </span>
              )}
              <span className="line-clamp-2">{j.reason}</span>
            </div>
          )}
        </a>
      ))}
    </div>
  );
}

function Thinking({ busy, line }: { busy: Exclude<Busy, null>; line: string }) {
  const label =
    busy === "scanning" ? line : busy === "reading" ? "Reading your resume…" : "";
  return (
    <div className="flex items-center gap-4">
      <Orb pulse />
      <div className="flex items-center gap-3">
        <span className="flex gap-1">
          {[0, 150, 300].map((d) => (
            <span
              key={d}
              className="h-1.5 w-1.5 animate-bounce rounded-full bg-cyan-300"
              style={{ animationDelay: `${d}ms` }}
            />
          ))}
        </span>
        {label && <span className="text-sm text-slate-400">{label}</span>}
      </div>
    </div>
  );
}
