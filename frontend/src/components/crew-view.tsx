"use client";

/**
 * The crew lead's two views, shared by the phone app (/) and the laptop manager view (/manager).
 *   CrewPanel  - crew summary card, full cards for people who need attention, everyone else in
 *                one quiet list. Tap someone for the details sheet.
 *   SitesPanel - job sites ranked by tomorrow's 0-10 risk index. Phone: List / Map switch.
 *                Laptop: map on top, list under it.
 *
 * Privacy: these screens connect as view=crew, so they never receive raw heart
 * rate or core temp. Status, minutes to danger and hazards only.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { HazardPanel } from "@/components/HazardPanel";
import { useIsDark } from "@/components/Nav";
import { Group, Row, Sheet } from "@/components/Sheet";
import { SiteMap } from "@/components/SiteMap";
import { Card, Chev, HUE, Icon, Ring, SectionHeader, Value } from "@/components/health";
import { ALERT_LABEL, HAZARD_LABEL } from "@/lib/contract";
import type { HazardKind, SiteStatus, Status, WorkerStatus } from "@/lib/contract";
import { STATUS_TEXT, StatusBadge, alertTone, fmtClock, fmtCountdown, indexColor } from "@/components/ui";
import { shortSiteName, siteMeta, useCrew, useSites } from "@/lib/crew";
import { dangerText } from "@/lib/format";
import { rankSites } from "@/lib/ranking";
import { useLive, useSimNow } from "@/lib/live";
import { secondsUntil } from "@/lib/normalize";

const STATUS_ORDER: Record<Status, number> = { red: 0, yellow: 1, green: 2 };
const STATUS_HUE: Record<Status, string> = { green: HUE.ok, yellow: HUE.warn, red: HUE.danger };
const DANGER_CLASS: Record<Status, string> = { green: "text-ink-2", yellow: "text-warn-ink", red: "text-danger-ink" };
const HAZARD_ICON: Record<HazardKind, () => React.ReactNode> = {
  heat: Icon.thermo, lightning: Icon.bolt, flood: Icon.drop, fire: Icon.flame, soil: Icon.layers, wildlife: Icon.leaf,
};


// ---------------------------------------------------------------------------
// Crew tab
// ---------------------------------------------------------------------------

export function CrewPanel() {
  const live = useLive();
  const crew = useCrew();
  const { byId } = useSites();
  const now = useSimNow(1000);
  const [open, setOpen] = useState<string | null>(null);

  const workers = useMemo(
    () => [...crew.workers].sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.name.localeCompare(b.name)),
    [crew.workers],
  );
  const downOf = (id: string) => live.mandown?.worker_id === id && (live.mandown.stage === "checking" || live.mandown.stage === "confirmed")
    ? (live.mandown.reason === "sos" ? "sos" : "mandown") : null;

  if (!live.ready) return <p className="py-10 text-center text-sm text-muted">Connecting…</p>;
  const openWorker = workers.find((w) => w.worker_id === open);
  const urgent = workers.filter((w) => w.status !== "green" || downOf(w.worker_id) || (w.minutes_to_danger !== null && Number.isFinite(w.minutes_to_danger)));
  const fine = workers.filter((w) => !urgent.includes(w));
  const site = byId[crew.siteIds[0]];

  return (
    <div>
      <SiteAlerts siteIds={crew.siteIds} sites={live.sites} now={now} />

      <CrewSummary workers={workers} siteName={site ? shortSiteName(site.name) : ""} now={now} />

      {/* Full cards only for people who need attention; everyone who's fine sits in one quiet list */}
      {urgent.length > 0 && (
        <>
          <SectionHeader title="Needs attention" />
          <ul className="flex flex-col gap-2.5">
            {urgent.map((w) => (
              <WorkerCard key={w.worker_id} w={w} onOpen={() => setOpen(w.worker_id)} down={downOf(w.worker_id)} />
            ))}
          </ul>
        </>
      )}
      {fine.length > 0 && (
        <>
          <SectionHeader title={urgent.length ? "Everyone else" : "Workers"} />
          <ul className="divide-y divide-line overflow-hidden rounded-[16px] bg-surface">
            {fine.map((w) => <OkRow key={w.worker_id} w={w} onOpen={() => setOpen(w.worker_id)} />)}
          </ul>
        </>
      )}

      <Sheet open={!!openWorker} onClose={() => setOpen(null)} title={openWorker?.name}>
        {openWorker && <WorkerDetails w={openWorker} now={now} down={downOf(openWorker.worker_id)} />}
      </Sheet>
    </div>
  );
}

/** Only shows when a crew site has a real alert (lightning stop, flood or fire warning). */
function SiteAlerts({ siteIds, sites, now }: { siteIds: string[]; sites: Record<string, SiteStatus>; now: number }) {
  const { byId } = useSites();
  const names = Object.fromEntries(Object.values(byId).map((s) => [s.id, s.name]));
  const alerts = siteIds.flatMap((id) => (sites[id]?.hazards ?? [])
    .filter((h) => h.stop_work || h.band_pattern !== null)    // real alerts only (the band buzzes for them)
    .map((h) => ({ id, h })));
  if (!alerts.length) return null;
  return (
    <div className="pt-4">
      <div className="flex flex-col gap-2.5">
        {alerts.map(({ id, h }) => {
          const left = h.lightning?.all_clear_at ? secondsUntil(h.lightning.all_clear_at, now) : null;
          return (
            <Card key={id + h.kind} icon={HAZARD_ICON[h.kind]()} color={HUE[h.kind]} meta="Now" tintLabel
              label={`${h.stop_work ? "Stop work" : HAZARD_LABEL[h.kind]} · ${shortSiteName(names[id]) || id}`}
              viz={left !== null ? <Ring value={left / 1800} color={HUE[h.kind]} /> : undefined}>
              {left !== null ? <Value unit="until all clear">{fmtCountdown(left)}</Value> : <Value>{HAZARD_LABEL[h.kind]}</Value>}
              <span className="mt-1.5 block truncate text-[15px] text-muted">{h.headline}</span>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

/** Top card: how many are OK, one small bar per worker in their status color (Health's steps bars). */
function CrewSummary({ workers, siteName, now }: { workers: WorkerStatus[]; siteName: string; now: number }) {
  const attention = workers.filter((w) => w.status !== "green").length;
  return (
    <div className="pt-4">
      <Card icon={<Icon.people />} label="Crew" color={HUE.crew} meta={fmtClock(now)}
        viz={<span className="flex h-9 items-end gap-[4px]" aria-hidden>
          {workers.map((w) => (
            <span key={w.worker_id} className="w-[7px] rounded-[2px]" style={{ height: w.status === "red" ? "100%" : w.status === "yellow" ? "70%" : "42%", background: STATUS_HUE[w.status] }} />
          ))}
        </span>}>
        {attention
          ? <Value unit={attention === 1 ? "needs attention" : "need attention"} tone={workers.some((w) => w.status === "red") ? "var(--danger-ink)" : "var(--warn-ink)"}>{attention}</Value>
          : <Value unit="all OK">{workers.length}</Value>}
        <span className="mt-1.5 block truncate text-[15px] text-muted">{workers.length} on shift{siteName ? ` at ${siteName}` : ""}</span>
      </Card>
    </div>
  );
}

/** Someone who's fine: one line, name and what they're doing, "OK" on the right. */
function OkRow({ w, onOpen }: { w: WorkerStatus; onOpen: () => void }) {
  const reason = w.on_break ? (w.hazard === "LIGHTNING" ? "Stopped for lightning" : "On break") : "Working";
  return (
    <li data-status={w.status}>
      <button onClick={onOpen} className="flex min-h-[56px] w-full items-center gap-3 px-4 py-2.5 text-left active:bg-surface-2">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[17px] font-semibold">{w.name}</span>
          <span className="block truncate text-[15px] text-muted">{reason}</span>
        </span>
        <span className="flex items-center gap-1 text-[15px] font-semibold" style={{ color: HUE.ok }}>
          <span className="h-4 w-4"><Icon.check /></span>
          <span>OK</span>
        </span>
        <Chev />
      </button>
    </li>
  );
}

/** One worker as a Health card: status label on top, name big, danger countdown, and a ring that empties toward danger. */
function WorkerCard({ w, onOpen, down }: { w: WorkerStatus; onOpen: () => void; down: "mandown" | "sos" | null }) {
  const hue = down === "sos" ? HUE.sos : down ? HUE.danger : STATUS_HUE[w.status];
  const mins = w.minutes_to_danger;
  const hasCountdown = mins !== null && Number.isFinite(mins);
  const reason = w.on_break ? (w.hazard === "LIGHTNING" ? "Stopped for lightning" : "On break") : w.status_reason;
  const tone = mins === 0 || w.status === "red" ? "text-danger-ink" : DANGER_CLASS[w.status];
  return (
    <li data-status={w.status} data-down={down ?? undefined}>
      <Card onClick={onOpen} color={hue} tintLabel
        icon={down ? <span className="dot-breathe block h-full w-full"><Icon.alert /></span> : w.status === "green" ? <Icon.check /> : <Icon.alert />}
        label={down === "sos" ? "SOS" : down ? "Man down" : STATUS_TEXT[w.status]}
        meta={w.hazard ? <span className={`h-4 w-4 ${alertTone(w.hazard)}`} title={ALERT_LABEL[w.hazard]}>{w.hazard === "LIGHTNING" ? <Icon.bolt /> : <Icon.thermo />}</span> : undefined}
        viz={<Ring color={hue} value={hasCountdown ? Math.min(1, mins! / 30) : 1}
          label={hasCountdown ? (mins === 0 ? "!" : mins) : <span className="block h-3.5 w-3.5" style={{ color: hue }}><Icon.check /></span>} />}>
        <span className="block truncate text-[22px] font-semibold leading-tight tracking-[-0.025em]">{w.name}</span>
        {hasCountdown
          ? <span data-testid="danger" className={`mt-1 block text-[15px] font-semibold tabular ${tone}`}>Danger {dangerText(mins)}</span>
          : <span className="mt-1 block truncate text-[15px] text-muted">{reason}</span>}
      </Card>
    </li>
  );
}

/** The sheet for one worker (Find My style). Status only, no raw biometrics. */
function WorkerDetails({ w, now, down }: { w: WorkerStatus; now: number; down: "mandown" | "sos" | null }) {
  const { byId } = useSites();
  const site = byId[w.site_id];
  const breakIn = w.break_at ? w.break_at - now : null;
  const tone = w.minutes_to_danger === 0 || w.status === "red" ? "text-danger-ink" : w.status === "yellow" ? "text-warn-ink" : "text-ink";
  return (
    <div className="flex flex-col gap-5 pb-2">
      <div className="px-1">
        <div className="flex items-center gap-2">
          {down
            ? <span className={`${down === "sos" ? "pulse-sos" : "pulse-red"} rounded-full px-2.5 py-0.5 text-xs font-bold text-white`}>{down === "sos" ? "SOS" : "Man down"}</span>
            : <StatusBadge status={w.status} big />}
          <span className="text-[15px] text-muted">{w.role} · {site ? shortSiteName(site.name) : w.site_id}</span>
        </div>
        <p className={`mt-3 text-[30px] font-semibold leading-tight tracking-[-0.03em] tabular ${tone}`}>
          {w.minutes_to_danger === null ? "No danger in 30 min" : w.minutes_to_danger === 0 ? "In danger now" : `Danger in ${w.minutes_to_danger} min`}
        </p>
        <p className="mt-1 text-[17px] text-ink-2">{w.status_reason}</p>
      </div>

      <Group>
        <Row label="Alert"><span className={w.hazard ? alertTone(w.hazard) : "font-normal text-muted"}>{w.hazard ? ALERT_LABEL[w.hazard] : "None"}</span></Row>
        {w.alert_acknowledged !== null && <Row label="Acknowledged">{w.alert_acknowledged ? "Yes" : <span className="text-danger-ink">Not yet</span>}</Row>}
        {breakIn !== null && <Row label={w.on_break ? "Back to work" : "Next break"}><span className="tabular">in {Math.max(0, Math.round(breakIn / 60))} min</span></Row>}
        <Row label="Band">{w.band_connected ? "Connected" : <span className="text-warn-ink">Not connected</span>}</Row>
      </Group>

      <Group>
        <Link href={`/worker/${w.worker_id}`} className="flex min-h-12 items-center justify-between px-4 py-3 text-[17px] text-accent">
          Open their phone view <Chev />
        </Link>
      </Group>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sites tab: sites ranked by tomorrow's 0-10 index (covers the 6 PM briefing). Map on request.
// ---------------------------------------------------------------------------

export function SitesPanel({ layout = "phone" }: { layout?: "phone" | "laptop" }) {
  const live = useLive();
  const crew = useCrew();
  const dark = useIsDark();
  const { sites } = useSites();
  const [view, setView] = useState<"list" | "map">("list");
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  // Ranked by tomorrow's index, highest first; no index yet = last. Worker alerts show as flags.
  const ranked = useMemo(() => (sites ? rankSites(sites, live.sites) : []), [sites, live.sites]);

  // A stop-work or incident at a crew site: open that site right away.
  const alarm = crew.siteIds.find((id) => live.sites[id]?.worker_flag === "mandown" || live.sites[id]?.worker_flag === "sos" || live.sites[id]?.hazards.some((h) => h.stop_work)) ?? null;
  useEffect(() => { if (alarm) { setSelected(alarm); setFocus(alarm); } }, [alarm]);

  const pick = (id: string) => { setSelected(id); setFocus(id); };
  const sel = sites?.find((s) => s.id === selected);
  const selStatus = selected ? live.sites[selected] : undefined;

  const laptop = layout === "laptop";
  return (
    <div className="pt-4">
      {/* Phone: List / Map, iOS segmented control. Laptop shows both. */}
      {!laptop && <div className="grid grid-cols-2 rounded-[10px] bg-black/[0.06] p-0.5 text-[13px] font-semibold dark:bg-white/10" role="tablist">
        {(["list", "map"] as const).map((v) => (
          <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
            className={`min-h-8 rounded-[8px] py-1.5 capitalize ${view === v ? "bg-surface text-ink shadow-[0_1px_3px_rgba(0,0,0,0.12)]" : "text-ink-2"}`}>
            {v}
          </button>
        ))}
      </div>}

      {(laptop || view === "map") && (
        <div className={`overflow-hidden rounded-[16px] ${laptop ? "h-[42vh] min-h-[300px]" : "mt-4 h-[62vh] min-h-[320px]"}`}>
          {sites && <SiteMap sites={sites} statuses={live.sites} reports={live.reports} selected={selected} onSelect={pick} dark={dark} focus={focus} />}
        </div>
      )}
      {(laptop || view === "list") && (
        <div>
          <SectionHeader title="Riskiest tomorrow" />
          <ol className="divide-y divide-line overflow-hidden rounded-[16px] bg-surface">
            {(showAll ? ranked : ranked.slice(0, 10)).map((s, i) => {
              const st = live.sites[s.id];
              const stop = st.hazards.some((h) => h.stop_work);
              const mine = crew.siteIds.includes(s.id);
              const idx = st.index_tomorrow;
              const has = idx !== null && Number.isFinite(idx);
              return (
                <li key={s.id}>
                  <button onClick={() => pick(s.id)} className="flex min-h-[64px] w-full items-center gap-3 px-4 py-2.5 text-left active:bg-surface-2">
                    <span className="w-5 shrink-0 text-[15px] font-semibold text-muted tabular">{i + 1}</span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-[17px] font-semibold">{s.name}</span>
                        {mine && <span className="shrink-0 h-3.5 w-3.5 text-accent" title="Your crew"><Icon.people /></span>}
                      </span>
                      <span className="block truncate text-[15px]">
                        {st.worker_flag === "mandown" ? <b className="font-semibold text-danger-ink">Man down</b>
                          : st.worker_flag === "sos" ? <b className="font-semibold text-sos">SOS</b>
                            : st.worker_flag === "heat" ? <b className="font-semibold text-danger-ink">Worker heat alert</b>
                              : stop ? <b className="font-semibold text-lightning">Stop work</b>
                                : <span className="text-muted">{st.hazard_index?.top_reasons[0]?.text ?? siteMeta(s, false)}</span>}
                      </span>
                    </span>
                    <Ring size={42} value={has ? idx! / 10 : 0} color={indexColor(has ? idx : null)} label={has ? idx!.toFixed(1) : "--"} />
                  </button>
                </li>
              );
            })}
          </ol>
          {ranked.length > 10 && (
            <button onClick={() => setShowAll(!showAll)} className="mt-1 min-h-11 px-1 py-2 text-[17px] text-accent">
              {showAll ? "Show top 10" : `Show all ${ranked.length} sites`}
            </button>
          )}
          <p className="mt-3 px-1 text-[13px] leading-snug text-muted">
            The ring is tomorrow&apos;s 0-10 risk, using only what&apos;s known by 6 PM. Data: EIA-860M, NOAA/NWS, GOES-19, Open-Meteo, NASA FIRMS, iNaturalist.
          </p>
        </div>
      )}

      <Sheet open={!!(sel && selStatus)} onClose={() => setSelected(null)} title={sel ? shortSiteName(sel.name) : undefined}>
        {sel && selStatus && <div className="rounded-[16px] bg-surface p-4"><HazardPanel site={sel} status={selStatus} /></div>}
      </Sheet>
    </div>
  );
}
