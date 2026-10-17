"use client";

/**
 * Crew lead app (phone first). Two tabs at the bottom:
 *   Crew  - one row per worker, worst first. Tap a row for details.
 *   Sites - the risk-ranked map of job sites and tomorrow's 0-10 index. Tap a site for its hazards.
 *
 * Privacy: this page connects as view=crew, so it never receives raw heart
 * rate or core temp. Status, minutes to danger and hazards only.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { HazardPanel } from "@/components/HazardPanel";
import { useIsDark } from "@/components/Nav";
import { SiteMap } from "@/components/SiteMap";
import { AlertIcon, HazardIcon, IndexChip, StatusBadge, alertTone, fmtCountdown } from "@/components/ui";
import type { Site, SiteStatus, Status, WorkerStatus } from "@/lib/contract";
import { ALERT_LABEL, HAZARD_LABEL } from "@/lib/contract";
import { shortSiteName, siteMeta, useCrew, useSites } from "@/lib/crew";
import { dangerText } from "@/lib/format";
import { rankSites } from "@/lib/ranking";
import { useFetch, useLive, useSimNow } from "@/lib/live";
import { secondsUntil } from "@/lib/normalize";

const STATUS_ORDER: Record<Status, number> = { red: 0, yellow: 1, green: 2 };

export default function CrewApp() {
  return <Suspense><CrewAppInner /></Suspense>;
}

function CrewAppInner() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = params.get("tab") === "sites" ? "sites" : "crew";
  const setTab = (t: "crew" | "sites") => router.replace(t === "crew" ? "/" : "/?tab=sites", { scroll: false });

  return (
    <div className="mx-auto flex min-h-screen max-w-xl flex-col pb-20">
      <AppHeader title={tab === "crew" ? "Your crew" : "Job sites"} />
      <main className="flex-1 px-4 py-3">{tab === "crew" ? <CrewTab /> : <SitesTab />}</main>
      <nav className="fixed inset-x-0 bottom-0 z-[600] border-t border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto grid max-w-xl grid-cols-2">
          <TabButton active={tab === "crew"} onClick={() => setTab("crew")} label="Crew" />
          <TabButton active={tab === "sites"} onClick={() => setTab("sites")} label="Sites" />
        </div>
      </nav>
    </div>
  );
}

function TabButton({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button onClick={onClick} className={`py-3.5 text-sm font-bold ${active ? "text-accent" : "text-muted"}`} aria-current={active ? "page" : undefined}>
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Crew tab
// ---------------------------------------------------------------------------

function CrewTab() {
  const live = useLive();
  const crew = useCrew();
  const now = useSimNow(1000);
  const [open, setOpen] = useState<string | null>(null);

  const workers = useMemo(
    () => [...crew.workers].sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.name.localeCompare(b.name)),
    [crew.workers],
  );
  const attention = workers.filter((w) => w.status !== "green").length;

  if (!live.ready) return <p className="py-10 text-center text-sm text-muted">Connecting…</p>;

  return (
    <div className="flex flex-col gap-3">
      <SiteBanners siteIds={crew.siteIds} sites={live.sites} now={now} />

      <p className="text-sm text-ink-2">
        {attention ? <b className="text-ink">{attention} need{attention === 1 ? "s" : ""} attention</b> : <b className="text-ink">Everyone OK</b>}
        {" · "}{workers.length} on shift
      </p>

      <ul className="flex flex-col gap-2">
        {workers.map((w) => (
          <WorkerRow key={w.worker_id} w={w} now={now} open={open === w.worker_id}
            onToggle={() => setOpen(open === w.worker_id ? null : w.worker_id)}
            down={live.mandown?.worker_id === w.worker_id && (live.mandown.stage === "checking" || live.mandown.stage === "confirmed") ? (live.mandown.reason === "sos" ? "sos" : "mandown") : null} />
        ))}
      </ul>
    </div>
  );
}

/** Only shows when a crew site has a real alert (lightning stop, flood or fire warning). */
function SiteBanners({ siteIds, sites, now }: { siteIds: string[]; sites: Record<string, SiteStatus>; now: number }) {
  const { byId } = useSites();
  const names = Object.fromEntries(Object.values(byId).map((s) => [s.id, s.name]));
  const banners = siteIds.flatMap((id) => (sites[id]?.hazards ?? [])
    .filter((h) => h.stop_work || h.band_pattern !== null)    // real alerts only (the band buzzes for them)
    .map((h) => ({ id, h })));
  if (!banners.length) return null;
  return (
    <div className="flex flex-col gap-2">
      {banners.map(({ id, h }) => (
        <div key={id + h.kind} className={`flex items-center gap-3 rounded-xl p-3 ${h.stop_work ? "bg-lightning text-white" : "bg-warn-bg text-warn-ink"}`}>
          <HazardIcon kind={h.kind} className="h-6 w-6 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="font-bold leading-tight">{h.stop_work ? "Stop work" : HAZARD_LABEL[h.kind]} · {shortSiteName(names[id]) || id}</p>
            <p className="truncate text-sm opacity-90">{h.headline}</p>
          </div>
          {h.lightning?.all_clear_at && (
            <div className="text-right">
              <p className="text-[10px] font-bold uppercase opacity-80">All clear</p>
              <p className="text-lg font-black tabular">{fmtCountdown(secondsUntil(h.lightning.all_clear_at, now))}</p>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const EDGE: Record<Status, string> = { green: "border-l-ok", yellow: "border-l-warn", red: "border-l-danger" };

function WorkerRow({ w, now, open, onToggle, down }: { w: WorkerStatus; now: number; open: boolean; onToggle: () => void; down: "mandown" | "sos" | null }) {
  const breakIn = w.break_at ? w.break_at - now : null;
  const danger = dangerText(w.minutes_to_danger);
  const dangerTone = w.minutes_to_danger === 0 || w.status === "red" ? "text-danger-ink" : w.status === "yellow" ? "text-warn-ink" : "text-ink-2";
  return (
    <li className={`overflow-hidden rounded-xl border border-line border-l-[6px] bg-surface ${down === "sos" ? "border-l-sos" : down ? "border-l-danger" : EDGE[w.status]}`}>
      <button onClick={onToggle} className="flex w-full items-center gap-3 px-3 py-3 text-left" aria-expanded={open}>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">{w.name}</p>
          <p className={`text-sm ${w.status === "red" ? "font-semibold text-danger-ink" : w.status === "yellow" ? "font-semibold text-warn-ink" : "text-muted"}`}>
            {down === "sos" ? "SOS" : down ? "Man down" : w.on_break ? (w.hazard === "LIGHTNING" ? "Stopped for lightning" : "On break") : w.status_reason}
          </p>
        </div>
        <div className="shrink-0 text-right" data-testid="danger">
          <p className="text-[10px] font-bold uppercase tracking-wider text-muted">Danger</p>
          <p className={`text-sm font-bold tabular ${dangerTone}`}>{danger}</p>
        </div>
        {w.hazard && <span className={alertTone(w.hazard)}><AlertIcon kind={w.hazard} className="h-5 w-5" /></span>}
        {down
          ? <span className={`${down === "sos" ? "pulse-sos" : "pulse-red"} rounded-full px-2 py-0.5 text-xs font-bold text-white`}>{down === "sos" ? "SOS" : "DOWN"}</span>
          : <StatusBadge status={w.status} />}
      </button>
      {open && (
        <div className="border-t border-line px-3 py-3 text-sm">
          <p className="text-ink-2">{w.status_reason}</p>
          <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5">
            <dt className="text-muted">Alert</dt>
            <dd className={w.hazard ? alertTone(w.hazard) : ""}>{w.hazard ? ALERT_LABEL[w.hazard] : "None"}</dd>
            {w.alert_acknowledged !== null && (<><dt className="text-muted">Acknowledged</dt><dd>{w.alert_acknowledged ? "Yes" : "Not yet"}</dd></>)}
            {breakIn !== null && (<><dt className="text-muted">{w.on_break ? "Back to work" : "Next break"}</dt>
            <dd className="tabular">in {Math.max(0, Math.round(breakIn / 60))} min</dd></>)}
            <dt className="text-muted">Role</dt><dd>{w.role}</dd>
            <dt className="text-muted">Band</dt><dd>{w.band_connected ? "Connected" : "Not connected"}</dd>
          </dl>
          <Link href={`/worker/${w.worker_id}`} className="mt-3 inline-block text-sm font-semibold text-accent">Open their phone view →</Link>
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Sites tab: risk-ranked map + tomorrow's 0-10 index (covers the 6 PM briefing)
// ---------------------------------------------------------------------------

function SitesTab() {
  const live = useLive();
  const crew = useCrew();
  const dark = useIsDark();
  const { sites } = useSites();
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  // Ranked by tomorrow's index, highest first; no index yet = last. Worker alerts show as flags.
  const ranked = useMemo(() => (sites ? rankSites(sites, live.sites) : []), [sites, live.sites]);

  // A stop-work or incident at a crew site: jump the map there.
  const alarm = crew.siteIds.find((id) => live.sites[id]?.worker_flag === "mandown" || live.sites[id]?.worker_flag === "sos" || live.sites[id]?.hazards.some((h) => h.stop_work)) ?? null;
  useEffect(() => { if (alarm) { setSelected(alarm); setFocus(alarm); } }, [alarm]);

  const pick = (id: string) => { setSelected(id); setFocus(id); };
  const sel = sites?.find((s) => s.id === selected);
  const selStatus = selected ? live.sites[selected] : undefined;

  return (
    <div className="flex flex-col gap-3">
      <div className="h-[42vh] min-h-[260px] overflow-hidden rounded-xl border border-line">
        {sites && <SiteMap sites={sites} statuses={live.sites} reports={live.reports} selected={selected} onSelect={pick} dark={dark} focus={focus} />}
      </div>

      {sel && selStatus && (
        <div className="rounded-xl border border-line bg-surface p-3">
          <div className="mb-2 flex justify-end">
            <button onClick={() => setSelected(null)} className="text-xs font-semibold text-muted">Close</button>
          </div>
          <HazardPanel site={sel} status={selStatus} />
        </div>
      )}

      <div>
        <p className="mb-1 text-xs font-bold uppercase tracking-wider text-muted">Riskiest tomorrow</p>
        <ol className="overflow-hidden rounded-xl border border-line bg-surface">
          {(showAll ? ranked : ranked.slice(0, 10)).map((s, i) => {
            const st = live.sites[s.id];
            const stop = st.hazards.some((h) => h.stop_work);
            const mine = crew.siteIds.includes(s.id);
            return (
              <li key={s.id} className="border-b border-line last:border-0">
                <button onClick={() => pick(s.id)} className={`flex w-full items-center gap-2 px-3 py-2.5 text-left ${selected === s.id ? "bg-surface-2" : ""}`}>
                  <span className="w-5 shrink-0 text-xs text-muted tabular">{i + 1}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{s.name}{mine && <span className="ml-1.5 text-xs font-semibold text-accent">your crew</span>}</span>
                    <span className="block truncate text-xs">
                      {st.worker_flag === "mandown" ? <b className="text-danger-ink">Man down</b>
                        : st.worker_flag === "sos" ? <b className="text-sos">SOS</b>
                          : st.worker_flag === "heat" ? <b className="text-danger-ink">Worker heat alert</b>
                            : stop ? <b className="text-lightning">Stop work</b>
                              : <span className="text-muted">{st.hazard_index?.top_reasons[0]?.text ?? siteMeta(s, false)}</span>}
                    </span>
                  </span>
                  <IndexChip index={st.index_tomorrow} />
                </button>
              </li>
            );
          })}
        </ol>
        {ranked.length > 10 && (
          <button onClick={() => setShowAll(!showAll)} className="mt-1 min-h-11 px-1 py-2 text-sm font-semibold text-accent">
            {showAll ? "Show top 10" : `Show all ${ranked.length} sites`}
          </button>
        )}
        <p className="mt-2 text-[11px] text-muted">
          0-10 = how dangerous tomorrow looks, using only what&apos;s known by 6 PM. Data: EIA-860M, NOAA/NWS, GOES-19, Open-Meteo, NASA FIRMS, iNaturalist.
        </p>
      </div>
    </div>
  );
}
