"use client";

/**
 * Hazards at one site: heat, lightning (10-mile ring + 30-min countdown),
 * flood, wildfire, soil, wildlife. Every row shows its source and time.
 */

import { useState } from "react";

import type { Hazard, HazardIndex, IndexHazard, Site, SiteStatus } from "@/lib/contract";
import { HAZARD_LABEL } from "@/lib/contract";
import { siteMeta } from "@/lib/crew";
import { useSimNow } from "@/lib/live";
import { secondsUntil } from "@/lib/normalize";
import { HAZARD_TEXT_CLASS, HazardIcon, IndexChip, LevelPill, fmtClock, fmtCountdown, indexColor } from "./ui";

export function HazardPanel({ site, status }: { site: Site; status: SiteStatus }) {
  const now = useSimNow(250);
  const lightning = status.hazards.find((h) => h.kind === "lightning");
  const [showAll, setShowAll] = useState(false);
  // Basic view: only hazards that are actually on. "Show all" lists the clear ones too.
  const active = status.hazards.filter((h) => h.level !== "none");
  const shown = showAll ? status.hazards : active;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold leading-tight">{site.name}</h3>
          <p className="text-xs text-muted">{siteMeta(site)}</p>
        </div>
        <div className="text-right">
          <IndexChip index={status.index_tomorrow} />
          <p className="mt-0.5 text-[10px] uppercase tracking-wider text-muted">Tomorrow</p>
        </div>
      </div>
      {status.hazard_index ? <IndexBreakdown hi={status.hazard_index} /> : status.index_drivers.length > 0 && (
        <p className="text-xs text-ink-2">Index driven by: {status.index_drivers.join(" · ")}</p>
      )}

      {lightning?.stop_work && <LightningBanner h={lightning} now={now} />}

      {shown.length === 0 && <p className="text-sm text-ok-ink">No active hazards right now.</p>}
      <ul className={`divide-y divide-line rounded-lg border border-line ${shown.length ? "" : "hidden"}`}>
        {shown.map((h) => (
          <li key={h.kind} className={`flex gap-3 px-3 py-2.5 ${h.stale ? "opacity-55" : ""}`}>
            <span className={`mt-0.5 ${HAZARD_TEXT_CLASS[h.kind]}`}><HazardIcon kind={h.kind} className="h-5 w-5" /></span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-semibold">
                  {HAZARD_LABEL[h.kind]}
                  {h.fake && <span className="ml-1.5 rounded bg-warn-bg px-1 py-px text-[10px] font-bold text-warn-ink">TEST</span>}
                </p>
                <LevelPill level={h.level} />
              </div>
              <p className="text-sm text-ink">{h.headline}</p>
              {h.detail && <p className="text-xs text-ink-2">{h.detail}</p>}
              <p className="mt-0.5 text-[11px] text-muted">
                {[h.source, h.stale ? `feed down, last updated ${fmtClock(h.updated_at)}` : fmtClock(h.updated_at), h.band_pattern ? `band: ${h.band_pattern}` : ""].filter(Boolean).join(" · ")}
              </p>
            </div>
          </li>
        ))}
      </ul>
      {active.length < status.hazards.length && (
        <button onClick={() => setShowAll(!showAll)} className="self-start text-xs font-semibold text-accent">
          {showAll ? "Only active hazards" : `Show all ${status.hazards.length} (${status.hazards.length - active.length} clear)`}
        </button>
      )}
    </div>
  );
}

function LightningBanner({ h, now }: { h: Hazard; now: number }) {
  const l = h.lightning!;
  const left = l.all_clear_at ? secondsUntil(l.all_clear_at, now) : 0;
  return (
    <div className="rounded-xl border-2 border-lightning bg-surface-2 p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="flex items-center gap-1.5 text-sm font-black uppercase tracking-wide text-lightning">
            <HazardIcon kind="lightning" className="h-5 w-5" /> Stop work: lightning
          </p>
          <p className="text-xs text-ink-2">Nearest strike {l.nearest_miles} mi (10-mile rule). Last strike {fmtClock(l.last_strike_at)}.</p>
        </div>
        <div className="text-right">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">All clear in</p>
          <p className="text-3xl font-black tabular text-ink">{fmtCountdown(left)}</p>
        </div>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line">
        <div className="h-full bg-lightning transition-[width]" style={{ width: `${Math.max(0, Math.min(100, (left / 1800) * 100))}%` }} />
      </div>
      <p className="mt-1 text-[11px] text-muted">The 30-minute clock restarts with every strike within 10 miles.</p>
    </div>
  );
}

const INDEX_HAZARDS: { key: IndexHazard; label: string }[] = [
  { key: "heat", label: "Heat" }, { key: "storm", label: "Storms" }, { key: "flood", label: "Flood" },
  { key: "fire", label: "Fire" }, { key: "tropical", label: "Tropical" },
];

/** Session C2's 0-10 index for tomorrow: per-hazard bars vs a usual day, and the top reasons. */
function IndexBreakdown({ hi }: { hi: HazardIndex }) {
  return (
    <details className="rounded-lg border border-line px-3 py-2 text-xs">
      <summary className="cursor-pointer font-semibold">
        Tomorrow: <span className="capitalize">{hi.band}</span> ({hi.index.toFixed(1)}/10) · a usual day here scores {hi.climatology_index.toFixed(1)}
        {hi.data_mode === "mock" && <span className="ml-1.5 rounded bg-warn-bg px-1 py-px text-[10px] font-bold text-warn-ink">MOCK</span>}
      </summary>
      <ul className="mt-2 space-y-1">
        {INDEX_HAZARDS.filter((h) => hi.hazards[h.key]).map((h) => {
          const part = hi.hazards[h.key]!;
          return (
            <li key={h.key} className="flex items-center gap-2">
              <span className="w-14 shrink-0 text-ink-2">{h.label}</span>
              <span className="relative h-2 flex-1 rounded-full bg-surface-2" title={`${part.index.toFixed(1)} (usual day ${part.climatology_index.toFixed(1)})`}>
                <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${part.index * 10}%`, background: indexColor(part.index) }} />
                <span className="absolute -top-0.5 h-3 w-0.5 bg-ink-2" style={{ left: `${part.climatology_index * 10}%` }} aria-label="usual day" />
              </span>
              <span className="w-7 text-right tabular">{part.index.toFixed(1)}</span>
            </li>
          );
        })}
      </ul>
      <p className="mt-1 text-[10px] text-muted">Bars: tomorrow. Tick: a usual day this month.</p>
      {hi.top_reasons.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-ink-2">
          {hi.top_reasons.map((r) => <li key={r.feature}>{r.text} <span className="text-muted tabular">(+{r.points.toFixed(1)})</span></li>)}
        </ul>
      )}
      <p className="mt-1 text-[10px] text-muted">Uses only what was known by 6 PM the day before. Ranks sites, never makes a safety call.{hi.notes.length ? ` ${hi.notes.join(" ")}` : ""}</p>
    </details>
  );
}
