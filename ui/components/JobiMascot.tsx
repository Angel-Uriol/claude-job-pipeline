// Jobi's face: a small round robot-buddy with an antenna and a tiny tie.
// Pure inline SVG (no images). `mood` changes the eyes / mouth:
//   idle  → blinks now and then      thinking → eyes look up, antenna pulses
//   happy → closed smiling eyes       oops     → small worried mouth
// Animation classes live in globals.css (.jobi-*) and switch off when
// the OS asks for reduced motion.

export type JobiMood = "idle" | "thinking" | "happy" | "oops";

export default function JobiMascot({ mood = "idle", size = 56 }: { mood?: JobiMood; size?: number }) {
  const lookUp = mood === "thinking" ? -2 : 0;
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" className="overflow-visible">
      <defs>
        <linearGradient id="jobi-body" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#818cf8" />
          <stop offset="100%" stopColor="#22d3ee" />
        </linearGradient>
        <radialGradient id="jobi-shine" cx="0.3" cy="0.25" r="0.6">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.55" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
        </radialGradient>
      </defs>
      {/* antenna */}
      <line x1="32" y1="12" x2="32" y2="5" stroke="#6366f1" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="32" cy="4.5" r="3.2" fill="#fbbf24" className={mood === "thinking" ? "jobi-pulse" : ""} />
      {/* body */}
      <rect x="8" y="11" width="48" height="44" rx="20" fill="url(#jobi-body)" />
      <rect x="8" y="11" width="48" height="44" rx="20" fill="url(#jobi-shine)" />
      {/* face plate */}
      <rect x="15" y="20" width="34" height="22" rx="11" fill="#0f172a" opacity="0.9" />
      {/* eyes */}
      {mood === "happy" ? (
        <g stroke="#a5f3fc" strokeWidth="2.6" strokeLinecap="round" fill="none">
          <path d="M21 32 q3.5 -4 7 0" />
          <path d="M36 32 q3.5 -4 7 0" />
        </g>
      ) : (
        <g className={mood === "idle" ? "jobi-blink" : ""} style={{ transformOrigin: "32px 31px" }}>
          <ellipse cx="24.5" cy={31 + lookUp} rx="3.6" ry="4.2" fill="#a5f3fc" />
          <ellipse cx="39.5" cy={31 + lookUp} rx="3.6" ry="4.2" fill="#a5f3fc" />
          <circle cx="25.6" cy={29.6 + lookUp} r="1.2" fill="#ffffff" />
          <circle cx="40.6" cy={29.6 + lookUp} r="1.2" fill="#ffffff" />
        </g>
      )}
      {/* mouth */}
      {mood === "oops" ? (
        <path d="M28 39 q4 -2.5 8 0" stroke="#a5f3fc" strokeWidth="2" strokeLinecap="round" fill="none" />
      ) : (
        <path d="M27.5 37.5 q4.5 3.5 9 0" stroke="#a5f3fc" strokeWidth="2" strokeLinecap="round" fill="none" />
      )}
      {/* cheeks */}
      <circle cx="17.5" cy="40" r="2.6" fill="#f472b6" opacity="0.55" />
      <circle cx="46.5" cy="40" r="2.6" fill="#f472b6" opacity="0.55" />
      {/* little tie */}
      <path d="M32 47 l-3 3 l3 4.5 l3 -4.5 z" fill="#fbbf24" />
      {/* arms */}
      <path d="M8 36 q-5 2 -4 8" stroke="#818cf8" strokeWidth="3.5" strokeLinecap="round" fill="none" />
      <path d="M56 36 q5 2 4 8" stroke="#22d3ee" strokeWidth="3.5" strokeLinecap="round" fill="none"
        className={mood === "happy" ? "jobi-wave" : ""} style={{ transformOrigin: "56px 36px" }} />
    </svg>
  );
}
