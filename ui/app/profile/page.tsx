// My info → Profile: edit who you are and what you want, without YAML.
// Saves the `profile:` block of config.yaml via PUT /api/profile (validated,
// previous file kept as config.yaml.bak). Everything here goes into Claude's
// scoring and search prompts.

"use client";

import { useEffect, useState } from "react";

import ChipInput from "@/components/ChipInput";
import MyInfoTabs from "@/components/MyInfoTabs";
import { ApiError, api } from "@/lib/api";
import { useJobiRefresh } from "@/lib/useJobiRefresh";

type Tier = "A" | "B" | "C";
interface TargetFunction { name: string; tier: Tier; notes: string }
interface ProfileInfo {
  name: string; email: string; phone: string; linkedin: string; location: string;
  headline: string; years_experience: number; work_authorization: string;
  needs_sponsorship: boolean; target_levels: string[]; target_functions: TargetFunction[];
  must_have_skills: string[]; preferred_skills: string[]; excluded_titles: string[];
  max_years_required: number; location_preference: string; salary_floor: string;
  highlights: string[]; honest_gaps: string[]; deal_breakers: string[]; dream_companies: string[];
}

const INPUT =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const CARD = "rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900";

const TIER_WORDS: Record<Tier, string> = { A: "Best fit", B: "Good fit", C: "Stretch" };

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-800 dark:text-slate-200">{label}</span>
      {hint && <span className="block text-xs text-slate-500 dark:text-slate-400">{hint}</span>}
      <div className="mt-1">{children}</div>
    </label>
  );
}

export default function ProfilePage() {
  const [p, setP] = useState<ProfileInfo | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const loadProfile = () =>
    api
      .getProfileInfo()
      .then((r) => setP(r.profile as unknown as ProfileInfo))
      .catch((e) => setMsg({ ok: false, text: e instanceof ApiError ? e.detail : String(e) }));

  useEffect(() => {
    void loadProfile();
  }, []);
  useJobiRefresh(loadProfile);

  if (!p) return <main className="mx-auto max-w-3xl px-4 py-8 text-sm text-slate-500">{msg?.text ?? "Loading…"}</main>;

  const set = <K extends keyof ProfileInfo>(k: K, v: ProfileInfo[K]) => setP({ ...p, [k]: v });
  const text = (k: keyof ProfileInfo) => (
    <input className={INPUT} value={String(p[k] ?? "")} onChange={(e) => set(k, e.target.value as never)} />
  );
  const list = (k: keyof ProfileInfo, label: string) => (
    <ChipInput label={label} value={p[k] as string[]} onChange={(v) => set(k, v as never)} />
  );
  const lines = (k: keyof ProfileInfo) => (
    <textarea
      className={`${INPUT} min-h-24`}
      value={(p[k] as string[]).join("\n")}
      onChange={(e) => set(k, e.target.value.split("\n") as never)}
      placeholder="One per line"
    />
  );

  const save = async () => {
    setSaving(true);
    setMsg(null);
    const clean = (xs: string[]) => xs.map((x) => x.trim()).filter(Boolean);
    try {
      await api.saveProfileInfo({
        ...p,
        highlights: clean(p.highlights),
        honest_gaps: clean(p.honest_gaps),
        deal_breakers: clean(p.deal_breakers),
        target_functions: p.target_functions.filter((f) => f.name.trim()),
      } as unknown as Record<string, unknown>);
      setMsg({ ok: true, text: "Saved. The next search and score use this." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.detail : String(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-50">My info</h1>
        <MyInfoTabs active="profile" />
      </div>
      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
        Claude uses this to find and score jobs for you. Be honest — it only helps you.
      </p>

      <div className="mt-6 space-y-6">
        <section className={`${CARD} grid gap-4 sm:grid-cols-2`}>
          <Field label="Name">{text("name")}</Field>
          <Field label="Email">{text("email")}</Field>
          <Field label="Phone">{text("phone")}</Field>
          <Field label="LinkedIn URL">{text("linkedin")}</Field>
          <Field label="Where you live">{text("location")}</Field>
          <Field label="One-line headline" hint='e.g. "Backend engineer, 4 years"'>{text("headline")}</Field>
          <Field label="Years of experience">
            <input type="number" min={0} className={INPUT} value={p.years_experience}
              onChange={(e) => set("years_experience", Number(e.target.value) || 0)} />
          </Field>
          <Field label="Work authorization" hint='In your words, e.g. "US citizen" or "H-1B"'>{text("work_authorization")}</Field>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input type="checkbox" checked={p.needs_sponsorship} onChange={(e) => set("needs_sponsorship", e.target.checked)} className="accent-indigo-600" />
            I need visa sponsorship
          </label>
        </section>

        <section className={`${CARD} space-y-4`}>
          <h2 className="font-semibold text-slate-900 dark:text-slate-100">What you&apos;re looking for</h2>
          <div>
            <p className="text-sm font-medium text-slate-800 dark:text-slate-200">Kinds of roles</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">Best fit = what you do today; Stretch = a step you&apos;d like to take.</p>
            <div className="mt-2 space-y-2">
              {p.target_functions.map((f, i) => (
                <div key={i} className="flex flex-wrap gap-2">
                  <input className={`${INPUT} flex-1`} value={f.name} placeholder="e.g. Backend engineering"
                    onChange={(e) => set("target_functions", p.target_functions.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                  <select className={`${INPUT} w-36`} value={f.tier}
                    onChange={(e) => set("target_functions", p.target_functions.map((x, j) => (j === i ? { ...x, tier: e.target.value as Tier } : x)))}>
                    {(["A", "B", "C"] as Tier[]).map((t) => <option key={t} value={t}>{TIER_WORDS[t]}</option>)}
                  </select>
                  <button type="button" aria-label="Remove role" className="px-2 text-slate-400 hover:text-rose-500"
                    onClick={() => set("target_functions", p.target_functions.filter((_, j) => j !== i))}>×</button>
                </div>
              ))}
              <button type="button" className="text-sm text-indigo-600 underline dark:text-indigo-400"
                onClick={() => set("target_functions", [...p.target_functions, { name: "", tier: "A", notes: "" }])}>
                + Add a kind of role
              </button>
            </div>
          </div>
          <Field label="Seniority levels" hint="e.g. mid, senior">{list("target_levels", "Seniority levels")}</Field>
          <Field label="Where you want to work" hint='e.g. "Bay Area or fully remote in the US"'>{text("location_preference")}</Field>
          <Field label="Minimum salary" hint='e.g. "$150k base"'>{text("salary_floor")}</Field>
          <Field label="Skills a job must use">{list("must_have_skills", "Must-have skills")}</Field>
          <Field label="Nice-to-have skills">{list("preferred_skills", "Nice-to-have skills")}</Field>
          <Field label="Dream companies">{list("dream_companies", "Dream companies")}</Field>
          <Field label="Titles to skip" hint='e.g. "Director", "Intern"'>{list("excluded_titles", "Titles to skip")}</Field>
          <Field label="Deal-breakers" hint='e.g. "heavy on-call"'>{lines("deal_breakers")}</Field>
        </section>

        <section className={`${CARD} space-y-4`}>
          <h2 className="font-semibold text-slate-900 dark:text-slate-100">Your story</h2>
          <Field label="Highlights" hint="Your best work, with real numbers. One per line.">{lines("highlights")}</Field>
          <Field label="Honest gaps" hint="Skills you haven't used for real yet. Claude won't claim them.">{lines("honest_gaps")}</Field>
        </section>
      </div>

      <div className="sticky bottom-0 mt-6 flex items-center gap-3 border-t border-slate-200 bg-slate-50/90 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
        <button type="button" onClick={save} disabled={saving}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
          {saving ? "Saving…" : "Save"}
        </button>
        {msg && <p role="status" className={`text-sm ${msg.ok ? "text-emerald-600" : "text-rose-600"}`}>{msg.text}</p>}
      </div>
    </main>
  );
}
