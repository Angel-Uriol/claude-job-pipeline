// Jobi's chat window: messages, live progress, change cards (Apply /
// Dismiss), the "hand off to Claude Code" card, and a cost line per answer.
// Nothing Jobi proposes is saved until you click Apply on its card.

"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import JobiMascot, { type JobiMood } from "@/components/JobiMascot";
import { ApiError, api } from "@/lib/api";
import type { JobiCard, JobiEvent, JobiMessage, JobiState } from "@/lib/types";

const STARTERS = [
  "Change my profile",
  "Add a company to my searches",
  "Find new jobs",
  "Why did my Chrome search fail?",
];

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : (e as Error).message;
}

// --- tiny, safe markdown: paragraphs, bullets, **bold**, `code` -------------

function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("**") && p.endsWith("**") ? (
          <b key={i}>{p.slice(2, -2)}</b>
        ) : p.startsWith("`") && p.endsWith("`") ? (
          <code key={i} className="rounded bg-slate-200/70 px-1 text-[0.85em] dark:bg-white/10">{p.slice(1, -1)}</code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

function Markdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={blocks.length} className={`ml-5 space-y-0.5 ${list.ordered ? "list-decimal" : "list-disc"}`}>
        {list.items.map((it, i) => <li key={i}><Inline text={it} /></li>)}
      </Tag>,
    );
    list = null;
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const num = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || num) {
      const ordered = Boolean(num);
      if (list && list.ordered !== ordered) flush();
      list = list ?? { ordered, items: [] };
      list.items.push((bullet ?? num)![1]);
      continue;
    }
    flush();
    if (line.trim()) {
      const heading = line.match(/^#{1,4}\s+(.*)$/);
      blocks.push(
        <p key={blocks.length} className={heading ? "font-semibold" : ""}>
          <Inline text={heading ? heading[1] : line} />
        </p>,
      );
    }
  }
  flush();
  return <div className="space-y-2">{blocks}</div>;
}

// --- change card -------------------------------------------------------------

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function Diff({ card }: { card: JobiCard }) {
  if (card.kind === "set_companies") {
    const before = new Set((card.before as string[]) ?? []);
    const after = new Set((card.after as string[]) ?? []);
    const added = [...after].filter((c) => !before.has(c));
    const removed = [...before].filter((c) => !after.has(c));
    return (
      <div className="space-y-1 text-xs">
        {added.length > 0 && <p className="text-emerald-700 dark:text-emerald-300">+ Add: {added.join(", ")}</p>}
        {removed.length > 0 && <p className="text-rose-700 dark:text-rose-300">− Remove: {removed.join(", ")}</p>}
      </div>
    );
  }
  if (card.kind === "run_command") {
    return <code className="block rounded bg-slate-900 px-2 py-1 text-xs text-slate-100">{String(card.payload.shown ?? "")}</code>;
  }
  if (card.kind === "start_premium_search") {
    return <p className="text-xs">Companies: {show(card.payload.companies)}</p>;
  }
  const before = (card.before ?? {}) as Record<string, unknown>;
  const after = (card.after ?? {}) as Record<string, unknown>;
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  if (!keys.length) return null;
  return (
    <dl className="space-y-1.5 text-xs">
      {keys.map((k) => (
        <div key={k}>
          <dt className="font-semibold text-slate-600 dark:text-slate-300">{k.replace(/_/g, " ")}</dt>
          {Object.keys(before).length > 0 && (
            <dd className="text-slate-400 line-through decoration-rose-400/70">{show(before[k])}</dd>
          )}
          {k in after && <dd className="text-emerald-700 dark:text-emerald-300">{show(after[k])}</dd>}
        </div>
      ))}
    </dl>
  );
}

function CardView({
  card,
  onApply,
  onDismiss,
  log,
}: {
  card: JobiCard;
  onApply: (c: JobiCard) => Promise<void>;
  onDismiss: (c: JobiCard) => Promise<void>;
  log?: string;
}) {
  const [busy, setBusy] = useState(false);
  const pending = card.status === "pending";
  const badge: Record<JobiCard["status"], string> = {
    pending: "",
    applied: "✓ Applied",
    dismissed: "Dismissed",
    failed: "Didn't work",
  };
  return (
    <div className="jobi-pop rounded-xl border border-indigo-200 bg-white p-3 shadow-sm dark:border-indigo-500/30 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{card.title}</p>
        {!pending && (
          <span className={`shrink-0 text-xs font-semibold ${card.status === "applied" ? "text-emerald-600" : card.status === "failed" ? "text-rose-600" : "text-slate-400"}`}>
            {badge[card.status]}
          </span>
        )}
      </div>
      {card.summary && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{card.summary}</p>}
      <div className="mt-2"><Diff card={card} /></div>
      {card.warnings.map((w) => (
        <p key={w} className="mt-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">⚠ {w}</p>
      ))}
      {card.status === "failed" && card.result && <p className="mt-2 text-xs text-rose-600">{card.result}</p>}
      {log && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-slate-500">Output</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-2 text-[11px] text-slate-100">{log}</pre>
        </details>
      )}
      {pending && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              await onApply(card);
              setBusy(false);
            }}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? "Applying…" : "Apply"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onDismiss(card)}
            className="rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

function HandoffCard({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="jobi-pop rounded-xl border border-cyan-200 bg-cyan-50/60 p-3 dark:border-cyan-500/30 dark:bg-cyan-500/5">
      <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">Hand this to Claude Code</p>
      <ol className="mt-1 list-decimal pl-4 text-xs text-slate-600 dark:text-slate-300">
        <li>Copy the message below.</li>
        <li>Open Claude Desktop → <b>Code</b> → start a new session in the app&apos;s folder.</li>
        <li>Paste it and send. That chat can use Chrome and fix things directly.</li>
      </ol>
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-2 text-[11px] text-slate-100">{text}</pre>
      <button
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          } catch {
            setCopied(false);
          }
        }}
        className="mt-2 rounded-lg bg-cyan-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-cyan-700"
      >
        {copied ? "Copied ✓" : "Copy message"}
      </button>
    </div>
  );
}

// --- the panel ---------------------------------------------------------------------

interface LiveTurn {
  text: string;
  status: string;
  cards: string[];
  handoff: string;
}

export default function JobiPanel({
  page,
  initialMessage,
  onClose,
  onMood,
}: {
  page: string;
  initialMessage: string;
  onClose: () => void;
  onMood: (m: JobiMood) => void;
}) {
  const [state, setState] = useState<JobiState | null>(null);
  const [cards, setCards] = useState<Record<string, JobiCard>>({});
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sentInitial = useRef("");

  const load = useCallback(async () => {
    try {
      const s = await api.jobi();
      setState(s);
      setCards(Object.fromEntries(s.cards.map((c) => [c.id, c])));
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [state?.history.length, live?.text, live?.cards.length, live?.status]);

  const send = useCallback(
    async (message: string) => {
      const m = message.trim();
      if (!m || live) return;
      setError(null);
      setText("");
      setState((s) => (s ? { ...s, history: [...s.history, { role: "user", text: m }] } : s));
      setLive({ text: "", status: "Thinking…", cards: [], handoff: "" });
      onMood("thinking");
      let failed = false;
      try {
        await api.jobiChat(m, page, (e: JobiEvent) => {
          if (e.type === "text") setLive((l) => l && { ...l, text: l.text + e.text, status: "" });
          else if (e.type === "status") setLive((l) => l && { ...l, status: e.text });
          else if (e.type === "proposal") {
            setCards((c) => ({ ...c, [e.card.id]: e.card }));
            setLive((l) => l && { ...l, cards: [...l.cards, e.card.id] });
          } else if (e.type === "handoff") setLive((l) => l && { ...l, handoff: e.text });
          else if (e.type === "error") {
            failed = true;
            setError(e.text);
          }
        });
      } catch (e) {
        failed = true;
        setError(errText(e));
      }
      setLive(null);
      onMood(failed ? "oops" : "happy");
      await load();
    },
    [live, page, load, onMood],
  );

  useEffect(() => {
    if (initialMessage && state && sentInitial.current !== initialMessage) {
      sentInitial.current = initialMessage;
      void send(initialMessage);
    }
  }, [initialMessage, state, send]);

  const apply = async (card: JobiCard) => {
    setError(null);
    try {
      const r = await api.jobiApply(card.id);
      setCards((c) => ({ ...c, [card.id]: r.card }));
      if (r.log) setLogs((l) => ({ ...l, [card.id]: r.log! }));
      if (r.client_action?.run) {
        try {
          await api.runPhase(r.client_action.run);
        } catch (e) {
          setError(errText(e));
        }
      }
      window.dispatchEvent(new CustomEvent("jobi:applied", { detail: { kind: card.kind, changed: r.changed } }));
      onMood(r.ok === false ? "oops" : "happy");
    } catch (e) {
      setError(errText(e));
      await load();
    }
  };

  const dismiss = async (card: JobiCard) => {
    try {
      await api.jobiDismiss(card.id);
      setCards((c) => ({ ...c, [card.id]: { ...card, status: "dismissed" } }));
    } catch (e) {
      setError(errText(e));
    }
  };

  const reset = async () => {
    try {
      await api.jobiReset();
      setLogs({});
      await load();
    } catch (e) {
      setError(errText(e));
    }
  };

  const history = (state?.history ?? []).filter((m) => m.role !== "note" || m.text.startsWith("Couldn"));

  const renderAssistant = (m: JobiMessage, i: number) => (
    <div key={i} className="space-y-2">
      {m.text && (
        <div className="max-w-[92%] rounded-2xl rounded-tl-sm bg-slate-100 px-3 py-2 text-sm text-slate-800 dark:bg-slate-800 dark:text-slate-100">
          <Markdown text={m.text} />
        </div>
      )}
      {(m.cards ?? []).map((id) => cards[id] && (
        <CardView key={id} card={cards[id]} onApply={apply} onDismiss={dismiss} log={logs[id]} />
      ))}
      {m.handoff && <HandoffCard text={m.handoff} />}
      {typeof m.cost_usd === "number" && m.cost_usd > 0 && (
        <p className="text-[11px] text-slate-400">this answer ≈ ${m.cost_usd.toFixed(2)}</p>
      )}
    </div>
  );

  return (
    <section
      role="dialog"
      aria-label="Jobi, your job-search helper"
      className="jobi-pop fixed inset-x-2 bottom-2 top-16 z-50 flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl sm:inset-x-auto sm:bottom-24 sm:right-5 sm:top-auto sm:h-[min(580px,calc(100vh-8rem))] sm:w-[390px] dark:border-slate-700 dark:bg-slate-950"
    >
      <header className="flex items-center gap-2 border-b border-slate-200 bg-gradient-to-r from-indigo-600 to-cyan-500 px-3 py-2 text-white dark:border-slate-800">
        <JobiMascot size={34} mood={live ? "thinking" : "idle"} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold leading-tight">Jobi</p>
          <p className="truncate text-[11px] opacity-90">Your job-search helper · {state?.model ?? "Claude"}</p>
        </div>
        <button type="button" onClick={() => void reset()} disabled={!!live}
          className="rounded-md px-2 py-1 text-xs font-semibold hover:bg-white/15 disabled:opacity-50">
          New chat
        </button>
        <button type="button" aria-label="Close Jobi" onClick={onClose} className="rounded-md px-2 py-1 text-lg leading-none hover:bg-white/15">
          ×
        </button>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {state?.demo ? (
          <div className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-100">
            Hi! I&apos;m Jobi. In the demo (made-up data) I&apos;m switched off, because I use Claude.{" "}
            <Link href="/profiles" className="font-semibold underline">Start your own profile</Link> and I can help you
            edit it, find jobs and fix problems.
          </div>
        ) : (
          <>
            {history.length === 0 && !live && (
              <div className="space-y-3">
                <div className="rounded-2xl rounded-tl-sm bg-slate-100 px-3 py-2 text-sm dark:bg-slate-800">
                  Hi, I&apos;m <b>Jobi</b>! I can change your profile, resume and companies, start searches, and fix
                  problems. I always show you a change first. Nothing is saved until you click <b>Apply</b>.
                </div>
                <div className="flex flex-wrap gap-2">
                  {STARTERS.map((s) => (
                    <button key={s} type="button" onClick={() => void send(s)}
                      className="rounded-full border border-indigo-200 px-3 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-500/40 dark:text-indigo-300 dark:hover:bg-indigo-500/10">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {history.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-indigo-600 px-3 py-2 text-sm text-white">{m.text}</p>
                </div>
              ) : m.role === "note" ? (
                <p key={i} className="text-center text-xs text-rose-500">{m.text}</p>
              ) : (
                renderAssistant(m, i)
              ),
            )}
            {live && (
              <div className="space-y-2">
                {live.text && (
                  <div className="max-w-[92%] rounded-2xl rounded-tl-sm bg-slate-100 px-3 py-2 text-sm dark:bg-slate-800">
                    <Markdown text={live.text} />
                  </div>
                )}
                {live.cards.map((id) => cards[id] && (
                  <CardView key={id} card={cards[id]} onApply={apply} onDismiss={dismiss} log={logs[id]} />
                ))}
                {live.handoff && <HandoffCard text={live.handoff} />}
                {live.status && (
                  <p className="flex items-center gap-2 text-xs text-slate-500">
                    <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-cyan-500" /> {live.status}
                  </p>
                )}
              </div>
            )}
          </>
        )}
        {error && (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      {!state?.demo && (
        <form
          className="flex items-end gap-2 border-t border-slate-200 p-2 dark:border-slate-800"
          onSubmit={(e) => {
            e.preventDefault();
            void send(text);
          }}
        >
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(text);
              }
            }}
            rows={1}
            placeholder={live ? "Jobi is working…" : "Ask Jobi anything…"}
            aria-label="Message Jobi"
            className="max-h-32 min-h-10 flex-1 resize-none rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:border-slate-700 dark:bg-slate-900"
          />
          <button type="submit" disabled={!!live || !text.trim()} aria-label="Send"
            className="grid h-10 w-10 place-items-center rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40">
            ↑
          </button>
        </form>
      )}
    </section>
  );
}
