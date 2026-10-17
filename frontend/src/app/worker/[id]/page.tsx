"use client";

/**
 * Worker phone view: giant status color, the current hazard, break countdown.
 * Made for a phone; on a laptop it shows inside a phone-sized frame.
 *
 * This page connects as view=worker for this worker only, so it gets this
 * person's own numbers and nobody else's.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { CoreChart } from "@/components/Charts";
import { HazardIcon, fmtCountdown } from "@/components/ui";
import type { Hazard, Status } from "@/lib/contract";
import { apiPost, useLive, useSimNow } from "@/lib/live";
import { SITE_BY_ID } from "@/lib/sites";

const LOOK: Record<Status, { bg: string; fg: string; title: string; tip: string }> = {
  green: { bg: "#0b7a0b", fg: "#ffffff", title: "You're OK", tip: "Keep drinking water. Breaks as planned." },
  yellow: { bg: "#fab219", fg: "#1f1400", title: "Slow down", tip: "Take a break in the shade and drink water." },
  red: { bg: "#c62828", fg: "#ffffff", title: "Stop. Cool down now.", tip: "Get to shade, drink water, tell your crew lead." },
};

const STOP_LOOK = { bg: "#4b3fb0", fg: "#ffffff", title: "Stop work. Shelter now.", tip: "Lightning within 10 miles. Wait for the all clear." };

export default function WorkerPhone() {
  const { id } = useParams<{ id: string }>();
  const live = useLive();
  const now = useSimNow(500);
  const [showNumbers, setShowNumbers] = useState(false);
  const w = live.workers[id];
  const site = w ? SITE_BY_ID[w.site_id] : null;
  const siteStatus = w ? live.sites[w.site_id] : null;
  const md = live.mandown;
  const meDown = md && md.worker_id === id && (md.stage === "checking" || md.stage === "confirmed");
  const crewDown = md && md.worker_id !== id && md.stage === "confirmed";
  const est = live.history[id]?.estimates ?? [];

  // Most urgent site hazard right now: stop-work first, then the highest level.
  const hazard: Hazard | undefined = siteStatus?.hazards
    .filter((h) => h.level !== "none" && (h.stop_work || h.band_pattern || h.level === "high" || h.level === "extreme"))
    .sort((a, b) => Number(b.stop_work) - Number(a.stop_work))[0];

  const press = () => apiPost(`/workers/${id}/button`);

  if (!w) {
    return <Frame bg="var(--surface)" fg="var(--ink)"><p className="m-auto text-sm opacity-70">{live.ready ? `No worker "${id}"` : "Connecting…"}</p></Frame>;
  }

  // Man-down check on this worker's own phone.
  if (meDown) {
    return (
      <Frame bg="#7f1d1d" fg="#fff" pulse>
        <div className="flex flex-1 flex-col items-center justify-center gap-6 text-center">
          <p className="text-sm font-bold uppercase tracking-[0.25em]">{md.stage === "checking" ? "Man-down check" : "Help is coming"}</p>
          <h1 className="text-5xl font-black leading-tight">Are you OK?</h1>
          {md.stage === "checking" ? (
            <>
              <p className="text-lg">Press the band button or tap below.<br />Alerting your crew in <b className="tabular">{fmtCountdown(md.confirm_at - now)}</b></p>
              <button onClick={press} className="h-40 w-40 rounded-full bg-white text-2xl font-black text-red-800 shadow-2xl active:scale-95">I&apos;M OK</button>
            </>
          ) : (
            <p className="text-lg">Your crew lead and crewmates have your location.<br />An emergency report is being sent.</p>
          )}
        </div>
      </Frame>
    );
  }

  // Lightning stop-work outranks yellow (same priority as the band: HEAT_RED > LIGHTNING > HEAT_YELLOW).
  const stopWork = w.status !== "red" && hazard?.stop_work;
  const look = stopWork ? STOP_LOOK : LOOK[w.status];
  const redUnacked = w.status === "red" && w.alert_acknowledged === false;
  const breakIn = w.break_at ? w.break_at - now : null;

  return (
    <Frame bg={look.bg} fg={look.fg} pulse={redUnacked}>
      <div className="flex items-center justify-between text-sm font-semibold opacity-90">
        <span>{w.name}</span>
        <span>{site?.name.replace(" Generating Station", "")}</span>
      </div>

      {crewDown && (
        <div className="mt-3 rounded-2xl bg-black/40 p-3 text-center font-bold">
          Crewmate down: {md.worker_name}. Go to {md.site_name} if you&apos;re close.
        </div>
      )}

      {/* Giant status */}
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-6 text-center">
        <p className="text-sm font-bold uppercase tracking-[0.25em] opacity-90">{stopWork ? `Lightning · heat ${w.status === "green" ? "OK" : "warning"}` : w.status === "green" ? "Status: OK" : w.status === "yellow" ? "Status: Warning" : "Status: Danger"}</p>
        <h1 className="text-5xl font-black leading-[1.05] tracking-tight sm:text-6xl">{look.title}</h1>
        <p className="text-lg font-medium">
          {w.minutes_to_danger === null ? "No danger predicted in the next 30 min."
            : w.minutes_to_danger === 0 ? "You're in the danger zone."
              : `Danger predicted in ~${w.minutes_to_danger} min.`}
        </p>
        <p className="text-base opacity-90">{look.tip}</p>
        {redUnacked && (
          <button onClick={press} className="mt-2 rounded-2xl bg-white px-8 py-4 text-xl font-black text-red-800 shadow-xl active:scale-95">Got it</button>
        )}
        {w.status === "red" && w.alert_acknowledged && <p className="text-sm font-semibold opacity-90">✓ Alert acknowledged</p>}
      </div>

      {/* Current hazard */}
      <div className="rounded-2xl bg-black/25 p-4">
        <p className="text-xs font-bold uppercase tracking-widest opacity-80">Current hazard</p>
        {hazard ? (
          <div className="mt-1 flex items-center gap-3">
            <HazardIcon kind={hazard.kind} className="h-9 w-9 shrink-0" />
            <div className="flex-1">
              <p className="text-lg font-bold leading-tight">{hazard.stop_work ? "STOP WORK. " : ""}{hazard.headline}</p>
              <p className="text-sm opacity-90">{hazard.detail}</p>
            </div>
            {hazard.lightning?.all_clear_at && (
              <div className="text-right">
                <p className="text-[10px] font-bold uppercase opacity-80">All clear in</p>
                <p className="text-2xl font-black tabular">{fmtCountdown(hazard.lightning.all_clear_at - now)}</p>
              </div>
            )}
          </div>
        ) : <p className="mt-1 text-lg font-semibold">None right now</p>}
      </div>

      {/* Break countdown */}
      <div className="mt-3 rounded-2xl bg-black/25 p-4 text-center">
        <p className="text-xs font-bold uppercase tracking-widest opacity-80">{w.on_break ? "On break" : "Next break"}</p>
        <p className="text-3xl font-black tabular">
          {breakIn === null ? "--" : w.on_break ? `Back to work in ${Math.max(1, Math.ceil(breakIn / 60))} min` : `in ${Math.max(0, Math.round(breakIn / 60))} min`}
        </p>
      </div>

      <button onClick={() => setShowNumbers(!showNumbers)} className="mt-3 text-sm font-semibold underline opacity-90">
        {showNumbers ? "Hide my numbers" : "Show my numbers"}
      </button>
      {showNumbers && (
        <div className="mt-2 rounded-2xl bg-surface p-2 text-ink">
          <CoreChart estimates={est} now={now} height={200} />
          <p className="px-2 pb-1 text-xs text-ink-2">{est[est.length - 1]?.why}</p>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-sm font-semibold">
        <Link href={`/report?worker=${id}`} className="rounded-xl bg-black/25 px-3 py-2">Report a hazard</Link>
        <Link href="/" className="opacity-80 underline">Crew dashboard</Link>
      </div>
    </Frame>
  );
}

/** Full screen on a phone; a phone-sized card on a laptop. */
function Frame({ bg, fg, pulse, children }: { bg: string; fg: string; pulse?: boolean; children: React.ReactNode }) {
  return (
    <main className="min-h-screen bg-page sm:grid sm:place-items-center sm:py-6">
      <div className={`flex min-h-screen w-full flex-col p-5 transition-colors duration-500 sm:min-h-[780px] sm:max-w-[400px] sm:rounded-[2.5rem] sm:border-[10px] sm:border-black sm:shadow-2xl ${pulse ? "pulse-red" : ""}`}
        style={{ background: pulse ? undefined : bg, color: fg }}>
        {children}
      </div>
    </main>
  );
}
