// Light/dark theme toggle. Flips the `.dark` class on <html> and persists
// the choice to localStorage. The no-FOUC script in layout.tsx applies the
// saved choice before paint; this component just lets the user change it.

"use client";

import { useEffect, useState } from "react";

export default function ThemeToggle() {
  // Start null so the first render matches the server (which can't know the
  // theme); we sync to the real DOM state after mount to avoid a hydration
  // mismatch on the icon.
  const [dark, setDark] = useState<boolean | null>(null);

  useEffect(() => {
    // One-time read of the class the no-FOUC script set before paint —
    // can't be a render-time read because `document` is undefined in SSR.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggle = () => {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* localStorage unavailable (private mode) — ignore */
    }
    setDark(next);
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle dark mode"
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-base text-slate-600 shadow-sm transition hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
    >
      {/* Until mounted we don't know the theme; render a neutral glyph. */}
      <span suppressHydrationWarning>{dark === null ? "◐" : dark ? "☀️" : "🌙"}</span>
    </button>
  );
}
