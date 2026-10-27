// Global top nav: Discover (the discovery/scoring inbox), Track (the
// application tracker) and Setup (connect Claude + the profile
// questionnaire) — plus the theme toggle.

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import ThemeToggle from "./ThemeToggle";

const TABS: { href: string; label: string }[] = [
  { href: "/", label: "Discover" },
  { href: "/track", label: "Track" },
  { href: "/setup", label: "Setup" },
];

export default function TabNav() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 z-20 border-b border-slate-200 bg-slate-50/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
      <div className="mx-auto flex max-w-[1200px] items-center justify-between px-4 py-2">
        <div className="flex items-center gap-1">
          <span className="mr-2 text-sm font-bold tracking-tight text-slate-900 dark:text-slate-50">
            Job Pipeline
          </span>
          {TABS.map((t) => {
            const active =
              t.href === "/" ? pathname === "/" : pathname.startsWith(t.href);
            return (
              <Link
                key={t.href}
                href={t.href}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold transition ${
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
        <ThemeToggle />
      </div>
    </nav>
  );
}
