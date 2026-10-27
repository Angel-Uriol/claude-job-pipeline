// Natural-language box for Run Chrome Claude. Type free text; Claude
// (via /api/chrome-claude/parse) turns it into a proposed run, which the
// parent opens in the Run Chrome Claude modal pre-filled for confirmation.

"use client";

import { useCallback, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { ChromeClaudeParseResult } from "@/lib/types";

interface Props {
  /** Called with the parsed run; the parent opens the modal pre-filled. */
  onParsed: (result: ChromeClaudeParseResult) => void;
}

export default function ChromeClaudeChat({ onParsed }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.chromeClaudeParse(t);
      onParsed(result);
      setText("");
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `${e.status}: ${e.detail}`
          : (e as Error).message;
      setError(`Couldn't parse that — ${msg}`);
    } finally {
      setBusy(false);
    }
  }, [text, busy, onParsed]);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            }
          }}
          disabled={busy}
          placeholder="Describe a search… e.g. “Stripe and Notion, ML-infra roles posted this week”"
          className="w-80 max-w-full rounded-lg border border-violet-300 bg-white px-3 py-2 text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-400 disabled:opacity-50 dark:border-violet-500/30 dark:bg-slate-800 dark:text-slate-200"
          aria-label="Describe a Chrome Claude search in plain English"
        />
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || !text.trim()}
          className="rounded-lg bg-violet-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Reading…" : "✨ Build run"}
        </button>
      </div>
      {error && (
        <span className="text-xs text-rose-600 dark:text-rose-400">{error}</span>
      )}
    </div>
  );
}
