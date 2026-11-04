// ProfileSwitcher — the "whose job search is this?" chip in the top nav.
// Click → quick list of profiles to switch to, plus a link to /profiles for
// creating, importing, exporting and deleting. Switching reloads the app so
// every tab re-reads the new person's data.

"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { ProfilesResponse } from "@/lib/types";

export default function ProfileSwitcher() {
  const [data, setData] = useState<ProfilesResponse | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.profiles().then(setData).catch(() => setData(null));
  }, []);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (!data) return null;
  const active = data.profiles.find((p) => p.active);

  const switchTo = async (slug: string) => {
    setError(null);
    try {
      await api.activateProfile(slug);
      window.location.assign("/");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message);
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-sm font-semibold text-slate-700 transition hover:border-indigo-300 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="grid h-5 w-5 place-items-center rounded-full bg-gradient-to-br from-indigo-500 to-cyan-500 text-[10px] text-white">
          {(active?.name ?? "?").slice(0, 1).toUpperCase()}
        </span>
        <span className="max-w-[10rem] truncate">{active?.name ?? "No profile"}</span>
        <span className="text-xs text-slate-400">▾</span>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-2 w-64 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-900"
        >
          <div className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Profiles
          </div>
          {data.profiles.map((p) => (
            <button
              key={p.slug}
              type="button"
              role="menuitem"
              disabled={p.active || !data.switching_enabled}
              onClick={() => void switchTo(p.slug)}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-slate-50 disabled:cursor-default dark:hover:bg-slate-800"
            >
              <span className="truncate">{p.name}</span>
              <span className="text-xs text-slate-400">
                {p.active ? "active" : p.configured ? `${p.applied} applied` : "setup"}
              </span>
            </button>
          ))}
          {error && <p className="px-3 pb-2 text-xs text-rose-600">{error}</p>}
          <Link
            href="/profiles"
            onClick={() => setOpen(false)}
            className="block border-t border-slate-100 px-3 py-2 text-sm font-semibold text-indigo-600 hover:bg-slate-50 dark:border-slate-800 dark:text-indigo-300 dark:hover:bg-slate-800"
          >
            Manage, import & export →
          </Link>
        </div>
      )}
    </div>
  );
}
