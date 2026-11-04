// Tabs between the two "My info" editors: profile (/profile) and resume (/resume).

import Link from "next/link";

export default function MyInfoTabs({ active }: { active: "profile" | "resume" }) {
  const cls = (on: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-semibold ${on ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`;
  return (
    <div className="flex gap-1">
      <Link href="/profile" className={cls(active === "profile")}>About me &amp; goals</Link>
      <Link href="/resume" className={cls(active === "resume")}>My resume</Link>
    </div>
  );
}
