// Shown on every page while the demo profile (made-up data) is active.

"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

export default function DemoBanner() {
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    api
      .profiles()
      .then((r) => setDemo(Boolean(r.profiles.find((p) => p.active)?.demo)))
      .catch(() => setDemo(false));
  }, []);

  if (!demo) return null;
  return (
    <div role="status" className="border-b border-amber-300 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
      You&apos;re exploring a <b>demo with made-up data</b> — Claude features are switched off.{" "}
      <Link href="/profiles" className="font-semibold underline">Start my own profile →</Link>
    </div>
  );
}
