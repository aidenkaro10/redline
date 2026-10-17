"use client";

/**
 * Today's crew schedule from the planner: 30-minute slots per worker.
 * Lightning stop-work windows show up automatically.
 */

import type { Schedule, TaskKind } from "@/lib/contract";

// Workload is ordered, so it uses one hue light -> dark. Breaks and stop-work get their own colors.
const TASK_STYLE: Record<TaskKind, { bg: string; label: string }> = {
  heavy: { bg: "#256abf", label: "Heavy" },
  moderate: { bg: "#5598e7", label: "Moderate" },
  light: { bg: "#9ec5f4", label: "Light" },
  break: { bg: "var(--ok)", label: "Break" },
  stop_work: { bg: "var(--lightning)", label: "Stop work" },
  off: { bg: "transparent", label: "Off" },
};

export function SchedulePanel({ schedule, now }: { schedule: Schedule; now: number }) {
  const slots = schedule.rows[0]?.slots.length ?? 0;
  const span = slots * schedule.slot_minutes * 60;
  const nowPct = ((now - schedule.day_start) / span) * 100;
  const hours = Array.from({ length: slots / 2 + 1 }, (_, i) => schedule.day_start + i * 3600);

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-x-auto">
        <div className="min-w-[620px]">
          {/* hour labels */}
          <div className="ml-28 flex text-[10px] text-muted tabular">
            {hours.map((h, i) => (
              <span key={h} className="flex-1" style={{ flexGrow: i === hours.length - 1 ? 0 : 1 }}>
                {new Date(h * 1000).toLocaleTimeString("en-US", { hour: "numeric" })}
              </span>
            ))}
          </div>
          <div className="relative mt-1 space-y-1.5">
            {schedule.rows.map((r) => (
              <div key={r.worker_id} className="flex items-center gap-2">
                <span className="w-26 shrink-0 truncate text-xs font-medium">{r.name}</span>
                <div className="flex flex-1 gap-[2px]">
                  {r.slots.map((s) => (
                    <div key={s.start} title={`${new Date(s.start * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}: ${s.label}`}
                      className={`h-5 flex-1 rounded-[3px] ${s.task === "off" ? "border border-dashed border-line" : ""}`}
                      style={{ background: TASK_STYLE[s.task].bg, opacity: s.start + 1800 < now ? 0.45 : 1 }} />
                  ))}
                </div>
              </div>
            ))}
            {nowPct >= 0 && nowPct <= 100 && (
              <div className="pointer-events-none absolute inset-y-[-4px] left-28 right-0">
                <div className="absolute inset-y-0 w-0.5 bg-ink" style={{ left: `${nowPct}%` }}>
                  <span className="absolute -top-4 -translate-x-1/2 rounded bg-ink px-1 text-[9px] font-bold text-page">NOW</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-2">
        {(Object.keys(TASK_STYLE) as TaskKind[]).filter((k) => k !== "off").map((k) => (
          <span key={k} className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: TASK_STYLE[k].bg }} />{TASK_STYLE[k].label}</span>
        ))}
        <span className="ml-auto">{schedule.checked_by_rules ? "✓ Passed the safety rule checker (code, not AI)" : "Not checked"}</span>
      </div>
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-2">
        {schedule.notes.map((n) => <li key={n}>{n}</li>)}
      </ul>
    </div>
  );
}
