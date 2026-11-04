// Global top nav in plain words: Today (home: what to do next), Jobs (every
// scored job), Applications (the tracker), Compare, Interview prep, My info
// (profile + resume editors) and Setup — plus the profile switcher and theme.

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import ProfileSwitcher from "./ProfileSwitcher";
import ThemeToggle from "./ThemeToggle";

const TABS: { href: string; label: string }[] = [
  { href: "/", label: "Today" },
  { href: "/jobs", label: "Jobs" },
  { href: "/applications", label: "Applications" },
  { href: "/compare", label: "Compare" },
  { href: "/interview", label: "Interview prep" },
  { href: "/setup", label: "Setup" },
];

export default function TabNav() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 z-20 border-b border-slate-200 bg-slate-50/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
      <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-2 px-4 py-2">
        {/* One row on every screen: tabs scroll sideways on phones instead of
            wrapping into a tall sticky bar that covers the page. */}
        <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto whitespace-nowrap [scrollbar-width:none]">
          <span className="mr-2 hidden text-sm font-bold tracking-tight text-slate-900 sm:inline dark:text-slate-50">
            Job Pipeline
          </span>
          {TABS.map((t) => {
            const active =
              t.href === "/" ? pathname === "/" : pathname.startsWith(t.href);
            return (
              <Link
                key={t.href}
                href={t.href}
                aria-current={active ? "page" : undefined}
                className={`shrink-0 rounded-lg px-3 py-1.5 text-sm font-semibold transition ${
                  active
                    ? "bg-indigo-600 text-white shadow-sm"
                    : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
                }`}
              >
                {t.label}
              </Link>
            );
          })}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ProfileSwitcher />
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}
