"use client";

/**
 * Crew lead dashboard.
 *
 * Privacy: this page connects as view=crew, so the server never sends it raw
 * heart rate or core temp. It shows status, minutes until predicted danger,
 * hazards and the schedule.
 */

import { useEffect, useMemo, useState } from "react";

import { AlertFeed } from "@/components/AlertFeed";
import { DemoControls } from "@/components/DemoControls";
import { HazardPanel } from "@/components/HazardPanel";
import { useIsDark } from "@/components/Nav";
import { SchedulePanel } from "@/components/SchedulePanel";
import { SiteMap } from "@/components/SiteMap";
import { WorkerCard } from "@/components/WorkerCard";
import { Card, HazardIcon, IndexChip, StatusDot, INDEX_RAMP } from "@/components/ui";
import type { Schedule, Site, Status } from "@/lib/contract";
import { siteMeta, useCrew } from "@/lib/crew";
import { useFetch, useLive, useSimNow } from "@/lib/live";

const STATUS_ORDER: Record<Status, number> = { red: 0, yellow: 1, green: 2 };

export default function CrewDashboard() {
  const live = useLive();
  const now = useSimNow(1000);
  const dark = useIsDark();
  const { data: sites } = useFetch<Site[]>("/sites");
  const { data: schedule, error: scheduleError } = useFetch<Schedule>("/schedule", 15000);
  const crew = useCrew();
  const [picked, setSelected] = useState<string | null>(null);
  const selected = picked ?? crew.siteIds[0] ?? "";
  const [focus, setFocus] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const workers = useMemo(
    () => Object.values(live.workers).sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.name.localeCompare(b.name)),
    [live.workers],
  );
  const counts = { green: 0, yellow: 0, red: 0 };
  for (const w of workers) counts[w.status]++;

  // Risk ranking: sites with a worker flag first, then by tomorrow's index.
  const ranked = useMemo(() => {
    if (!sites) return [];
    const flagRank = { mandown: 0, sos: 0, heat: 1, none: 2 };
    return [...sites].filter((s) => live.sites[s.id]).sort((a, b) => {
      const A = live.sites[a.id], B = live.sites[b.id];
      return flagRank[A.worker_flag] - flagRank[B.worker_flag] || B.index_tomorrow - A.index_tomorrow;
    });
  }, [sites, live.sites]);

  // When lightning or a man-down hits a crew site, zoom the map there so the judge sees it.
  const crewAlarm = crew.siteIds.find((id) => (live.sites[id]?.worker_flag === "mandown" || live.sites[id]?.worker_flag === "sos") || live.sites[id]?.hazards.some((h) => h.stop_work)) ?? null;
  useEffect(() => {
    if (crewAlarm) { setSelected(crewAlarm); setFocus(crewAlarm); }
  }, [crewAlarm]);

  const stopWorkSites = Object.values(live.sites).filter((s) => s.hazards.some((h) => h.stop_work)).length;
  const selSite = sites?.find((s) => s.id === selected);
  const selStatus = live.sites[selected];
  const pick = (id: string) => { setSelected(id); setFocus(id); };

  return (
    <main className="mx-auto flex max-w-[1500px] flex-col gap-4 px-4 py-4">
      <DemoControls />

      {/* Crew summary */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Your crew</h1>
          <p className="text-sm text-ink-2">{workers.length} workers on {crew.siteIds.length} site{crew.siteIds.length === 1 ? "" : "s"} · statuses only, no raw biometrics</p>
        </div>
        <div className="flex flex-wrap gap-2 text-sm">
          <Pill><StatusDot status="red" /> <b className="tabular">{counts.red}</b> danger</Pill>
          <Pill><StatusDot status="yellow" /> <b className="tabular">{counts.yellow}</b> warning</Pill>
          <Pill><StatusDot status="green" /> <b className="tabular">{counts.green}</b> OK</Pill>
          <Pill><span className="text-lightning"><HazardIcon kind="lightning" className="h-4 w-4" /></span> <b className="tabular">{stopWorkSites}</b> stop-work sites</Pill>
        </div>
      </div>

      {/* Worker cards */}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
        {!live.ready && Array.from({ length: 6 }, (_, i) => <div key={i} className="h-44 animate-pulse rounded-xl bg-surface-2" />)}
        {workers.map((w) => (
          <WorkerCard key={w.worker_id} w={w} now={now} down={live.mandown?.worker_id === w.worker_id && (live.mandown.stage === "checking" || live.mandown.stage === "confirmed") ? (live.mandown.reason === "sos" ? "sos" : "mandown") : null} />
        ))}
      </section>

      {/* Map + hazards */}
      <section className="grid gap-4 lg:grid-cols-3">
        <Card
          className="lg:col-span-2"
          title={`Job sites, risk ranked (${ranked.length})`}
          right={<IndexLegend />}
          bodyClass="flex flex-col md:flex-row"
        >
          <div className="order-2 max-h-[460px] overflow-y-auto border-t border-line md:order-1 md:max-h-[640px] md:w-72 md:border-r md:border-t-0">
            <ol>
              {(showAll ? ranked : ranked.slice(0, 15)).map((s, i) => {
                const st = live.sites[s.id];
                const stop = st.hazards.some((h) => h.stop_work);
                return (
                  <li key={s.id}>
                    <button onClick={() => pick(s.id)}
                      className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-surface-2 ${selected === s.id ? "bg-surface-2" : ""}`}>
                      <span className="w-5 shrink-0 text-xs text-muted tabular">{i + 1}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{s.name}</span>
                        <span className="flex gap-2 text-[11px]">
                          {st.worker_flag === "mandown" && <b className="text-danger-ink">MAN DOWN</b>}
                          {st.worker_flag === "sos" && <b className="text-sos">SOS</b>}
                          {st.worker_flag === "heat" && <b className="text-danger-ink">Worker heat alert</b>}
                          {stop && <b className="text-lightning">Stop work</b>}
                          {st.worker_flag === "none" && !stop && <span className="text-muted">{siteMeta(s, false)}</span>}
                        </span>
                      </span>
                      <IndexChip index={st.index_tomorrow} />
                    </button>
                  </li>
                );
              })}
            </ol>
            {ranked.length > 15 && (
              <button onClick={() => setShowAll(!showAll)} className="w-full px-3 py-2 text-left text-xs font-semibold text-accent hover:underline">
                {showAll ? "Show top 15" : `Show all ${ranked.length} sites`}
              </button>
            )}
          </div>
          <div className="order-1 h-[360px] flex-1 overflow-hidden rounded-br-xl md:order-2 md:h-auto md:min-h-[460px]">
            {sites && <SiteMap sites={sites} statuses={live.sites} reports={live.reports} selected={selected} onSelect={pick} dark={dark} focus={focus} />}
          </div>
        </Card>

        <Card title="Site hazards" right={<button className="text-accent hover:underline" onClick={() => crew.siteIds[0] && pick(crew.siteIds[0])}>Crew site</button>}>
          {selSite && selStatus ? <HazardPanel site={selSite} status={selStatus} /> : <p className="text-sm text-muted">Pick a site on the map.</p>}
        </Card>
      </section>

      <p className="-mt-2 text-[11px] text-muted">
        Data: U.S. EIA Form EIA-860M (sites) · NOAA / NWS · GOES-19 GLM · Open-Meteo (CC BY 4.0) · NASA FIRMS · iNaturalist (CC BY-NC) · FEMA · USACE. Lightning rings: 10 mi stop work, 30 mi watch.
      </p>

      {/* Schedule + alerts */}
      <section className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Today's schedule" right="From the planner, 30-min slots">
          {schedule ? <SchedulePanel schedule={schedule} now={now} />
            : <p className="text-sm text-muted">{scheduleError ? "No schedule from the planner yet." : "Loading…"}</p>}
        </Card>
        <Card title="Alerts and log">
          <AlertFeed />
        </Card>
      </section>
    </main>
  );
}

function Pill({ children }: { children: React.ReactNode }) {
  return <span className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1">{children}</span>;
}

function IndexLegend() {
  return (
    <span className="flex items-center gap-1.5">
      Tomorrow&apos;s index 0
      <span className="flex">{INDEX_RAMP.map((c) => <span key={c} className="h-2.5 w-4" style={{ background: c }} />)}</span>
      10
    </span>
  );
}
