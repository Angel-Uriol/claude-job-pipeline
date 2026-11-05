// "Made by" credit card for the start of Setup: who built Jobi and this app,
// with links to the author's LinkedIn and the GitHub repo (lib/credits.ts).

import JobiMascot from "@/components/JobiMascot";
import { CREDITS } from "@/lib/credits";

function LinkedInIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="currentColor">
      <path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.34V9h3.42v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.56V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z" />
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="currentColor">
      <path d="M12 .3a12 12 0 0 0-3.8 23.38c.6.12.83-.26.83-.57v-2.04c-3.34.73-4.04-1.6-4.04-1.6-.55-1.4-1.34-1.77-1.34-1.77-1.08-.74.09-.72.09-.72 1.2.08 1.83 1.23 1.83 1.23 1.07 1.83 2.8 1.3 3.49 1 .1-.78.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.12-3.18 0 0 1-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.3-1.55 3.3-1.23 3.3-1.23.66 1.66.25 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.62-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.57A12 12 0 0 0 12 .3" />
    </svg>
  );
}

export default function MadeBy() {
  return (
    <aside className="mt-5 rounded-2xl bg-gradient-to-r from-indigo-500 via-sky-500 to-cyan-400 p-[1.5px] shadow-sm">
      <div className="flex flex-wrap items-center gap-4 rounded-[15px] bg-white px-4 py-3 dark:bg-slate-900">
        <div className="jobi-bob shrink-0">
          <JobiMascot size={44} mood="happy" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-600 dark:text-cyan-300">
            Meet Jobi · {CREDITS.repoName}
          </p>
          <p className="mt-0.5 text-sm text-slate-700 dark:text-slate-200">
            Jobi and this job-search app were created by{" "}
            <span className="bg-gradient-to-r from-indigo-600 to-cyan-500 bg-clip-text font-bold text-transparent dark:from-indigo-300 dark:to-cyan-300">
              {CREDITS.author}
            </span>
            . Free and open source.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a
            href={CREDITS.linkedinUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full bg-[#0A66C2] px-3 py-1.5 text-xs font-semibold text-white transition hover:brightness-110"
          >
            <LinkedInIcon /> Connect on LinkedIn
          </a>
          <a
            href={CREDITS.repoUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-100 dark:border-slate-600 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            <GitHubIcon /> {CREDITS.repoName}
          </a>
        </div>
      </div>
    </aside>
  );
}
