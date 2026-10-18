"use client";

/**
 * The Plan tab on the manager dashboard.
 *   Today:    each person's day in 30-minute blocks (heavy, moderate, light, break, stop work),
 *             why it looks that way (real forecast numbers), and what changed during the day.
 *   Tomorrow: the 6 PM briefing (forecast, heat and storm hours, what changes) and tomorrow's plan.
 * The plan comes from fixed safety rules in code (mock/planner.ts), never from AI.
 */

import { useEffect, useState } from "react";

import type { Briefing, Schedule, TaskKind } from "@/lib/contract";
import { apiGet, useLive, useSimNow } from "@/lib/live";
import { Icon } from "./health";

const COLOR: Record<TaskKind | "storm", string> = {
  heavy: "#ff6b2c", moderate: "#ff9f0a", light: "#30b0c7", storm: "#7a78e0", break: "#34c759", stop_work: "#5856d6", off: "transparent",
};
const NAME: Record<TaskKind | "storm", string> = {
  heavy: "Heavy", moderate: "Moderate", light: "Light", storm: "Indoor (storms)", break: "Break", stop_work: "Stop work", off: "Off",
};
const kindOf = (s: { task: TaskKind; label: string }) => (s.task === "light" && s.label.startsWith("Indoor") ? "storm" : s.task);
const clock = (t: number) => new Date(t * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });

export function PlanView({ day, onDay, workerIds }: { day: "today" | "tomorrow"; onDay: (d: "today" | "tomorrow") => void; workerIds: string[] }) {
  const live = useLive();
  const now = useSimNow(5000);
  const [sched, setSched] = useState<Schedule | null>(null);
  const [brief, setBrief] = useState<Briefing | null>(null);
  const scene = live.demo?.scene ?? null;

  // Refresh every few seconds (the plan changes when someone runs hot or lightning hits)
  useEffect(() => {
    let alive = true;
    const load = () => {
      apiGet<Schedule>(`/schedule?day=${day}`).then((s) => alive && setSched(s)).catch(() => {});
      if (day === "tomorrow") apiGet<Briefing>("/briefing").then((b) => alive && setBrief(b)).catch(() => {});
    };
    load();
    const t = setInterval(load, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [day, scene]);

  const rows = (sched?.rows ?? []).filter((r) => workerIds.includes(r.worker_id));
  // Only this crew's changes (plus site-wide ones like lightning)
  const changes = (sched?.changes ?? []).filter((c) => !c.worker_id || workerIds.includes(c.worker_id));
  // Show the working part of the day only
  const first = Math.min(...rows.flatMap((r) => r.slots.filter((s) => s.task !== "off").map((s) => s.start)), Infinity);
  const last = Math.max(...rows.flatMap((r) => r.slots.filter((s) => s.task !== "off").map((s) => s.start)), -Infinity);
  const span = Number.isFinite(first) ? { from: first, to: last + 1800 } : null;
  const hours = span ? Array.from({ length: Math.ceil((span.to - span.from) / 3600) + 1 }, (_, i) => Math.ceil(span.from / 3600) * 3600 + i * 3600).filter((t) => t <= span.to) : [];
  const pct = (t: number) => (span ? ((t - span.from) / (span.to - span.from)) * 100 : 0);

  return (
    <div className="flex flex-col gap-4 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="grid grid-cols-2 rounded-[10px] bg-black/[0.06] p-0.5 text-[13px] font-semibold dark:bg-white/10" role="tablist">
          {(["today", "tomorrow"] as const).map((d) => (
            <button key={d} role="tab" aria-selected={day === d} onClick={() => onDay(d)}
              className={`min-h-8 rounded-[8px] px-5 py-1.5 capitalize ${day === d ? "bg-surface text-ink shadow-[0_1px_3px_rgba(0,0,0,0.12)]" : "text-ink-2"}`}>{d}</button>
          ))}
        </div>
        {sched && (
          <span className="flex items-center gap-1.5 text-[13px] text-muted">
            <span className="h-3.5 w-3.5" style={{ color: sched.checked_by_rules ? "#34c759" : "#ff3b30" }}><Icon.check /></span>
            {sched.checked_by_rules ? "Checked by the safety rules" : "Safety check failed"} · {sched.source}
          </span>
        )}
      </div>

      {day === "tomorrow" && brief && (
        <section className="rounded-[16px] bg-surface p-5 shadow-[0_1px_2px_rgba(20,20,10,0.04)]">
          <p className="text-[13px] font-medium text-muted">6 PM briefing · for {new Date(brief.for_date + "T12:00:00").toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" })}</p>
          <p className="mt-2 text-[22px] font-semibold leading-snug tracking-[-0.02em] text-balance">{brief.summary}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {(brief.sites[0]?.windows ?? []).map((w) => (
              <span key={w.kind} className="rounded-full px-3 py-1 text-[13px] font-semibold" style={{ background: w.kind === "heat" ? "#fff1e0" : "#ecebfb", color: w.kind === "heat" ? "#b25000" : "#4846b8" }}>{w.text}</span>
            ))}
          </div>
        </section>
      )}

      {/* The timeline: one row per person */}
      <section className="rounded-[16px] bg-surface p-4 shadow-[0_1px_2px_rgba(20,20,10,0.04)]">
        <div className="overflow-x-auto">
          <div className="min-w-[560px]">
            <div className="relative ml-28 h-5 text-[11px] text-muted">
              {hours.map((t) => <span key={t} className="absolute -translate-x-1/2 tabular" style={{ left: `${pct(t)}%` }}>{new Date(t * 1000).toLocaleTimeString("en-US", { hour: "numeric" })}</span>)}
            </div>
            <div className="flex flex-col gap-2">
              {rows.map((r) => (
                <div key={r.worker_id} className="flex items-center gap-3">
                  <span className="w-25 shrink-0 truncate text-[14px] font-semibold" style={{ width: 100 }}>{r.name}</span>
                  <div className="relative h-8 flex-1 overflow-hidden rounded-lg bg-surface-2">
                    {r.slots.filter((s) => s.task !== "off" && span && s.start >= span.from && s.start < span.to).map((s) => (
                      <span key={s.start} title={`${clock(s.start)} · ${s.label}`}
                        className={`absolute inset-y-0 border-r border-surface ${s.changed ? "ring-2 ring-inset ring-ink/70" : ""}`}
                        style={{ left: `${pct(s.start)}%`, width: `${pct(s.start + 1800) - pct(s.start)}%`, background: COLOR[kindOf(s)] }} />
                    ))}
                    {day === "today" && span && now > span.from && now < span.to && (
                      <span className="absolute inset-y-0 w-0.5 bg-ink" style={{ left: `${pct(now)}%` }} aria-label="now" />
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2">
          {(["heavy", "moderate", "light", "storm", "break", "stop_work"] as const).map((k) => (
            <span key={k} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: COLOR[k] }} />{NAME[k]}</span>
          ))}
          {day === "today" && <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm ring-2 ring-inset ring-ink/70" />Changed today</span>}
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-[16px] bg-surface p-5 shadow-[0_1px_2px_rgba(20,20,10,0.04)]">
          <p className="text-[15px] font-semibold">Why the plan looks like this</p>
          <ul className="mt-2 flex flex-col gap-1.5 text-[14px] leading-snug text-ink-2">
            {(sched?.notes ?? []).map((n) => <li key={n} className="flex gap-2"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-muted" />{n}</li>)}
          </ul>
        </section>
        {day === "today" && (
          <section className="rounded-[16px] bg-surface p-5 shadow-[0_1px_2px_rgba(20,20,10,0.04)]">
            <p className="text-[15px] font-semibold">Changes today</p>
            {changes.length ? (
              <ul className="mt-2 flex flex-col gap-2 text-[14px] leading-snug">
                {[...changes].reverse().map((c) => (
                  <li key={c.at + c.text} className="flex gap-3"><span className="w-16 shrink-0 font-semibold tabular text-muted">{clock(c.at)}</span><span>{c.text}</span></li>
                ))}
              </ul>
            ) : <p className="mt-2 text-[14px] text-muted">Nothing yet. When someone runs hot or lightning gets close, the plan changes here.</p>}
          </section>
        )}
      </div>
    </div>
  );
}
