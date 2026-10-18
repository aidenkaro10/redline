"use client";

/**
 * Worker phone app, laid out like Apple Health's Summary screen.
 *
 * Top: a soft color wash (mint when OK, amber, red, indigo for lightning) behind
 * one big answer: "You're OK", "Slow down", "Stop. Cool down now."
 * Below: three cards, each with one big number: core temp, hazard, next break.
 * Tapping a card slides up the details (chart, hazard info), so extra info only
 * shows when the worker asks for it. Thermometer check and hazard report are two
 * rows under that. SOS is a hold-3-seconds pill at the bottom.
 *
 * This page connects as view=worker for this worker only, so it gets this
 * person's own numbers and nobody else's.
 */

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { CoreChart } from "@/components/Charts";
import { LearningCard } from "@/components/LearningCard";
import { ReportForm } from "@/components/ReportForm";
import { Sheet } from "@/components/Sheet";
import { WhyLine } from "@/components/WhyLine";
import { Avatar, Card, Chev, HUE, Icon, Ring, SectionHeader, Spark, Sub, Value, Wash, type WashTone } from "@/components/health";
import { fmtClock, fmtCountdown } from "@/components/ui";
import type { Hazard, HazardKind, HazardLevel, Status } from "@/lib/contract";
import { HAZARD_LABEL } from "@/lib/contract";
import { callForIncident } from "@/lib/emergency";
import { apiPost, useLive, useSimNow } from "@/lib/live";
import { createHold } from "@/lib/hold";
import { secondsUntil } from "@/lib/normalize";
import { shortSiteName, useSites } from "@/lib/crew";

const LOOK: Record<Status, { tone: WashTone; title: string; tip: string }> = {
  green: { tone: "ok", title: "You're OK", tip: "Keep drinking water. Breaks as planned." },
  yellow: { tone: "warn", title: "Slow down", tip: "Take a break in the shade and drink water." },
  red: { tone: "danger", title: "Stop. Cool down now.", tip: "Get to shade, drink water, tell your crew lead." },
};
const STOP_LOOK = { tone: "stop" as WashTone, title: "Stop work. Shelter now.", tip: "Lightning within 10 miles. Wait for the all clear." };

const HAZARD_ICON: Record<HazardKind, () => React.ReactNode> = {
  heat: Icon.thermo, lightning: Icon.bolt, flood: Icon.drop, fire: Icon.flame, soil: Icon.layers, wildlife: Icon.leaf,
};
const LEVEL_N: Record<HazardLevel, number> = { none: 0, low: 1, moderate: 2, high: 3, extreme: 4 };
const LEVEL_WORD: Record<HazardLevel, string> = { none: "Clear", low: "Low", moderate: "Moderate", high: "High", extreme: "Extreme" };

type SheetName = "core" | "hazard" | "spot" | "report" | null;

export default function WorkerPhonePage() {
  return <Suspense><WorkerPhone /></Suspense>;
}

function WorkerPhone() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const live = useLive();
  const now = useSimNow(500);
  const [sheet, setSheet] = useState<SheetName>(search.get("details") === "1" ? "core" : null);   // ?details=1 opens core temp
  const w = live.workers[id];
  const { byId } = useSites();
  const site = w ? byId[w.site_id] : null;
  const siteStatus = w ? live.sites[w.site_id] : null;
  const md = live.mandown;
  const meDown = md && md.worker_id === id && (md.stage === "checking" || md.stage === "confirmed");
  const call = md ? callForIncident(live.emergency, md) : null;   // the emergency call for this incident, if any
  const crewDown = md && md.worker_id !== id && md.stage === "confirmed";
  const est = live.history[id]?.estimates ?? [];
  const last = est[est.length - 1];

  // Most urgent site hazard right now: stop-work first, then the highest level.
  const hazard: Hazard | undefined = siteStatus?.hazards
    .filter((h) => h.level !== "none" && (h.stop_work || h.band_pattern || h.level === "high" || h.level === "extreme"))
    .sort((a, b) => Number(b.stop_work) - Number(a.stop_work) || LEVEL_N[b.level] - LEVEL_N[a.level])[0];

  const press = () => apiPost(`/workers/${id}/button`);

  // The worker is OK after all: stop the emergency call if it can still be stopped, then close the incident.
  const imOk = async () => {
    if (!md) return;
    if (call && (call.stage === "notifying" || call.stage === "cancel_window")) await apiPost(`/emergency/${call.id}/cancel`);
    await apiPost(`/mandown/${md.id}/resolve`);
  };

  if (!w) {
    return <Frame><p className="m-auto text-sm text-muted">{live.ready ? `No worker "${id}"` : "Connecting…"}</p></Frame>;
  }

  // Emergency screens copy the Apple Watch fall screen: black, one question, very few buttons.
  // This worker pressed SOS (band button held 3 s, or the phone's SOS button).
  if (meDown && md.reason === "sos") {
    return (
      <Frame dark status="sos">
        <div className="flex flex-1 flex-col justify-center gap-4">
          <AlarmLabel color="#bf5af2">SOS sent</AlarmLabel>
          <h1 className="text-[44px] font-semibold leading-[1.04] tracking-[-0.035em]">Help is coming</h1>
          <p className="text-[17px] text-white/75">Your crew lead and crewmates have your location. Stay where you are if you can.</p>
          {call?.stage === "failed" ? (
            <p className="rounded-2xl bg-[#ff3b30] px-4 py-3 text-2xl font-bold">Automatic call failed. Call 911 now.</p>
          ) : call && (
            <p className="self-start rounded-full bg-white/12 px-3.5 py-1.5 text-sm font-medium text-white/85">
              {call.stage === "cancelled" ? "Cancelled by your crew lead" : call.stage === "done" ? "Emergency report delivered" : "Contacting your crew lead…"}
            </p>
          )}
        </div>
        <OkButton onClick={imOk} calling={call?.stage === "calling" || call?.stage === "connected"} />
      </Frame>
    );
  }

  // Man-down check on this worker's own phone.
  if (meDown) {
    const checking = md.stage === "checking";
    return (
      <Frame dark status="mandown">
        <div className="flex flex-1 flex-col justify-center gap-4">
          <AlarmLabel color="#ff453a">{checking ? "Man-down check" : "Help is coming"}</AlarmLabel>
          <h1 className="text-[44px] font-semibold leading-[1.04] tracking-[-0.035em]">Are you OK?</h1>
          {checking ? (
            <p className="text-[17px] text-white/75">
              Press the band button or tap below. Alerting your crew in <b className="tabular text-white">{fmtCountdown(md.confirm_at - now)}</b>
            </p>
          ) : (
            <p className="text-[17px] text-white/75">Your crew lead and crewmates have your location. An emergency report is being sent.</p>
          )}
        </div>
        {checking
          ? <button onClick={press} className="mb-2 w-full rounded-full bg-white py-5 text-xl font-semibold text-black active:scale-[.98]">I&apos;m OK</button>
          : <OkButton onClick={imOk} calling={call?.stage === "calling" || call?.stage === "connected"} />}
      </Frame>
    );
  }

  // Lightning stop-work outranks yellow (same priority as the band: HEAT_RED > LIGHTNING > HEAT_YELLOW).
  const stopWork = w.status !== "red" && !!hazard?.stop_work;
  const look = stopWork ? STOP_LOOK : LOOK[w.status];
  const redUnacked = w.status === "red" && w.alert_acknowledged === false;
  const breakIn = w.break_at ? w.break_at - now : null;
  const hasBreaks = w.on_break || w.break_at !== null;    // no planner schedule yet = no break card
  const danger = w.minutes_to_danger === null ? "No danger predicted in the next 30 min."
    : w.minutes_to_danger === 0 ? "You're in the danger zone."
      : `Danger predicted in about ${w.minutes_to_danger} min.`;
  // Status text uses the darker ink shades so it stays readable on the wash and on white cards.
  const statusHue = w.status === "red" ? "var(--danger-ink)" : w.status === "yellow" ? "var(--warn-ink)" : undefined;

  // Core temp sparkline: the last ~20 minutes, then the forecast every 5 minutes, dashed.
  const past = est.slice(-20).map((e) => e.core.mean);
  const future = last ? last.forecast.filter((f) => f.minutes_ahead % 5 === 0).map((f) => f.p50) : [];
  const lightningLeft = hazard?.lightning?.all_clear_at ? secondsUntil(hazard.lightning.all_clear_at, now) : null;

  return (
    <Frame status={stopWork ? "stop" : w.status}>
      <Wash tone={look.tone}>
        <div className="px-4 pt-[max(14px,env(safe-area-inset-top))]">
          {/* Back to the name list (iOS-style back button), and tap your initials to switch person */}
          <div className="flex items-center justify-between">
            <Link href={`/worker?team=${encodeURIComponent(w.team ?? "demo")}&switch=1`} aria-label="Back to the crew list"
              className="-ml-1.5 flex min-h-11 items-center gap-1 pr-2 text-[15px] font-semibold text-ink/70 active:opacity-60">
              <svg viewBox="0 0 10 16" className="h-4 w-2.5" aria-hidden><path d="M8 2L2 8l6 6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              {site ? shortSiteName(site.name) : w.site_id}
            </Link>
            <Link href={`/worker?team=${encodeURIComponent(w.team ?? "demo")}&switch=1`} aria-label={`Not ${w.name}? Switch person`} className="min-h-11 content-center">
              <Avatar name={w.name} />
            </Link>
          </div>
          <h1 className="mt-12 text-[34px] font-semibold leading-[1.08] tracking-[-0.035em] text-balance">{look.title}</h1>
          {!stopWork && <p className="mt-1.5 text-[17px] font-medium leading-snug" style={statusHue ? { color: statusHue } : undefined}>{danger}</p>}
          <p className="mt-1 text-[15px] leading-snug text-ink-2">{look.tip}</p>
          {redUnacked && (
            <button onClick={press} className="mt-4 rounded-full bg-[#ff3b30] px-7 py-3 text-[17px] font-semibold text-white active:scale-[.98]">Got it</button>
          )}
          {crewDown && (
            <div className="mt-4 flex items-center gap-3 rounded-[16px] bg-[#ff3b30] px-4 py-3 text-white">
              <span className="dot-breathe h-2.5 w-2.5 shrink-0 rounded-full bg-white" aria-hidden />
              <p className="text-[15px] font-semibold leading-snug">Crewmate down: {md.worker_name}. Go to {md.site_name} if you&apos;re close.</p>
            </div>
          )}
        </div>
      </Wash>

      <div className="relative px-4 pb-6">
        <SectionHeader title="Right now" />
        <div className="flex flex-col gap-2.5">
          <Card icon={<Icon.thermo />} label="Core Temp" color={HUE.core} meta={last ? fmtClock(last.timestamp) : undefined}
            onClick={() => setSheet("core")} viz={<Spark past={past} future={future} color={HUE.core} lo={Math.min(...past, ...future) - 0.35} hi={Math.max(...past, ...future) + 0.35} />}>
            {last ? <Value unit="°C">{last.core.mean.toFixed(1)}</Value> : <Value>--</Value>}
            <Sub tone={statusHue}>
              {w.minutes_to_danger === null ? "Steady" : w.minutes_to_danger === 0 ? "In the danger zone" : `Danger in ${w.minutes_to_danger} min`}
            </Sub>
          </Card>

          <Card icon={hazard ? HAZARD_ICON[hazard.kind]() : <Icon.shield />} label={hazard?.stop_work ? "Stop work" : "Hazard"}
            color={hazard ? HUE[hazard.kind] : HUE.ok} meta={hazard ? "Now" : undefined}
            onClick={hazard ? () => setSheet("hazard") : undefined}
            viz={hazard ? (lightningLeft !== null ? <Ring value={lightningLeft / 1800} color={HUE.lightning} /> : <LevelBars level={hazard.level} color={HUE[hazard.kind]} />) : undefined}>
            <Value>{hazard ? HAZARD_LABEL[hazard.kind] : "None"}</Value>
            <Sub>
              {lightningLeft !== null ? <>All clear in <span className="tabular">{fmtCountdown(lightningLeft)}</span></>
                : hazard ? `${LEVEL_WORD[hazard.level]} · ${hazard.headline}` : `All clear at ${site ? shortSiteName(site.name) : "your site"}`}
            </Sub>
          </Card>

          {hasBreaks && (
            <Card icon={<Icon.cup />} label={w.on_break ? "On break" : "Next break"} color={HUE.break}
              viz={breakIn !== null ? <Ring value={w.on_break ? Math.min(1, breakIn / 900) : 1 - Math.min(1, breakIn / 3600)} color={HUE.break} label={Math.max(0, Math.round(breakIn / 60))} /> : undefined}>
              {breakIn === null ? <Value>--</Value>
                : w.on_break ? <Value unit="min left">{Math.max(1, Math.ceil(breakIn / 60))}</Value>
                  : <Value pre="in" unit="min">{Math.max(0, Math.round(breakIn / 60))}</Value>}
              {w.break_at && <Sub>{w.on_break ? "Back to work at" : "At"} {fmtClock(w.break_at)}</Sub>}
            </Card>
          )}
        </div>

        <SectionHeader title="Log" />
        <div className="divide-y divide-line overflow-hidden rounded-[16px] bg-surface">
          <ListRow icon={<Icon.thermo />} color={HUE.core} onClick={() => setSheet("spot")}>Add a thermometer reading</ListRow>
          <ListRow icon={<Icon.alert />} color={HUE.heat} onClick={() => setSheet("report")}>Report a hazard</ListRow>
        </div>

        <div className="mt-6">
          <SosButton onFire={() => apiPost(`/workers/${id}/sos`)} />
        </div>
      </div>

      {/* Details, only when asked for */}
      <Sheet open={sheet === "core"} onClose={() => setSheet(null)} title="Core Temperature">
        <div className="flex flex-col gap-4 pb-2">
          <div className="rounded-[16px] bg-surface px-2 pb-3 pt-3">
            <CoreChart estimates={est} now={now} height={210} />
            <div className="px-2"><WhyLine why={last?.why} /></div>
          </div>
          <LearningCard workerId={id} name={w.name} offset={live.offsets[id]} />
        </div>
      </Sheet>
      <Sheet open={sheet === "hazard" && !!hazard} onClose={() => setSheet(null)} title={hazard ? HAZARD_LABEL[hazard.kind] : ""}>
        {hazard && (
          <div className="rounded-[16px] bg-surface p-4">
            <p className="text-[17px] font-semibold">{hazard.headline}</p>
            {hazard.detail && <p className="mt-1 text-[15px] text-ink-2">{hazard.detail}</p>}
            <p className="mt-3 text-[13px] text-muted">{hazard.source} · {fmtClock(hazard.updated_at)}</p>
          </div>
        )}
      </Sheet>
      <Sheet open={sheet === "spot"} onClose={() => setSheet(null)} title="Thermometer Reading">
        <LearningCard workerId={id} name={w.name} offset={live.offsets[id]} />
      </Sheet>
      <Sheet open={sheet === "report"} onClose={() => setSheet(null)} title="Report a Hazard">
        <div className="rounded-[16px] bg-surface p-4"><ReportForm workerId={id} siteId={w.site_id} /></div>
      </Sheet>
    </Frame>
  );
}

/** Four small bars, filled up to the hazard level (low, moderate, high, extreme). */
function LevelBars({ level, color }: { level: HazardLevel; color: string }) {
  const n = LEVEL_N[level];
  return (
    <span className="flex h-9 items-end gap-[3px]" aria-hidden>
      {[1, 2, 3, 4].map((i) => (
        <span key={i} className="w-[6px] rounded-[1.5px]"
          style={{ height: `${25 * i}%`, background: i <= n ? color : "color-mix(in srgb, currentColor 12%, transparent)" }} />
      ))}
    </span>
  );
}

/** A row with a small colored icon, like Health's lists. */
function ListRow({ icon, color, onClick, children }: { icon: React.ReactNode; color: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className="flex min-h-12 w-full items-center gap-3 px-4 py-3 text-left active:bg-surface-2">
      <span className="h-[18px] w-[18px] shrink-0" style={{ color }}>{icon}</span>
      <span className="flex-1 text-[17px]">{children}</span>
      <Chev />
    </button>
  );
}

/** Small red or purple caps label with a breathing dot, top of the emergency screens. */
function AlarmLabel({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <p className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-[0.14em]" style={{ color }}>
      <span className="dot-breathe h-2.5 w-2.5 rounded-full" style={{ background: color }} aria-hidden />
      {children}
    </p>
  );
}

/** "I'm OK" on the worker's own emergency screen, so they're never stuck there. */
function OkButton({ onClick, calling }: { onClick: () => void; calling: boolean }) {
  return (
    <div className="flex flex-col gap-2 pb-2">
      <button onClick={onClick} className="w-full rounded-full bg-white/15 py-4 text-lg font-semibold text-white active:scale-[.98]">
        I&apos;m OK, close this
      </button>
      <p className="text-center text-[13px] text-white/60">
        {calling ? "The emergency call already went out. Tell your crew lead you're OK." : "Stops the alert for your crew lead too."}
      </p>
    </div>
  );
}

/**
 * SOS on the phone, same as holding the band button: hold for 3 seconds.
 * A ring fills while you hold; letting go early cancels.
 * Purple, the same color the band blinks for SOS.
 */
function SosButton({ onFire }: { onFire: () => void }) {
  const [progress, setProgress] = useState(0);
  const [hint, setHint] = useState(false);
  const fire = useRef(onFire);
  fire.current = onFire;
  const hold = useRef(createHold(3000, () => fire.current(), () => performance.now()));
  const raf = useRef<number | null>(null);
  const step = () => {
    const p = hold.current.tick();
    setProgress(p);
    if (p < 1) raf.current = requestAnimationFrame(step);
  };
  const stop = () => {
    if (raf.current) cancelAnimationFrame(raf.current);
    const fired = hold.current.release();
    if (!fired) setHint(true);              // let go early: nothing sent, just a reminder
    setProgress(0);
  };
  useEffect(() => () => { if (raf.current) cancelAnimationFrame(raf.current); }, []);
  const holding = progress > 0;
  return (
    <button
      onPointerDown={(e) => {
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* keep going without capture */ }
        setHint(false);
        hold.current.start();
        step();
      }}
      onPointerUp={stop} onPointerCancel={stop} onContextMenu={(e) => e.preventDefault()}
      className={`flex min-h-[52px] w-full select-none items-center justify-center gap-2.5 rounded-full px-4 py-3 touch-none transition-colors ${
        holding ? "bg-[#af52de] text-white" : "bg-surface text-sos"}`}
      aria-label="Hold 3 seconds to send SOS"
    >
      <svg viewBox="0 0 36 36" className="h-6 w-6 -rotate-90" aria-hidden>
        <circle cx="18" cy="18" r="14" fill="none" stroke="currentColor" strokeOpacity=".25" strokeWidth="4" />
        <circle cx="18" cy="18" r="14" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round" strokeDasharray={`${progress * 88} 88`} />
      </svg>
      <b className="text-[17px] font-semibold">SOS</b>
      <span className="text-[15px] opacity-75">{holding ? "keep holding…" : hint ? "hold for 3 seconds" : "hold 3 s"}</span>
    </button>
  );
}

/** Full screen on a phone; a phone-sized card on a laptop. `dark` = the black emergency screens. */
function Frame({ dark = false, status, children }: { dark?: boolean; status?: string; children: React.ReactNode }) {
  return (
    <main className="font-app min-h-screen bg-page sm:grid sm:place-items-center sm:py-6">
      <div data-status={status}
        className={`relative flex min-h-screen w-full flex-col overflow-hidden sm:min-h-[820px] sm:max-w-[400px] sm:rounded-[2.75rem] sm:border-[10px] sm:border-black sm:shadow-2xl ${
          dark ? "bg-black px-5 pb-[max(20px,env(safe-area-inset-bottom))] pt-[max(16px,env(safe-area-inset-top))] text-white" : "bg-page text-ink"}`}>
        {children}
      </div>
    </main>
  );
}
