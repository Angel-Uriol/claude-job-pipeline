// "Ask Jobi to fix this" — opens the Jobi chat with a ready message about
// what just went wrong (see components/Jobi.tsx for the "jobi:open" event).

"use client";

export function askJobi(message: string): void {
  window.dispatchEvent(new CustomEvent("jobi:open", { detail: { message } }));
}

export default function AskJobiButton({ message, label = "Ask Jobi to fix this" }: { message: string; label?: string }) {
  return (
    <button
      type="button"
      onClick={() => askJobi(message)}
      className="ml-2 inline-flex items-center gap-1 rounded-full bg-gradient-to-r from-indigo-600 to-cyan-500 px-2.5 py-0.5 text-xs font-semibold text-white shadow-sm hover:brightness-110"
    >
      🤖 {label}
    </button>
  );
}
