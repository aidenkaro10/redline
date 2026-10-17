"use client";

/**
 * One worker, as the crew lead sees it: status, minutes until predicted
 * danger, current hazard, break countdown. No raw biometrics (privacy rule).
 */

import Link from "next/link";

import type { WorkerStatus } from "@/lib/contract";
import { ALERT_LABEL } from "@/lib/contract";
import { SITE_BY_ID } from "@/lib/sites";
import { AlertIcon, StatusBadge, alertTone, fmtCountdown } from "./ui";

const EDGE: Record<WorkerStatus["status"], string> = {
  green: "border-l-ok",
  yellow: "border-l-warn",
  red: "border-l-danger",
};

export function WorkerCard({ w, now, down }: { w: WorkerStatus; now: number; down: "mandown" | "sos" | null }) {
  const site = SITE_BY_ID[w.site_id];
  const breakIn = w.break_at ? (w.break_at - now) : null;
  return (
    <article className={`flex flex-col gap-2.5 rounded-xl border border-line border-l-[6px] bg-surface p-3.5 ${down === "sos" ? "border-l-sos ring-2 ring-sos" : down ? "border-l-danger ring-2 ring-danger" : EDGE[w.status]}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate font-semibold leading-tight">{w.name}</h3>
          <p className="truncate text-xs text-muted">{w.role} · {site?.name.replace(" Generating Station", "") ?? w.site_id}</p>
        </div>
        {down === "sos" ? <span className="pulse-sos rounded-full px-2 py-0.5 text-xs font-bold text-white">SOS</span>
          : down ? <span className="pulse-red rounded-full px-2 py-0.5 text-xs font-bold text-white">MAN DOWN</span>
            : <StatusBadge status={w.status} />}
      </div>

      <div>
        <p className="text-xs text-muted">Predicted danger</p>
        <p className={`text-2xl font-bold tabular leading-tight ${w.status === "red" ? "text-danger-ink" : w.status === "yellow" ? "text-warn-ink" : "text-ink"}`}>
          {w.minutes_to_danger === null ? "None in 30 min" : w.minutes_to_danger === 0 ? "Now" : `in ${w.minutes_to_danger} min`}
        </p>
        <p className="mt-0.5 text-xs text-ink-2">{w.status_reason}</p>
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-2 text-xs">
        {w.hazard ? (
          <span className={`flex items-center gap-1 font-semibold ${alertTone(w.hazard)}`}>
            <AlertIcon kind={w.hazard} className="h-3.5 w-3.5" /> {ALERT_LABEL[w.hazard]}
          </span>
        ) : <span className="text-muted">No active alerts</span>}
        {w.alert_acknowledged !== null && (
          <span className={w.alert_acknowledged ? "text-ok-ink" : "font-semibold text-danger-ink"}>
            {w.alert_acknowledged ? "✓ Acknowledged" : "Not acknowledged"}
          </span>
        )}
        <span className="ml-auto text-ink-2 tabular">
          {w.on_break
            ? `${w.hazard === "LIGHTNING" ? "Stopped for lightning" : "On break"} · back in ${breakIn !== null ? fmtCountdown(breakIn) : "--"}`
            : breakIn !== null ? `Break in ${Math.max(0, Math.round(breakIn / 60))} min` : ""}
        </span>
      </div>
      <Link href={`/worker/${w.worker_id}`} className="text-xs font-medium text-accent hover:underline">Open phone view →</Link>
    </article>
  );
}
