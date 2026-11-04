"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { researchApi, type Comparison, type Finding, type InterviewPlan, type Prospects,
  type ResearchJob, type ResearchKind, type ResearchReport } from "@/lib/research";

const PANEL = "rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900";
const FIELD = "rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950";

export default function ResearchWorkspace({ kind }: { kind: ResearchKind }) {
  const [jobs, setJobs] = useState<ResearchJob[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [reports, setReports] = useState<ResearchReport[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [stage, setStage] = useState("Recruiter screen");
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const compare = kind === "comparison";

  useEffect(() => {
    let live = true;
    Promise.all([researchApi.options(kind), researchApi.reports()]).then(([options, history]) => {
      if (!live) return;
      const requested = new URLSearchParams(window.location.search).get("job");
      setJobs(options.jobs);
      setSelected(requested && options.jobs.some(j => j.job_key === requested)
        ? [requested] : options.jobs.slice(0, kind === "comparison" ? 3 : 1).map(j => j.job_key));
      setReports(history.reports);
      setCurrentId(history.reports.find(r => r.kind === kind &&
        (!requested || r.jobs.some(j => j.job_key === requested)))?.id ?? null);
    }).catch(e => { if (live) setError((e as Error).message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [kind]);

  const running = reports.some(r => r.status === "running");
  useEffect(() => {
    if (!running) return;
    let live = true;
    const timer = setInterval(() => {
      // Clear a previous polling error once the backend answers again.
      researchApi.reports().then(data => { if (live) { setReports(data.reports); setError(""); } })
        .catch(() => { if (live) setError("Lost contact with the app's backend — retrying…"); });
    }, 2500);
    return () => { live = false; clearInterval(timer); };
  }, [running]);

  const current = reports.find(r => r.id === currentId);
  const start = async () => {
    setStarting(true); setError("");
    try {
      const report = await researchApi.start(kind, selected, stage, days);
      setReports(prev => [report, ...prev]); setCurrentId(report.id);
    } catch (e) { setError((e as Error).message); }
    finally { setStarting(false); }
  };

  return <main className="mx-auto max-w-[1200px] space-y-6 px-4 py-8 text-slate-900 dark:text-slate-100">
    <header>
      <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-indigo-600 dark:text-indigo-400">{compare ? "Make an informed choice" : "Turn your experience into clear answers"}</p>
      <h1 className="text-3xl font-bold">{compare ? "Compare recommended jobs" : "Interview prep"}</h1>
      <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600 dark:text-slate-400">{compare
        ? "Compare pay, benefits and company reviews against similar roles in the market. Explore your relative interview and hiring prospects alongside the evidence and gaps."
        : "Research the company and role, practice likely topics, and build a preparation plan around your real experience."}</p>
    </header>

    {error && <p role="alert" className="rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800 dark:bg-rose-950 dark:text-rose-200">{error}</p>}
    <section className={PANEL}>
      <h2 className="font-semibold">{compare ? "Choose up to five recommendations" : "Choose an application"}</h2>
      {loading ? <p className="mt-3 text-sm" role="status">Loading…</p> : jobs.length === 0 ?
        <p className="mt-3 text-sm text-slate-500">{compare ? "No apply or consider recommendations yet. Run scoring in Discover first."
          : "Mark a tracked job as applied to start preparing."} <Link className="text-indigo-600 underline" href={compare ? "/" : "/track"}>{compare ? "Open Discover" : "Open Track"}</Link></p> : <>
        <div className="my-4 max-h-64 space-y-2 overflow-y-auto">
          {jobs.map(job => <label key={job.job_key} className={`flex cursor-pointer items-center gap-3 rounded-lg border p-3 text-sm ${selected.includes(job.job_key) ? "border-indigo-400 bg-indigo-50 dark:bg-indigo-500/10" : "border-slate-200 dark:border-slate-700"}`}>
            <input type={compare ? "checkbox" : "radio"} name="research-job" checked={selected.includes(job.job_key)}
              disabled={running || starting || (compare && selected.length >= 5 && !selected.includes(job.job_key))}
              onChange={() => setSelected(prev => !compare ? [job.job_key] : prev.includes(job.job_key)
                ? prev.filter(k => k !== job.job_key) : [...prev, job.job_key])} className="accent-indigo-600" />
            <span className="flex-1"><b>{job.company}</b> · {job.title}</span>
            {job.fit_score != null && <span className="text-xs text-slate-500">Fit {job.fit_score.toFixed(1)}/10</span>}
          </label>)}
        </div>
        {!compare && <div className="mb-4 flex flex-wrap gap-4">
          <label className="flex flex-col gap-1 text-sm">Interview stage
            <select value={stage} onChange={e => setStage(e.target.value)} className={FIELD}>
              {["Recruiter screen", "Hiring manager", "Technical interview", "Behavioral interview", "Take-home presentation", "Final round"].map(s => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">Days to prepare
            <input type="number" min={1} max={60} value={days} onChange={e => setDays(Number(e.target.value))} className={`${FIELD} w-28`} />
          </label>
        </div>}
        <p className="mb-3 max-w-3xl text-xs leading-5 text-slate-500 dark:text-slate-400">Uses your Claude connection: up to six public searches per job, then a tailored analysis. Web search and model usage may incur API charges or consume your plan. Public queries use company, role, location and posting URL; your profile and resume evidence go only to Claude for analysis. Reports stay in this profile. Each click creates a new snapshot.</p>
        <button type="button" onClick={() => void start()} disabled={starting || running || !selected.length || !Number.isInteger(days) || days < 1 || days > 60}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50">
          {starting ? "Starting…" : running ? "Research in progress…" : compare ? `Research & compare (${selected.length})` : "Research & build my prep plan"}
        </button>
      </>}
    </section>

    {reports.some(r => r.kind === kind) && <section>
      <label className="flex flex-wrap items-center gap-3 text-sm font-medium">Saved reports
        <select className={`${FIELD} max-w-full`} value={currentId ?? ""} onChange={e => setCurrentId(e.target.value)}>
          <option value="" disabled>Select a report</option>
          {reports.filter(r => r.kind === kind).map(r => <option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString()} · {r.jobs.map(j => j.company).join(", ")} · {r.status}</option>)}
        </select>
      </label>
    </section>}

    {current && <section className="space-y-5" aria-label="Research report">
      <div className={PANEL}>
        <h2 className="font-semibold">{current.jobs.map(j => `${j.company} · ${j.title}`).join(" / ")}</h2>
        <p className="mt-1 text-xs text-slate-500">Snapshot from {new Date(current.created_at).toLocaleString()}. Refresh research after changes to your profile or the posting.</p>
        <p className="mt-3 text-sm" role="status" aria-live="polite">
          {current.status === "running" && <span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-indigo-500" aria-hidden="true" />}
          {current.progress}
        </p>
        {!!current.warnings?.length && <div className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-500/10 dark:text-amber-200"><BulletList items={current.warnings} /></div>}
      </div>
      {current.status === "complete" && current.content && <>
        {current.kind === "comparison" ? <ComparisonView report={current} /> : <PrepView report={current} />}
        <Sources report={current} />
      </>}
    </section>}
  </main>;
}

function BulletList({ items }: { items: string[] }) {
  return <ul className="list-disc space-y-1 pl-5">{items.map((item, i) => <li key={i}>{item}</li>)}</ul>;
}
function Refs({ ids }: { ids: string[] }) {
  return <span className="ml-1 inline-flex flex-wrap gap-2 text-xs">{ids.map(id => <a key={id} href={`#source-${id}`} className="text-indigo-600 underline dark:text-indigo-400">[{id}]</a>)}</span>;
}
function FindingView({ finding }: { finding: Finding }) {
  return <div className="space-y-2">
    <p className={`font-semibold ${finding.rating === "above market" ? "text-emerald-700 dark:text-emerald-400" : finding.rating === "unknown" ? "text-slate-500" : "text-slate-900 dark:text-slate-100"}`}>{finding.rating} <span className="font-normal text-slate-500">· {finding.confidence} confidence</span></p>
    <p>{finding.explanation}<Refs ids={finding.source_ids} /></p>
    {finding.rating !== "unknown" && finding.cohort && <p className="text-xs text-slate-500"><span className="font-semibold">Compared with:</span> {finding.cohort}</p>}
    {finding.rating !== "unknown" && finding.evidence_quote && <blockquote className="border-l-2 border-slate-300 pl-2 text-xs italic text-slate-600 dark:border-slate-600 dark:text-slate-400">“{finding.evidence_quote}”</blockquote>}
  </div>;
}
function ProspectsView({ value }: { value: Prospects }) {
  return <div className="space-y-2"><p className="font-semibold capitalize">{value.signal}</p><BulletList items={value.reasons} />
    {!!value.blockers.length && <><p className="font-medium">Barriers / unknowns</p><BulletList items={value.blockers} /></>}
    {!!value.next_steps.length && <><p className="font-medium">Improve your position</p><BulletList items={value.next_steps} /></>}
  </div>;
}
function ComparisonView({ report }: { report: ResearchReport }) {
  const data = report.content as Comparison;
  return <>
    <div className={PANEL}><p className="leading-7">{data.summary}</p><p className="mt-3 text-sm text-slate-500">Prospects are qualitative assessments, not measured interview or hiring probabilities. Fit scores are not odds.</p></div>
    <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
      <table className="w-full border-collapse text-left text-sm"><caption className="sr-only">Side-by-side comparison of the selected jobs. Scroll sideways on small screens.</caption><thead className="bg-slate-100 dark:bg-slate-800"><tr><th scope="col" className="p-4">Compare</th>{data.rows.map(row => {
        const job = report.jobs.find(j => j.job_key === row.job_key);
        return <th scope="col" key={row.job_key} className="min-w-64 p-4 align-top sm:min-w-72"><p>{job?.company}</p><p className="mt-1 font-normal text-slate-500">{job?.title}</p></th>;
      })}</tr></thead><tbody className="bg-white dark:bg-slate-900">
        {([['salary', 'Salary'], ['benefits', 'Benefits'], ['company_reviews', 'Company reviews']] as const).map(([key, label]) => <tr key={key} className="border-t border-slate-200 dark:border-slate-800"><th scope="row" className="p-4 align-top">{label}</th>{data.rows.map(row => <td key={row.job_key} className="p-4 align-top"><FindingView finding={row[key]} /></td>)}</tr>)}
        {([['interview_prospects', 'Interview prospects'], ['hiring_prospects', 'Hiring prospects']] as const).map(([key, label]) => <tr key={key} className="border-t border-slate-200 dark:border-slate-800"><th scope="row" className="p-4 align-top">{label}</th>{data.rows.map(row => <td key={row.job_key} className="p-4 align-top"><ProspectsView value={row[key]} /></td>)}</tr>)}
        <tr className="border-t border-slate-200 dark:border-slate-800"><th scope="row" className="p-4 align-top">Tradeoff</th>{data.rows.map(row => <td key={row.job_key} className="p-4 align-top">{row.tradeoff}</td>)}</tr>
      </tbody></table>
    </div>
    <div className={PANEL}><h3 className="mb-2 font-semibold">How to read this comparison</h3><p className="text-sm leading-6">{data.methodology}</p><h3 className="mb-2 mt-4 font-semibold">What still needs checking</h3><div className="text-sm"><BulletList items={data.missing_information} /></div></div>
  </>;
}
function PrepView({ report }: { report: ResearchReport }) {
  const data = report.content as InterviewPlan;
  return <>
    <div className="grid gap-5 md:grid-cols-2">
      <section className={PANEL}><h3 className="mb-3 text-lg font-semibold">Know the company & role</h3><p className="text-sm leading-6">{data.company_brief}<Refs ids={data.source_ids} /></p><div className="mt-4 text-sm"><BulletList items={data.role_priorities} /></div></section>
      <section className={PANEL}><h3 className="mb-3 text-lg font-semibold">Understand the interview</h3><p className="text-sm leading-6">{data.process}</p><h4 className="mb-2 mt-4 font-semibold">Confirm with the recruiter</h4><div className="text-sm"><BulletList items={data.missing_information} /></div></section>
    </div>
    <section className="space-y-3"><h3 className="text-xl font-semibold">Practice your answers</h3><p className="text-sm text-slate-500">Use these outlines in your own words. Practice questions are suggestions; reported questions are anecdotes, not predictions.</p>
      {data.questions.map((q, i) => <details key={i} className={PANEL} open={i === 0}>
        <summary className="cursor-pointer font-semibold">{i+1}. {q.question} <span className="ml-2 text-xs font-normal text-indigo-600 dark:text-indigo-400">{q.kind}</span></summary>
        <div className="mt-4 space-y-3 text-sm leading-6"><p className="text-slate-500">{q.purpose}<Refs ids={q.source_ids} /></p><p className="whitespace-pre-wrap">{q.answer_outline}</p>
          {!!q.evidence_ids.length && <div className="rounded-lg bg-indigo-50 p-3 dark:bg-indigo-500/10"><h4 className="mb-2 font-semibold">Your evidence — original wording</h4><BulletList items={q.evidence_ids.map(id => report.evidence?.[id] ?? id)} /></div>}
          {q.gap_to_acknowledge && <p className="border-l-2 border-amber-500 pl-3">{q.gap_to_acknowledge}</p>}
          {q.verify_note && <p role="note" className="rounded-lg bg-rose-50 p-3 text-rose-800 dark:bg-rose-500/10 dark:text-rose-200">⚠ {q.verify_note}</p>}
        </div>
      </details>)}
    </section>
    <section className={PANEL}><h3 className="mb-4 text-xl font-semibold">Your preparation plan</h3><div className="grid gap-4 md:grid-cols-2">{data.study_plan.map((s, i) => <div key={i} className="rounded-lg border border-slate-200 p-4 text-sm dark:border-slate-700"><p className="text-xs font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400">{s.session}</p><h4 className="my-2 font-semibold">{s.focus}</h4><BulletList items={s.tasks} /><p className="mt-3 text-slate-500">Ready when: {s.success_check}</p></div>)}</div></section>
    <section className={PANEL}><h3 className="mb-3 text-lg font-semibold">Questions to ask them</h3><div className="text-sm"><BulletList items={data.questions_to_ask} /></div></section>
  </>;
}
function Sources({ report }: { report: ResearchReport }) {
  return <section className={PANEL}><h3 className="mb-3 font-semibold">Sources & evidence</h3><p className="mb-4 text-xs text-slate-500">Web links were observed during research. A saved posting is the local snapshot, not a fresh verification. Access time is not publication time. Check the source for current terms and context.</p><ol className="space-y-3 text-sm">{report.sources?.map(s => <li key={s.id} id={`source-${s.id}`} className="scroll-mt-24">
    <a href={s.url} target="_blank" rel="noopener noreferrer" className="break-words text-indigo-600 underline dark:text-indigo-400">[{s.id}] {s.title}</a>
    <p className="text-xs text-slate-500">{s.kind}{s.kind === "search listing" ? " (link only — not read)" : ""}{s.published ? ` · published ${s.published}` : ""} · accessed {new Date(s.accessed_at).toLocaleDateString()}</p>
    {s.excerpt && <details className="mt-1 text-xs text-slate-500"><summary className="cursor-pointer">Evidence excerpt</summary><p className="mt-1 whitespace-pre-wrap">{s.excerpt}</p></details>}
  </li>)}</ol></section>;
}
