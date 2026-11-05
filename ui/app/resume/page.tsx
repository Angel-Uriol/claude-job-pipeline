// My info → Resume: edit the facts every tailored resume is built from.
// Saves resume_content.yaml via PUT /api/resume. Bullet ids stay stable (the
// tailorer merges by id); new bullets get the next free id on the server.
// If you add a number that wasn't there before, you're asked to double-check
// it first — Claude never invents numbers, so the ones here must be real.

"use client";

import { useEffect, useState } from "react";

import { useJobiRefresh } from "@/lib/useJobiRefresh";

import ChipInput from "@/components/ChipInput";
import MyInfoTabs from "@/components/MyInfoTabs";
import { ApiError, api } from "@/lib/api";

interface Bullet { id: string; text: string; tags: string[] }
interface Exp { key?: string; company: string; title: string; location: string; start: string; end: string; bullets: Bullet[] }
interface Proj { key?: string; title: string; stack: string; bullets: Bullet[] }
interface Edu { school: string; degree: string; location: string; start: string; end: string; honors: string }
interface Resume {
  header: { name: string; email: string; phone: string; linkedin: string; location: string };
  summaries: Record<string, string>;
  experiences: Exp[];
  projects: Proj[];
  education: Edu[];
  skills: Record<string, string[]>;
  additional: Record<string, string>;
}

const INPUT =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const CARD = "rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900";
const LINK = "text-sm text-indigo-600 underline dark:text-indigo-400";
const X = "px-2 text-slate-400 hover:text-rose-500";

const SKILL_WORDS: Record<string, string> = {
  languages: "Programming languages",
  data_platform: "Data & databases",
  cloud: "Cloud & infrastructure",
  ml: "Machine learning",
  frontend: "Frontend",
  other: "Other tools",
};

function In({ label, value, onChange, className = "" }: { label: string; value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="text-xs font-medium text-slate-600 dark:text-slate-300">{label}</span>
      <input className={`${INPUT} mt-0.5`} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Bullets({ items, onChange }: { items: Bullet[]; onChange: (b: Bullet[]) => void }) {
  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs font-medium text-slate-600 dark:text-slate-300">Bullet points</p>
      {items.map((b, i) => (
        <div key={b.id || `new-${i}`} className="flex gap-2">
          <textarea
            className={`${INPUT} min-h-[3.5rem] flex-1`}
            value={b.text}
            aria-label={`Bullet ${i + 1}`}
            onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
          />
          <button type="button" aria-label="Remove bullet" className={X} onClick={() => onChange(items.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button type="button" className={LINK} onClick={() => onChange([...items, { id: "", text: "", tags: [] }])}>
        + Add a bullet point
      </button>
    </div>
  );
}

export default function ResumePage() {
  const [r, setR] = useState<Resume | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  const load = () =>
    api
      .getResume()
      .then((x) => setR(x.resume as unknown as Resume))
      .catch((e) => setMsg({ ok: false, text: e instanceof ApiError ? e.detail : String(e) }));

  useEffect(() => {
    load();
  }, []);
  useJobiRefresh(load);

  if (!r) return <main className="mx-auto max-w-3xl px-4 py-8 text-sm text-slate-500">{msg?.text ?? "Loading…"}</main>;

  const save = async (confirm = false) => {
    setSaving(true);
    setMsg(null);
    try {
      const cleaned: Resume = {
        ...r,
        experiences: r.experiences.map((e) => ({ ...e, bullets: e.bullets.filter((b) => b.text.trim()) })),
        projects: r.projects.map((p) => ({ ...p, bullets: p.bullets.filter((b) => b.text.trim()) })),
      };
      const res = await api.saveResume(cleaned as unknown as Record<string, unknown>, confirm);
      if (!res.saved) {
        setWarnings(res.warnings);
      } else {
        setWarnings([]);
        setMsg({ ok: true, text: "Saved. New resumes use this from now on." });
        await load(); // pick up the ids the server gave new bullets
      }
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.detail : String(e) });
    } finally {
      setSaving(false);
    }
  };

  const setExp = (i: number, e: Exp) => setR({ ...r, experiences: r.experiences.map((x, j) => (j === i ? e : x)) });
  const setProj = (i: number, p: Proj) => setR({ ...r, projects: r.projects.map((x, j) => (j === i ? p : x)) });
  const setEdu = (i: number, e: Edu) => setR({ ...r, education: r.education.map((x, j) => (j === i ? e : x)) });

  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-50">My info</h1>
        <MyInfoTabs active="resume" />
      </div>
      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
        Every tailored resume is built from these facts. Claude may reword them for a job, but never adds
        anything that isn&apos;t here.
      </p>

      <div className="mt-6 space-y-6">
        <section className={`${CARD} grid gap-3 sm:grid-cols-2`}>
          {(["name", "email", "phone", "linkedin", "location"] as const).map((k) => (
            <In key={k} label={k === "linkedin" ? "LinkedIn URL" : k[0].toUpperCase() + k.slice(1)} value={r.header[k]}
              onChange={(v) => setR({ ...r, header: { ...r.header, [k]: v } })} />
          ))}
        </section>

        <section className={CARD}>
          <h2 className="font-semibold text-slate-900 dark:text-slate-100">Summary</h2>
          {Object.entries(r.summaries).map(([k, v]) => (
            <textarea key={k} className={`${INPUT} mt-2 min-h-28`} value={v} aria-label="Summary"
              onChange={(e) => setR({ ...r, summaries: { ...r.summaries, [k]: e.target.value } })} />
          ))}
        </section>

        <section className={`${CARD} space-y-6`}>
          <h2 className="font-semibold text-slate-900 dark:text-slate-100">Experience</h2>
          {r.experiences.map((e, i) => (
            <div key={e.key ?? `new-${i}`} className="border-t border-slate-100 pt-4 first:border-0 first:pt-0 dark:border-slate-800">
              <div className="grid gap-3 sm:grid-cols-2">
                <In label="Company" value={e.company} onChange={(v) => setExp(i, { ...e, company: v })} />
                <In label="Job title" value={e.title} onChange={(v) => setExp(i, { ...e, title: v })} />
                <In label="Location" value={e.location} onChange={(v) => setExp(i, { ...e, location: v })} />
                <div className="grid grid-cols-2 gap-2">
                  <In label="Start" value={e.start} onChange={(v) => setExp(i, { ...e, start: v })} />
                  <In label="End" value={e.end} onChange={(v) => setExp(i, { ...e, end: v })} />
                </div>
              </div>
              <Bullets items={e.bullets} onChange={(b) => setExp(i, { ...e, bullets: b })} />
              <button type="button" className="mt-2 text-xs text-rose-600" onClick={() => setR({ ...r, experiences: r.experiences.filter((_, j) => j !== i) })}>
                Remove this job
              </button>
            </div>
          ))}
          <button type="button" className={LINK}
            onClick={() => setR({ ...r, experiences: [...r.experiences, { company: "", title: "", location: "", start: "", end: "", bullets: [] }] })}>
            + Add a job
          </button>
        </section>

        <section className={`${CARD} space-y-6`}>
          <h2 className="font-semibold text-slate-900 dark:text-slate-100">Projects</h2>
          {r.projects.map((p, i) => (
            <div key={p.key ?? `new-${i}`} className="border-t border-slate-100 pt-4 first:border-0 first:pt-0 dark:border-slate-800">
              <div className="grid gap-3 sm:grid-cols-2">
                <In label="Project name" value={p.title} onChange={(v) => setProj(i, { ...p, title: v })} />
                <In label="Built with" value={p.stack} onChange={(v) => setProj(i, { ...p, stack: v })} />
              </div>
              <Bullets items={p.bullets} onChange={(b) => setProj(i, { ...p, bullets: b })} />
              <button type="button" className="mt-2 text-xs text-rose-600" onClick={() => setR({ ...r, projects: r.projects.filter((_, j) => j !== i) })}>
                Remove this project
              </button>
            </div>
          ))}
          <button type="button" className={LINK} onClick={() => setR({ ...r, projects: [...r.projects, { title: "", stack: "", bullets: [] }] })}>
            + Add a project
          </button>
        </section>

        <section className={`${CARD} space-y-4`}>
          <h2 className="font-semibold text-slate-900 dark:text-slate-100">Education</h2>
          {r.education.map((e, i) => (
            <div key={i} className="grid gap-3 sm:grid-cols-2">
              <In label="School" value={e.school} onChange={(v) => setEdu(i, { ...e, school: v })} />
              <In label="Degree" value={e.degree} onChange={(v) => setEdu(i, { ...e, degree: v })} />
              <In label="Start" value={e.start} onChange={(v) => setEdu(i, { ...e, start: v })} />
              <In label="End" value={e.end} onChange={(v) => setEdu(i, { ...e, end: v })} />
              <In label="Honors (optional)" value={e.honors} onChange={(v) => setEdu(i, { ...e, honors: v })} className="sm:col-span-2" />
            </div>
          ))}
          <button type="button" className={LINK}
            onClick={() => setR({ ...r, education: [...r.education, { school: "", degree: "", location: "", start: "", end: "", honors: "" }] })}>
            + Add education
          </button>
        </section>

        <section className={`${CARD} space-y-3`}>
          <h2 className="font-semibold text-slate-900 dark:text-slate-100">Skills</h2>
          {Object.keys(SKILL_WORDS).map((k) => (
            <div key={k}>
              <p className="text-xs font-medium text-slate-600 dark:text-slate-300">{SKILL_WORDS[k]}</p>
              <ChipInput label={SKILL_WORDS[k]} value={r.skills[k] ?? []} onChange={(v) => setR({ ...r, skills: { ...r.skills, [k]: v } })} />
            </div>
          ))}
        </section>

        <section className={`${CARD} grid gap-3`}>
          <h2 className="font-semibold text-slate-900 dark:text-slate-100">Additional</h2>
          <In label="Languages" value={r.additional.languages ?? ""} onChange={(v) => setR({ ...r, additional: { ...r.additional, languages: v } })} />
          <In label="Certifications" value={r.additional.certifications ?? ""} onChange={(v) => setR({ ...r, additional: { ...r.additional, certifications: v } })} />
          <In label="Academic background" value={r.additional.academic_background ?? ""} onChange={(v) => setR({ ...r, additional: { ...r.additional, academic_background: v } })} />
        </section>
      </div>

      <div className="sticky bottom-0 mt-6 border-t border-slate-200 bg-slate-50/90 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
        {warnings.length > 0 && (
          <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-100">
            <p className="font-semibold">Quick check before saving</p>
            <ul className="mt-1 list-disc pl-5">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            <div className="mt-2 flex gap-2">
              <button type="button" className="rounded-md bg-amber-600 px-3 py-1 text-xs font-semibold text-white" onClick={() => save(true)}>
                They&apos;re accurate — save
              </button>
              <button type="button" className="rounded-md px-3 py-1 text-xs underline" onClick={() => setWarnings([])}>
                Let me fix them
              </button>
            </div>
          </div>
        )}
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => save(false)} disabled={saving}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
            {saving ? "Saving…" : "Save"}
          </button>
          {msg && <p role="status" className={`text-sm ${msg.ok ? "text-emerald-600" : "text-rose-600"}`}>{msg.text}</p>}
        </div>
      </div>
    </main>
  );
}
