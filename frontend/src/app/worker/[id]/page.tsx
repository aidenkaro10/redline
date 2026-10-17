"use client";

/**
 * Worker phone app. The basics on one screen: big status color, what to do,
 * the current hazard, the next break, and SOS. "Details" opens the rest:
 * core temp chart, thermometer spot check, report a hazard.
 * Made for a phone; on a laptop it shows inside a phone-sized frame.
 *
 * This page connects as view=worker for this worker only, so it gets this
 * person's own numbers and nobody else's.
 */

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { CoreChart } from "@/components/Charts";
import { LearningCard } from "@/components/LearningCard";
import { ReportForm } from "@/components/ReportForm";
import { WhyLine } from "@/components/WhyLine";
import { HazardIcon, fmtCountdown } from "@/components/ui";
import type { Hazard, Status } from "@/lib/contract";
import { airLine, airNote } from "@/lib/format";
import { HAZARD_LABEL } from "@/lib/contract";
import { callForIncident } from "@/lib/emergency";
import { apiPost, useLive, useSimNow } from "@/lib/live";
import { createHold } from "@/lib/hold";
import { secondsUntil } from "@/lib/normalize";
import { shortSiteName, useSites } from "@/lib/crew";

const LOOK: Record<Status, { bg: string; fg: string; title: string; tip: string }> = {
  green: { bg: "#0b7a0b", fg: "#ffffff", title: "You're OK", tip: "Keep drinking water. Breaks as planned." },
  yellow: { bg: "#fab219", fg: "#1f1400", title: "Slow down", tip: "Take a break in the shade and drink water." },
  red: { bg: "#c62828", fg: "#ffffff", title: "Stop. Cool down now.", tip: "Get to shade, drink water, tell your crew lead." },
};

const STOP_LOOK = { bg: "#4b3fb0", fg: "#ffffff", title: "Stop work. Shelter now.", tip: "Lightning within 10 miles. Wait for the all clear." };

export default function WorkerPhonePage() {
  return <Suspense><WorkerPhone /></Suspense>;
}

function WorkerPhone() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const live = useLive();
  const now = useSimNow(500);
  const [showDetails, setShowDetails] = useState(search.get("details") === "1");   // ?details=1 opens it
  const w = live.workers[id];
  const { byId } = useSites();
  const site = w ? byId[w.site_id] : null;
  const siteStatus = w ? live.sites[w.site_id] : null;
  const md = live.mandown;
  const meDown = md && md.worker_id === id && (md.stage === "checking" || md.stage === "confirmed");
  const call = md ? callForIncident(live.emergency, md) : null;   // the emergency call for this incident, if any
  const crewDown = md && md.worker_id !== id && md.stage === "confirmed";
  const est = live.history[id]?.estimates ?? [];

  // Most urgent site hazard right now: stop-work first, then the highest level.
  const hazard: Hazard | undefined = siteStatus?.hazards
    .filter((h) => h.level !== "none" && (h.stop_work || h.band_pattern || h.level === "high" || h.level === "extreme"))
    .sort((a, b) => Number(b.stop_work) - Number(a.stop_work))[0];

  const press = () => apiPost(`/workers/${id}/button`);

  // The worker is OK after all: stop the emergency call if it can still be stopped, then close the incident.
  const imOk = async () => {
    if (!md) return;
    if (call && (call.stage === "notifying" || call.stage === "cancel_window")) await apiPost(`/emergency/${call.id}/cancel`);
    await apiPost(`/mandown/${md.id}/resolve`);
  };

  if (!w) {
    return <Frame bg="var(--surface)" fg="var(--ink)"><p className="m-auto text-sm opacity-70">{live.ready ? `No worker "${id}"` : "Connecting…"}</p></Frame>;
  }

  // This worker pressed SOS (band button held 3 s, or the phone's SOS button).
  if (meDown && md.reason === "sos") {
    return (
      <Frame bg="#4c1d95" fg="#fff" pulse="sos">
        <div className="flex flex-1 flex-col items-center justify-center gap-6 text-center">
          <p className="text-sm font-bold uppercase tracking-[0.25em]">SOS sent</p>
          <h1 className="text-5xl font-black leading-tight">Help is coming</h1>
          <p className="text-lg">Your crew lead and crewmates have your location.<br />Stay where you are if you can.</p>
          {call?.stage === "failed" ? (
            <p className="rounded-xl bg-red-700 px-4 py-3 text-2xl font-black">Automatic call failed. Call 911 now.</p>
          ) : call && (
            <p className="rounded-xl bg-black/30 px-4 py-2 text-sm font-semibold">
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
    return (
      <Frame bg="#7f1d1d" fg="#fff" pulse="red">
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
        {md.stage === "confirmed" && <OkButton onClick={imOk} calling={call?.stage === "calling" || call?.stage === "connected"} />}
      </Frame>
    );
  }

  // Lightning stop-work outranks yellow (same priority as the band: HEAT_RED > LIGHTNING > HEAT_YELLOW).
  const stopWork = w.status !== "red" && hazard?.stop_work;
  const look = stopWork ? STOP_LOOK : LOOK[w.status];
  const redUnacked = w.status === "red" && w.alert_acknowledged === false;
  const breakIn = w.break_at ? w.break_at - now : null;
  const hasBreaks = w.on_break || w.break_at !== null;    // no planner schedule yet = no break tile

  return (
    <Frame bg={look.bg} fg={look.fg} pulse={redUnacked ? "red" : undefined}>
      <div className="flex items-center justify-between text-sm font-semibold opacity-90">
        <span>{w.name}</span>
        <span>{site ? shortSiteName(site.name) : w.site_id}</span>
      </div>

      {crewDown && (
        <div className="mt-3 rounded-2xl bg-black/40 p-3 text-center font-bold">
          Crewmate down: {md.worker_name}. Go to {md.site_name} if you&apos;re close.
        </div>
      )}

      {/* The basics: status, what to do, and the two things that matter next */}
      <div className="flex flex-1 flex-col items-center justify-center gap-3 py-8 text-center">
        <h1 className="text-5xl font-black leading-[1.05] tracking-tight">{look.title}</h1>
        <p className="text-lg font-medium">
          {w.minutes_to_danger === null ? "No danger predicted in the next 30 min."
            : w.minutes_to_danger === 0 ? "You're in the danger zone."
              : `Danger predicted in ~${w.minutes_to_danger} min.`}
        </p>
        <p className="text-base opacity-90">{look.tip}</p>
        {redUnacked && (
          <button onClick={press} className="mt-2 rounded-2xl bg-white px-8 py-4 text-xl font-black text-red-800 shadow-xl active:scale-95">Got it</button>
        )}
      </div>

      <div className={`grid gap-2 ${hasBreaks ? "grid-cols-2" : "grid-cols-1"}`}>
        <Tile label={hazard?.stop_work ? "Stop work" : "Hazard"}>
          {hazard ? (
            <>
              <span className="flex items-center gap-1.5"><HazardIcon kind={hazard.kind} className="h-5 w-5 shrink-0" />{HAZARD_LABEL[hazard.kind]}</span>
              {hazard.lightning?.all_clear_at && <span className="block text-sm font-semibold tabular opacity-90">clear in {fmtCountdown(secondsUntil(hazard.lightning.all_clear_at, now))}</span>}
            </>
          ) : "None"}
        </Tile>
        {hasBreaks && <Tile label={w.on_break ? "On break" : "Next break"}>
          <span className="tabular">
            {breakIn === null ? "--" : w.on_break ? `${Math.max(1, Math.ceil(breakIn / 60))} min left` : `in ${Math.max(0, Math.round(breakIn / 60))} min`}
          </span>
        </Tile>}
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button onClick={() => setShowDetails(!showDetails)} className="flex-1 rounded-xl bg-black/25 px-3 py-3 text-sm font-bold" aria-expanded={showDetails}>
          {showDetails ? "Hide details" : "Details"}
        </button>
        <SosButton onFire={() => apiPost(`/workers/${id}/sos`)} />
      </div>

      {/* More, for anyone who wants it */}
      {showDetails && (
        <div className="mt-3 flex flex-col gap-3">
          {hazard && (
            <Section title={HAZARD_LABEL[hazard.kind]}>
              <p className="font-semibold">{hazard.headline}</p>
              {hazard.detail && <p className="text-sm opacity-90">{hazard.detail}</p>}
            </Section>
          )}
          <Section title="My core temperature">
            <div className="rounded-xl bg-surface p-1 text-ink">
              <CoreChart estimates={est} now={now} height={190} />
            </div>
            <WhyLine why={est[est.length - 1]?.why} tone="glass" />
          </Section>
          <LearningCard workerId={id} name={w.name} offset={live.offsets[id]} tone="dark-glass" />
          {live.air[id]?.air_temp_c != null && (
            <Section title="Air at my band">
              <p className="font-semibold tabular">{airLine(live.air[id])}</p>
              <p className="text-sm opacity-90">{airNote(live.air[id])}</p>
            </Section>
          )}
          <Section title="Report a hazard">
            <ReportForm workerId={id} siteId={w.site_id} />
          </Section>
        </div>
      )}
    </Frame>
  );
}

/** "I'm OK" on the worker's own emergency screen, so they're never stuck there. */
function OkButton({ onClick, calling }: { onClick: () => void; calling: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1.5 pb-2">
      <button onClick={onClick} className="w-full rounded-2xl bg-white/90 px-6 py-3 text-lg font-bold text-black active:scale-95">
        I&apos;m OK, close this
      </button>
      <p className="text-center text-xs opacity-80">
        {calling ? "The emergency call already went out. Tell your crew lead you're OK." : "Stops the alert for your crew lead too."}
      </p>
    </div>
  );
}

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl bg-black/25 p-3">
      <p className="text-[11px] font-bold uppercase tracking-widest opacity-80">{label}</p>
      <div className="mt-0.5 text-lg font-bold leading-tight">{children}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl bg-black/25 p-4">
      <p className="mb-2 text-xs font-bold uppercase tracking-widest opacity-80">{title}</p>
      {children}
    </div>
  );
}

/**
 * SOS on the phone, same as holding the band button: hold for 3 seconds.
 * A ring fills while you hold; letting go early cancels.
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
  return (
    <button
      onPointerDown={(e) => {
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* keep going without capture */ }
        setHint(false);
        hold.current.start();
        step();
      }}
      onPointerUp={stop} onPointerCancel={stop} onContextMenu={(e) => e.preventDefault()}
      className="relative flex min-h-11 select-none items-center gap-2 rounded-xl bg-[#6d28d9] px-3 py-2 text-white touch-none"
      aria-label="Hold 3 seconds to send SOS"
    >
      <svg viewBox="0 0 36 36" className="h-7 w-7 -rotate-90" aria-hidden>
        <circle cx="18" cy="18" r="15" fill="none" stroke="rgba(255,255,255,.3)" strokeWidth="4" />
        <circle cx="18" cy="18" r="15" fill="none" stroke="#fff" strokeWidth="4" strokeDasharray={`${progress * 94.2} 94.2`} />
      </svg>
      <span className="text-left leading-tight"><b className="block">SOS</b><span className="text-[11px] font-medium opacity-90">{progress > 0 ? "keep holding…" : hint ? "hold for 3 seconds" : "hold 3 s"}</span></span>
    </button>
  );
}

/** Full screen on a phone; a phone-sized card on a laptop. */
function Frame({ bg, fg, pulse, children }: { bg: string; fg: string; pulse?: "red" | "sos"; children: React.ReactNode }) {
  return (
    <main className="min-h-screen bg-page sm:grid sm:place-items-center sm:py-6">
      <div className={`flex min-h-screen w-full flex-col p-5 transition-colors duration-500 sm:min-h-[780px] sm:max-w-[400px] sm:rounded-[2.5rem] sm:border-[10px] sm:border-black sm:shadow-2xl ${pulse ? `pulse-${pulse}` : ""}`}
        style={{ background: pulse ? undefined : bg, color: fg }}>
        {children}
      </div>
    </main>
  );
}
