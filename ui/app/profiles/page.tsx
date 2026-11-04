// Profiles — one complete memory per person.
//
// Each profile keeps its own config, resume, target companies, setup chat
// and history (Discover inbox, tracker of what was applied to, resumes).
// Here you can create, switch, rename, export (JSON) and import profiles.
// Importing one of our exports restores it exactly; importing any other
// JSON about a person lets Claude pre-fill a new profile's setup chat.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { ProfilesResponse, ProfileSummary } from "@/lib/types";

const CARD =
  "rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900";
const BTN =
  "rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50";
const GHOST =
  "rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800";
const INPUT =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:border-slate-700 dark:bg-slate-800";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : (e as Error).message;
}

export default function ProfilesPage() {
  const [data, setData] = useState<ProfilesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.profiles());
    } catch (e) {
      setError(errText(e));
    }
  }, []);

  useEffect(() => {
    // Initial load; setState happens after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const act = async (label: string, fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (after) after();
      else await load();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  };

  const goHome = () => {
    window.location.assign("/");
  };

  const importFile = (file: File) =>
    void act("import", async () => {
      const r = await api.importProfile(file);
      setNotice(
        r.mode === "restore"
          ? `Restored “${r.name}” with ${r.restored?.jobs ?? 0} tracked jobs.`
          : `Created “${r.name}” from your file — the setup chat will ask only what's missing.`,
      );
      await api.activateProfile(r.slug);
      setTimeout(() => {
        window.location.assign(r.mode === "restore" && r.configured ? "/" : "/setup");
      }, 1200);
    }, () => undefined);

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-8">
      <h1 className="text-2xl font-bold tracking-tight">Profiles</h1>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        Each profile is a complete memory for one person: their profile, resume, target companies,
        setup chat, and job history — including what they already applied to.
      </p>

      {data && !data.switching_enabled && (
        <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
          This app is pinned to one profile folder (JOB_PIPELINE_PROFILE_DIR), so switching is off.
        </p>
      )}
      {error && (
        <p className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </p>
      )}
      {notice && (
        <p className="mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
          {notice}
        </p>
      )}

      <div className="mt-6 grid gap-3">
        {data?.profiles.map((p) => (
          <ProfileCard
            key={p.slug}
            p={p}
            canSwitch={data.switching_enabled}
            busy={busy}
            onSwitch={() => void act(`use:${p.slug}`, () => api.activateProfile(p.slug), goHome)}
            onRename={(name) => void act(`ren:${p.slug}`, () => api.renameProfile(p.slug, name))}
            onDelete={() => void act(`del:${p.slug}`, () => api.deleteProfile(p.slug))}
          />
        ))}
        {data && data.profiles.length === 0 && (
          <p className="text-sm text-slate-400">No profiles yet — create one below.</p>
        )}
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <section className={CARD}>
          <h2 className="font-semibold">New profile</h2>
          <p className="mt-1 text-xs text-slate-500">Starts empty; you&apos;ll go through setup for it.</p>
          <form
            className="mt-3 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!newName.trim()) return;
              void act("create", () => api.createProfile(newName.trim()), () => {
                window.location.assign("/setup");
              });
            }}
          >
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Name, e.g. Sam"
              className={INPUT}
              disabled={!data?.switching_enabled}
            />
            <button type="submit" className={BTN} disabled={!newName.trim() || busy !== null}>
              Create
            </button>
          </form>
        </section>

        <section className={CARD}>
          <h2 className="font-semibold">Import from JSON</h2>
          <p className="mt-1 text-xs text-slate-500">
            A profile exported from this app is restored exactly. Any other JSON about a person
            (resume export, notes) — Claude reads it and pre-fills the setup chat.
          </p>
          <div
            className="mt-3 rounded-xl border-2 border-dashed border-slate-300 p-4 text-center dark:border-slate-700"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = e.dataTransfer.files?.[0];
              if (f) importFile(f);
            }}
          >
            <button
              type="button"
              className={GHOST}
              disabled={busy !== null || !data?.switching_enabled}
              onClick={() => fileRef.current?.click()}
            >
              {busy === "import" ? "Importing…" : "Choose .json file"}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) importFile(f);
                e.target.value = "";
              }}
            />
          </div>
        </section>
      </div>
    </main>
  );
}

function ProfileCard({
  p,
  canSwitch,
  busy,
  onSwitch,
  onRename,
  onDelete,
}: {
  p: ProfileSummary;
  canSwitch: boolean;
  busy: string | null;
  onSwitch: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(p.name);
  const [confirm, setConfirm] = useState("");

  return (
    <section className={`${CARD} ${p.active ? "ring-2 ring-indigo-400/60" : ""}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-indigo-500 to-cyan-500 font-semibold text-white">
            {p.name.slice(0, 1).toUpperCase()}
          </span>
          <div className="min-w-0">
            {editing ? (
              <form
                className="flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  onRename(name);
                  setEditing(false);
                }}
              >
                <input value={name} onChange={(e) => setName(e.target.value)} className={INPUT} />
                <button type="submit" className={GHOST}>Save</button>
              </form>
            ) : (
              <div className="flex items-center gap-2">
                <span className="truncate font-semibold">{p.name}</span>
                {p.active && (
                  <span className="rounded bg-indigo-100 px-1.5 text-[10px] font-semibold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-200">
                    ACTIVE
                  </span>
                )}
              </div>
            )}
            <div className="text-xs text-slate-500">
              {p.configured
                ? `${p.applied} applied · ${p.tracked_jobs} in tracker`
                : p.chat_started
                  ? "Setup in progress"
                  : "Not set up yet"}
              {" · "}
              <code>{p.slug}</code>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {!p.active && (
            <button type="button" className={BTN} disabled={!canSwitch || busy !== null} onClick={onSwitch}>
              {busy === `use:${p.slug}` ? "Switching…" : "Switch"}
            </button>
          )}
          <a className={GHOST} href={api.exportProfileUrl(p.slug)} download>
            Export JSON
          </a>
          <button type="button" className={GHOST} onClick={() => setEditing((v) => !v)}>
            Rename
          </button>
        </div>
      </div>

      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-slate-400">Delete…</summary>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-slate-500">
            Deletes everything for this person. Type <code>{p.slug}</code> to confirm:
          </span>
          <input
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className="w-40 rounded border border-slate-300 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-800"
          />
          <button
            type="button"
            disabled={confirm !== p.slug || busy !== null}
            onClick={onDelete}
            className="rounded bg-rose-600 px-2 py-1 font-semibold text-white disabled:opacity-40"
          >
            Delete forever
          </button>
        </div>
      </details>
    </section>
  );
}
