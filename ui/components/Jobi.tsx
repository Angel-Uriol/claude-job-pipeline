// The floating Jobi button (bottom-right on every page) and its chat window.
//
// Other parts of the app can open Jobi with a ready message:
//   window.dispatchEvent(new CustomEvent("jobi:open", { detail: { message: "…" } }))
// After a change is applied, Jobi fires "jobi:applied" so pages can reload.

"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import JobiMascot, { type JobiMood } from "@/components/JobiMascot";
import JobiPanel from "@/components/JobiPanel";

const HELLO_KEY = "jobi-hello-seen";

export default function Jobi() {
  const pathname = usePathname() ?? "/";
  const [open, setOpen] = useState(false);
  const [initial, setInitial] = useState("");
  const [mood, setMood] = useState<JobiMood>("idle");
  const [hello, setHello] = useState(false);

  useEffect(() => {
    let seen = true;
    try {
      seen = localStorage.getItem(HELLO_KEY) === "1";
    } catch {
      seen = true;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!seen) setHello(true);
    const onOpen = (e: Event) => {
      const message = (e as CustomEvent<{ message?: string }>).detail?.message ?? "";
      setInitial(message);
      setOpen(true);
    };
    window.addEventListener("jobi:open", onOpen);
    return () => window.removeEventListener("jobi:open", onOpen);
  }, []);

  // Happy / oops faces fade back to idle.
  useEffect(() => {
    if (mood !== "happy" && mood !== "oops") return;
    const t = setTimeout(() => setMood("idle"), 2500);
    return () => clearTimeout(t);
  }, [mood]);

  const dismissHello = useCallback(() => {
    setHello(false);
    try {
      localStorage.setItem(HELLO_KEY, "1");
    } catch {
      /* private window etc. */
    }
  }, []);

  if (pathname.startsWith("/print")) return null;

  return (
    <div data-noprint className="print:hidden">
      {open && (
        <JobiPanel
          page={pathname}
          initialMessage={initial}
          onClose={() => {
            setOpen(false);
            setInitial("");
          }}
          onMood={setMood}
        />
      )}
      {hello && !open && (
        <div className="jobi-pop fixed bottom-24 right-5 z-40 max-w-[220px] rounded-2xl rounded-br-sm bg-white px-3 py-2 text-sm shadow-lg ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
          Hi, I&apos;m <b>Jobi</b>! Ask me anything: profile changes, searches, or if something breaks.
          <button type="button" onClick={dismissHello} className="ml-1 text-xs text-slate-400 underline">
            Got it
          </button>
        </div>
      )}
      <button
        type="button"
        aria-label={open ? "Close Jobi" : "Open Jobi, your job-search helper"}
        aria-expanded={open}
        onClick={() => {
          dismissHello();
          setOpen((o) => !o);
        }}
        className={`fixed bottom-5 right-5 z-50 grid h-16 w-16 place-items-center rounded-full bg-white shadow-xl ring-1 ring-slate-200 transition hover:scale-105 focus:outline-none focus-visible:ring-4 focus-visible:ring-indigo-400 dark:bg-slate-900 dark:ring-slate-700 ${
          open ? "" : mood === "thinking" ? "jobi-wiggle" : "jobi-bob"
        } ${open ? "max-sm:hidden" : ""}`}
      >
        <JobiMascot size={50} mood={mood} />
      </button>
    </div>
  );
}
