// Re-run `reload` when Jobi applies a change (the "jobi:applied" window
// event), so the page shows the new profile / resume / jobs without a refresh.

"use client";

import { useEffect, useRef } from "react";

export function useJobiRefresh(reload: () => unknown): void {
  const ref = useRef(reload);
  useEffect(() => {
    ref.current = reload;
  });
  useEffect(() => {
    const on = () => void ref.current();
    window.addEventListener("jobi:applied", on);
    return () => window.removeEventListener("jobi:applied", on);
  }, []);
}
