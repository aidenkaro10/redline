"use client";

/**
 * Alerts and the event log. Every hazard event is listed with source and time,
 * so the site has a record of what was reported and when.
 */

import { useState } from "react";

import { ALERT_LABEL } from "@/lib/contract";
import { useLive } from "@/lib/live";
import { useSites } from "@/lib/crew";
import { AlertIcon, alertTone, fmtClockSec } from "./ui";

export function AlertFeed() {
  const { alerts, log, workers } = useLive();
  const { byId } = useSites();
  const [tab, setTab] = useState<"alerts" | "log">("alerts");
  const list = [...alerts].reverse();
  const entries = [...log].reverse();
  const active = alerts.filter((a) => !a.cleared_at).length;

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex gap-1">
        {(["alerts", "log"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`rounded-md px-2.5 py-1 text-xs font-semibold ${tab === t ? "bg-surface-2 text-ink" : "text-ink-2"}`}>
            {t === "alerts" ? `Alerts${active ? ` (${active} active)` : ""}` : "Event log"}
          </button>
        ))}
      </div>
      <div className="max-h-[340px] flex-1 overflow-y-auto pr-1">
        {tab === "alerts" ? (
          list.length === 0 ? <p className="text-sm text-muted">No alerts yet.</p> : (
            <ul className="space-y-1.5">
              {list.map((a) => (
                <li key={a.id} className={`flex gap-2 rounded-lg px-2 py-1.5 ${a.cleared_at ? "opacity-55" : "bg-surface-2"}`}>
                  <span className={`mt-0.5 ${alertTone(a.kind)}`}><AlertIcon kind={a.kind} className="h-4 w-4" /></span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold leading-tight">
                      {ALERT_LABEL[a.kind]}
                      <span className="ml-1.5 font-normal text-muted">
                        {a.worker_id ? workers[a.worker_id]?.name : byId[a.site_id ?? ""]?.name ?? a.site_id}
                      </span>
                    </p>
                    <p className="text-xs text-ink-2">{a.message}</p>
                    <p className="text-[11px] text-muted tabular">
                      {fmtClockSec(a.started_at)} · {a.source}
                      {a.acknowledged_at ? " · acknowledged" : ""}
                      {a.cleared_at ? ` · cleared ${fmtClockSec(a.cleared_at)}` : " · active"}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )
        ) : (
          <ul className="space-y-1 text-xs">
            {entries.map((e, i) => (
              <li key={i} className="flex gap-2">
                <span className="shrink-0 text-muted tabular">{fmtClockSec(e.at)}</span>
                <span className="text-ink">{e.text} <span className="text-muted">· {e.source}</span></span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
