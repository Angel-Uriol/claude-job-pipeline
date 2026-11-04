// Print-ready resume (and cover letter, if written) for one job:
//   /print/resume?job=<key>
// Renders the same spec the .docx was made from, laid out for US Letter with
// 1-inch margins in Arial. "Save as PDF" opens the browser's print dialog,
// where "Save as PDF" is a destination. The .docx stays the main download.

"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { PrintSpec } from "@/lib/types";

interface Header { name: string; email?: string; phone?: string; linkedin?: string; location?: string }
interface Bullet { id: string; text: string }
interface Exp { company: string; title: string; location?: string; start?: string; end?: string; bullets: Bullet[] }
interface Proj { title: string; stack?: string; bullets: Bullet[] }
interface Edu { school: string; degree: string; start?: string; end?: string; honors?: string }
interface Spec {
  section_order: string[];
  header: Header;
  summary: string;
  experiences: Exp[];
  projects: Proj[];
  education: Edu[];
  skills: { group: string; items: string[] }[];
  additional: Record<string, string>;
}

const PRINT_CSS = `
@page { size: letter; margin: 1in; }
@media print {
  nav, [data-noprint], [role="status"] { display: none !important; }
  body { background: white !important; }
  .sheet { box-shadow: none !important; margin: 0 !important; padding: 0 !important; width: auto !important; }
  .page-break { break-before: page; }
}
`;

function contact(h: Header): string {
  return [h.location, h.email, h.linkedin?.replace(/^https?:\/\//, ""), h.phone].filter(Boolean).join("  ·  ");
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-4 border-b border-[#1F3864] pb-0.5 text-[13pt] font-bold uppercase tracking-wide text-[#1F3864]">
      {children}
    </h2>
  );
}

function Resume({ s }: { s: Spec }) {
  const sections: Record<string, React.ReactNode> = {
    summary: s.summary && (
      <section key="summary">
        <Heading>Summary</Heading>
        <p className="mt-1">{s.summary}</p>
      </section>
    ),
    experience: s.experiences.length > 0 && (
      <section key="experience">
        <Heading>Experience</Heading>
        {s.experiences.map((e) => (
          <div key={`${e.company}-${e.title}`} className="mt-2 break-inside-avoid">
            <p className="flex justify-between gap-4">
              <span><b>{e.company}</b>{e.location ? `  ·  ${e.location}` : ""}</span>
              <i>{[e.start, e.end].filter(Boolean).join(" – ")}</i>
            </p>
            <p className="italic">{e.title}</p>
            <ul className="ml-5 list-disc">{e.bullets.map((b) => <li key={b.id}>{b.text}</li>)}</ul>
          </div>
        ))}
      </section>
    ),
    projects: s.projects.length > 0 && (
      <section key="projects">
        <Heading>Projects</Heading>
        {s.projects.map((p) => (
          <div key={p.title} className="mt-2 break-inside-avoid">
            <p><b>{p.title}</b>{p.stack ? `  ·  ${p.stack}` : ""}</p>
            <ul className="ml-5 list-disc">{p.bullets.map((b) => <li key={b.id}>{b.text}</li>)}</ul>
          </div>
        ))}
      </section>
    ),
    education: s.education.length > 0 && (
      <section key="education">
        <Heading>Education</Heading>
        {s.education.map((e) => (
          <div key={e.school} className="mt-1">
            <p className="flex justify-between gap-4">
              <span><b>{e.school}</b>{e.degree ? `  ·  ${e.degree}` : ""}</span>
              <i>{[e.start, e.end].filter(Boolean).join(" – ")}</i>
            </p>
            {e.honors && <p>{e.honors}</p>}
          </div>
        ))}
      </section>
    ),
    skills: s.skills.length > 0 && (
      <section key="skills">
        <Heading>Skills</Heading>
        {s.skills.map((g) => (
          <p key={g.group} className="mt-0.5"><b>{g.group}:</b> {g.items.join(", ")}</p>
        ))}
      </section>
    ),
    additional: Object.values(s.additional ?? {}).some(Boolean) && (
      <section key="additional">
        <Heading>Additional</Heading>
        {Object.entries(s.additional).filter(([, v]) => v).map(([k, v]) => (
          <p key={k} className="mt-0.5"><b>{k.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())}:</b> {v}</p>
        ))}
      </section>
    ),
  };
  return (
    <>
      <h1 className="text-center text-[18pt] font-bold text-[#1F3864]">{s.header.name}</h1>
      <p className="text-center">{contact(s.header)}</p>
      {(s.section_order ?? Object.keys(sections)).map((k) => sections[k] ?? null)}
    </>
  );
}

function PrintView() {
  const key = useSearchParams().get("job") ?? "";
  const [data, setData] = useState<PrintSpec | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!key) return;
    api
      .resumeSpec(key)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.detail : (e as Error).message));
  }, [key]);

  if (!key) return <p className="p-8">No job selected.</p>;
  if (error) return <p className="p-8 text-rose-600">{error}</p>;
  if (!data) return <p className="p-8 text-slate-500">Loading…</p>;
  const letter = data.cover_letter;

  return (
    <div className="bg-slate-100 py-6 dark:bg-slate-950">
      <style>{PRINT_CSS}</style>
      <div data-noprint className="mx-auto mb-4 flex max-w-[8.5in] flex-wrap items-center justify-between gap-3 px-4">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          {data.title} · {data.company}. In the print window choose <b>Save as PDF</b>.
        </p>
        <button
          type="button"
          onClick={() => window.print()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
        >
          Save as PDF
        </button>
      </div>
      <article
        className="sheet mx-auto w-[8.5in] max-w-full bg-white p-[1in] font-[Arial,Helvetica,sans-serif] text-[11pt] leading-snug text-black shadow-lg"
      >
        <Resume s={data.resume as unknown as Spec} />
        {letter && (
          <div className="page-break mt-16 print:mt-0">
            <h1 className="text-center text-[18pt] font-bold text-[#1F3864]">{letter.header.name}</h1>
            <p className="text-center">{contact(letter.header)}</p>
            <p className="mt-6">{letter.date}</p>
            <p className="mt-4">Re: {letter.title}</p>
            <p className="mt-4">{letter.greeting}</p>
            {letter.paragraphs.map((p, i) => <p key={i} className="mt-3">{p}</p>)}
            <p className="mt-4">{letter.closing}</p>
            <p className="mt-6">{letter.header.name}</p>
          </div>
        )}
      </article>
    </div>
  );
}

export default function PrintResumePage() {
  return (
    <Suspense fallback={<p className="p-8 text-slate-500">Loading…</p>}>
      <PrintView />
    </Suspense>
  );
}
