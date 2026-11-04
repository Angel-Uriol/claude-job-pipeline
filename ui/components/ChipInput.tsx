// A list-of-words input: type and press Enter (or comma) to add, × to remove.
// Used by the profile and resume editors instead of comma-separated YAML.

"use client";

import { useState } from "react";

export default function ChipInput({
  value,
  onChange,
  placeholder = "Type and press Enter",
  label,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  label: string;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const parts = draft.split(",").map((s) => s.trim()).filter(Boolean);
    if (parts.length) onChange([...value, ...parts.filter((p) => !value.includes(p))]);
    setDraft("");
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2 py-1.5 focus-within:ring-2 focus-within:ring-indigo-400 dark:border-slate-700 dark:bg-slate-800">
      {value.map((v) => (
        <span key={v} className="flex items-center gap-1 rounded-full bg-indigo-100 px-2 py-0.5 text-xs text-indigo-800 dark:bg-indigo-500/20 dark:text-indigo-100">
          {v}
          <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(value.filter((x) => x !== v))} className="opacity-60 hover:opacity-100">
            ×
          </button>
        </span>
      ))}
      <input
        aria-label={label}
        value={draft}
        placeholder={value.length ? "" : placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          } else if (e.key === "Backspace" && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={add}
        className="min-w-[8rem] flex-1 bg-transparent py-0.5 text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100"
      />
    </div>
  );
}
