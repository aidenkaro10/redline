"use client";

/**
 * Video mode: one button per scene, for recording the demo video. Only shown at /manager?video=1.
 * Each button jumps the built-in demo backend to that moment of a recorded July day
 * and plays it back fast. Keys 1-5 do the same, 0 goes back to normal.
 *   1 Day before  6 PM briefing and tomorrow's plan
 *   2 Morning     7:30 AM, today's plan for each person
 *   3 Heat        12:40 PM, one person on an urgent repair runs hot, the plan changes
 *   4 Lightning   3:05 PM, strikes at 6 then 5 miles, everyone stops work
 *   5 SOS         a crewmate presses SOS on their band
 */

import { useEffect, useState } from "react";

import { apiPost, useLive } from "@/lib/live";

const SCENES = [
  { id: "briefing", label: "Day before" },
  { id: "morning", label: "Morning" },
  { id: "heat", label: "Heat" },
  { id: "lightning", label: "Lightning" },
  { id: "sos", label: "SOS" },
] as const;

export function VideoBar({ team }: { team: string }) {
  const live = useLive();
  const scene = live.demo?.scene ?? null;
  const [busy, setBusy] = useState(false);

  const go = async (id: string) => {
    setBusy(true);
    try { await apiPost("/demo/scene", { scene: id, team }); } finally { setBusy(false); }
  };

  // Keyboard shortcuts, so the mouse doesn't have to move on camera
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const n = Number(e.key);
      if (e.key === "0") go("live");
      else if (n >= 1 && n <= SCENES.length) go(SCENES[n - 1].id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team]);

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[13px]" aria-label="Video mode">
      <span className="mr-1 font-medium text-muted">Video</span>
      {SCENES.map((s, i) => (
        <button key={s.id} disabled={busy} onClick={() => go(s.id)} title={`Press ${i + 1}`}
          className={`flex min-h-8 items-center gap-1.5 rounded-full px-3 py-1 font-semibold transition-colors disabled:opacity-60 ${scene === s.id ? "bg-ink text-page" : "bg-black/[0.05] text-ink-2 hover:bg-black/10 dark:bg-white/10 dark:hover:bg-white/15"}`}>
          <span className="tabular opacity-60">{i + 1}</span>{s.label}
        </button>
      ))}
      {scene && (
        <button disabled={busy} onClick={() => go("live")} title="Press 0"
          className="min-h-8 rounded-full px-3 py-1 font-semibold text-ink-2 underline underline-offset-2 hover:text-ink disabled:opacity-60">Exit</button>
      )}
    </div>
  );
}
