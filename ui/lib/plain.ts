// Plain-language words for pipeline jargon. One place, so every page says
// the same thing. Keep in sync with job_pipeline/checklist.py (score_label,
// AXIS_WORDS).

/** 1–10 fit score → a label anyone understands. */
export function scoreLabel(score: number | null | undefined): string {
  if (score == null) return "Not scored yet";
  if (score >= 8) return "Strong match";
  if (score >= 6.5) return "Good match";
  if (score >= 5) return "Possible match";
  return "Weak match";
}

/** Tailwind classes for a score label chip. */
export function scoreTone(score: number | null | undefined): string {
  if (score == null) return "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
  if (score >= 8) return "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-200";
  if (score >= 6.5) return "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-200";
  if (score >= 5) return "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-200";
  return "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300";
}

/** The five scoring axes, in plain words. */
export const AXIS_LABELS: Record<string, string> = {
  stack: "Skills overlap",
  level: "Seniority fit",
  function: "Role type",
  bridgeability: "Gaps you can explain",
  desirability: "Company appeal",
};

export const VERDICT_LABELS: Record<string, string> = {
  apply: "Worth applying",
  consider: "Maybe",
  skip: "Not a fit",
};

/** Short definitions shown by the "?" buttons (components/Explain.tsx). */
export const GLOSSARY: Record<string, string> = {
  score:
    "How well the job fits you, from 1 to 10. Claude reads the posting and compares it with your resume and preferences. 8+ is a strong match.",
  stack: "How many of the skills and tools the job asks for are already on your resume.",
  level: "Whether the job's seniority (junior, mid, senior…) matches your experience.",
  function: "Whether the kind of work (backend, data, ML…) is what you want to do.",
  bridgeability:
    "For skills you don't have yet: how easy it is to honestly explain them with related experience.",
  desirability: "How appealing the company and role look based on your preferences.",
  verdict:
    "Claude's suggestion. 'Worth applying' = go for it, 'Maybe' = look closer, 'Not a fit' = probably skip.",
  unknown:
    "We couldn't verify this from public information. It isn't bad news — ask the recruiter.",
  follow_up:
    "About a week after applying, a short, polite note to the recruiter keeps you on their radar.",
};
