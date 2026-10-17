"use client";

/**
 * Hazards at one site: heat, lightning (10-mile ring + 30-min countdown),
 * flood, wildfire, soil, wildlife. Every row shows its source and time.
 */

import type { Hazard, Site, SiteStatus } from "@/lib/contract";
import { HAZARD_LABEL } from "@/lib/contract";
import { siteMeta } from "@/lib/crew";
import { useSimNow } from "@/lib/live";
import { HAZARD_TEXT_CLASS, HazardIcon, IndexChip, LevelPill, fmtClock, fmtCountdown } from "./ui";

export function HazardPanel({ site, status }: { site: Site; status: SiteStatus }) {
  const now = useSimNow(250);
  const lightning = status.hazards.find((h) => h.kind === "lightning");
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
      {status.index_drivers.length > 0 && (
        <p className="text-xs text-ink-2">Index driven by: {status.index_drivers.join(" · ")}</p>
      )}

      {lightning?.stop_work && <LightningBanner h={lightning} now={now} />}

      <ul className="divide-y divide-line rounded-lg border border-line">
        {status.hazards.map((h) => (
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
    </div>
  );
}

function LightningBanner({ h, now }: { h: Hazard; now: number }) {
  const l = h.lightning!;
  const left = (l.all_clear_at ?? now) - now;
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
