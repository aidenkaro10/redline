"use client";

/**
 * The man-down screen. Full-screen, on top of every page.
 *
 * Shows: worker name, location, time since the fall, last vitals, and the
 * emergency call as it happens (notify crew lead -> cancel window ->
 * "Demo: would call 911" -> connected -> done). If the emergency call fails,
 * a huge "Call 911 yourself now" box takes over, and it can't be minimized.
 *
 * Last vitals are shown to the crew lead here on purpose: in an emergency,
 * responders need them. Everywhere else the crew lead sees status only.
 */

import { usePathname } from "next/navigation";
import { useMemo, useState } from "react";

import type { EmergencyCall, EmergencyStage, ManDown } from "@/lib/contract";
import { CALL_FAILED_TEXT, callForIncident, cancelCallRequest } from "@/lib/emergency";
import { singleFlight } from "@/lib/once";
import { apiPost, useLive, useSimNow } from "@/lib/live";
import { secondsUntil } from "@/lib/normalize";
import { fmtClock, fmtClockSec, fmtCountdown } from "./ui";

const STEPS: { stage: EmergencyStage; label: string }[] = [
  { stage: "notifying", label: "Notify crew lead + safety officer" },
  { stage: "cancel_window", label: "Cancel window" },
  { stage: "calling", label: "Calling" },
  { stage: "connected", label: "Connected" },
  { stage: "done", label: "Done, call logged" },
];

export function ManDownOverlay() {
  const { mandown: md, emergency: em } = useLive();
  const path = usePathname();
  const now = useSimNow(250);
  const [minimized, setMinimized] = useState<string | null>(null);

  if (!md || (md.stage !== "checking" && md.stage !== "confirmed")) return null;

  // The downed worker's own phone shows its own "Are you OK?" screen instead.
  const phone = path.match(/^\/worker\/([^/]+)/)?.[1];
  if (phone === md.worker_id) return null;
  // Other workers' phones only hear about it once it's confirmed (crewmates get alerted).
  if (phone && md.stage !== "confirmed") return null;

  const call = callForIncident(em, md);
  const failed = call?.stage === "failed";
  const downFor = now - md.fell_at;
  const sos = md.reason === "sos";
  const pulse = sos ? "pulse-sos" : "pulse-red";

  // A failed call is never hidden behind the minimized bar: someone has to call 911 now.
  if (minimized === md.id && !failed) {
    return (
      <button onClick={() => setMinimized(null)}
        className={`${pulse} fixed inset-x-0 top-0 z-[2000] flex items-center justify-center gap-3 px-4 py-2 text-sm font-bold text-white`}>
        {sos ? "SOS" : "MAN DOWN"}: {md.worker_name} · {md.site_name} · {sos ? "pressed" : "down"} {fmtCountdown(downFor)}{sos ? " ago" : ""}
        {call && <span className="rounded bg-white/20 px-2 py-0.5">{stageText(call, now)}</span>}
        <span className="underline">Open</span>
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-[2000] overflow-y-auto bg-black/50 backdrop-blur-sm" role="alertdialog" aria-label={sos ? "SOS alert" : "Man down alert"}>
      <div className="mx-auto flex min-h-full max-w-4xl flex-col gap-4 p-3 sm:p-6">
        {/* Header */}
        <div className={`${pulse} rounded-2xl p-5 text-white shadow-2xl sm:p-7`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.2em] opacity-90">
                {sos ? "SOS from" : md.stage === "checking" ? "Possible man down: checking" : "Man down: confirmed"}
              </p>
              <h1 className="mt-1 text-4xl font-black tracking-tight sm:text-6xl">{md.worker_name}</h1>
              <p className="mt-2 text-lg font-semibold">{md.site_name}</p>
              <p className="text-sm opacity-90 tabular">GPS {md.lat.toFixed(5)}, {md.lon.toFixed(5)}</p>
            </div>
            <div className="text-right">
              <p className="text-xs font-bold uppercase tracking-widest opacity-80">{sos ? "SOS pressed" : "Down for"}</p>
              <p className="text-5xl font-black tabular sm:text-6xl">{fmtCountdown(downFor)}</p>
              <p className="text-xs opacity-80">{sos ? "ago, at" : "since"} {fmtClockSec(md.fell_at)}</p>
            </div>
          </div>
          <div className="mt-5 grid grid-cols-3 gap-2 text-center">
            <Vital label="Est. core temp" value={md.last_vitals.core_temp !== null ? `${md.last_vitals.core_temp.toFixed(1)} °C` : "--"} />
            <Vital label="Heart rate" value={md.last_vitals.hr !== null ? `${Math.round(md.last_vitals.hr)} bpm` : "--"} />
            <Vital label="Last reading" value={fmtClock(md.last_vitals.at)} />
          </div>
          <p className="mt-4 rounded-lg bg-black/25 px-3 py-2 text-sm font-medium">
            {sos
              ? `${md.worker_name.split(" ")[0]} held the band button for 3 seconds. Escalating right away: crew lead and nearby crewmates alerted with the location.`
              : md.stage === "checking"
              ? `Band is buzzing. Waiting for ${md.worker_name.split(" ")[0]} to press the button or move. Escalates in ${fmtCountdown(md.confirm_at - now)}.`
              : `No response. Crew lead and nearby crewmates alerted with the location${md.reason === "no_response_to_red" ? " (red heat alert was never acknowledged)" : ""}.`}
          </p>
        </div>

        {/* The emergency call failed: tell the crew lead to call 911, as big as possible. */}
        {failed && <CallFailed md={md} />}

        {/* Emergency call */}
        {call && <CallPanel call={call} now={now} />}

        <div className="flex flex-wrap justify-end gap-2">
          {md.stage === "checking" && (
            <button onClick={() => apiPost(`/workers/${md.worker_id}/button`)} className="rounded-lg border border-white/40 bg-white/10 px-4 py-2 text-sm font-semibold text-white hover:bg-white/20">
              Demo: worker presses band button
            </button>
          )}
          {md.stage === "confirmed" && (
            <button onClick={() => apiPost(`/mandown/${md.id}/resolve`)} className={`rounded-lg bg-white px-4 py-2 text-sm font-bold hover:bg-red-50 ${sos ? "text-violet-800" : "text-red-800"}`}>
              Worker reached, close alert
            </button>
          )}
          {!failed && (
            <button onClick={() => setMinimized(md.id)} className="rounded-lg border border-white/40 bg-black/30 px-4 py-2 text-sm font-semibold text-white hover:bg-black/40">
              Minimize
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Vital({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-black/20 px-2 py-2">
      <p className="text-[11px] font-semibold uppercase tracking-wider opacity-80">{label}</p>
      <p className="text-xl font-bold tabular sm:text-2xl">{value}</p>
    </div>
  );
}

function stageText(call: EmergencyCall, now: number) {
  switch (call.stage) {
    case "notifying": return "Notifying crew lead";
    case "cancel_window": return `Cancel window ${fmtCountdown(call.cancel_deadline ? secondsUntil(call.cancel_deadline, now) : 0)}`;
    case "calling": return "Calling (demo: would call 911)";
    case "connected": return "Connected";
    case "done": return "Call done";
    case "cancelled": return "Cancelled";
    case "failed": return "CALL FAILED: call 911 now";
  }
}

/** Huge red box. Gives the crew lead what to say to 911. */
function CallFailed({ md }: { md: ManDown }) {
  return (
    <div role="alert" className="rounded-2xl border-4 border-white bg-red-700 p-5 text-white shadow-2xl sm:p-8">
      <p className="text-4xl font-black leading-tight sm:text-6xl">{CALL_FAILED_TEXT}</p>
      <p className="mt-4 text-lg font-semibold sm:text-xl">
        Tell them: {md.worker_name}, {md.reason === "sos" ? "pressed SOS" : "collapsed"} at {md.site_name}. GPS {md.lat.toFixed(5)}, {md.lon.toFixed(5)}.
      </p>
    </div>
  );
}

const MODE_LABEL: Record<NonNullable<EmergencyCall["mode"]>, string> = {
  dry_run: "Dry run: nothing dialed",
  fake: "Pretend phone",
  live: "Live phone call",
};

function CallPanel({ call, now }: { call: EmergencyCall; now: number }) {
  // A failed call stopped at the "Calling" step, so that's the one we mark.
  const idx = STEPS.findIndex((s) => s.stage === (call.stage === "failed" ? "calling" : call.stage));
  const canCancel = call.stage === "notifying" || call.stage === "cancel_window";
  // A double tap must send the cancel once: ignore taps while the first one is in flight, then lock it.
  const [sent, setSent] = useState(false);
  const cancel = useMemo(() => singleFlight(async () => {
    const [path, body] = cancelCallRequest(call);
    await apiPost(path, body);
    setSent(true);
  }), [call.id, call.worker_id]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="rounded-2xl border border-line bg-surface p-4 text-ink shadow-2xl sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Emergency call</h2>
          <p className="text-sm text-ink-2">The decision to call is a fixed rule in code (confirmed man-down or SOS → call). The voice agent only reads the report.</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          {call.demo && (
            <span className="rounded-lg border-2 border-danger px-3 py-1.5 text-sm font-black uppercase tracking-wide text-danger-ink">
              {call.target_label}
            </span>
          )}
          {call.mode && <span className="text-xs font-semibold text-muted">{MODE_LABEL[call.mode]}</span>}
        </div>
      </div>

      {/* Stepper */}
      <ol className="mt-4 grid grid-cols-5 gap-1.5">
        {STEPS.map((s, i) => {
          const done = call.stage !== "cancelled" && i < idx;
          const current = i === idx;
          const broke = current && call.stage === "failed";
          // Notify step done, but a phone call didn't go through: a "!" instead of a check mark.
          const partial = done && s.stage === "notifying" && (call.unreached?.length ?? 0) > 0;
          return (
            <li key={s.stage} className="flex flex-col gap-1.5">
              <span className={`h-1.5 rounded-full ${partial ? "bg-warn" : done || (current && call.stage === "done") ? "bg-ok" : current ? "bg-danger" : "bg-surface-2"}`} />
              <span className={`text-[11px] leading-tight sm:text-xs ${broke ? "font-black text-danger-ink" : partial ? "font-bold text-warn-ink" : current ? "font-bold text-ink" : done ? "text-ink-2" : "text-muted"}`}>
                {partial ? "! " : done ? "✓ " : ""}{broke ? "✗ Call failed" : s.label}
              </span>
            </li>
          );
        })}
      </ol>

      {(call.unreached?.length ?? 0) > 0 && (
        <p className="mt-3 text-sm font-semibold text-warn-ink">Phone alert didn&apos;t go through to: {call.unreached!.join(", ")}. Tell them yourself.</p>
      )}

      {call.stage === "cancelled" && <p className="mt-3 font-semibold text-ok-ink">{call.outcome}</p>}

      {canCancel && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl bg-warn-bg p-3">
          <p className="flex-1 text-sm font-medium text-warn-ink">
            {call.stage === "notifying" ? "Crew lead and safety officer are being notified." : `Calling in ${fmtCountdown(call.cancel_deadline ? secondsUntil(call.cancel_deadline, now) : 0)} unless cancelled.`}
          </p>
          {/* Same route as session D suggests: cancel by worker, not by call id. */}
          <button onClick={() => { if (!sent) void cancel(); }} disabled={sent}
            className="min-h-11 rounded-lg bg-ink px-4 py-2 text-sm font-bold text-page hover:opacity-90 disabled:opacity-60">
            {sent ? "Cancelling…" : "Cancel, false alarm"}
          </button>
        </div>
      )}

      {call.stage === "calling" && (
        <p className="mt-4 flex items-center gap-2 text-sm font-semibold">
          <span className="relative inline-flex h-3 w-3 text-danger"><span className="ring-pulse absolute inset-0 rounded-full" /><span className="h-3 w-3 rounded-full bg-danger" /></span>
          Ringing the test phone (a teammate playing dispatcher). Never real 911 from the prototype.
        </p>
      )}

      {call.transcript.length > 0 && (
        <div className="mt-4 space-y-2 rounded-xl bg-surface-2 p-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted">Live transcript</p>
          {call.transcript.map((line, i) => (
            <p key={i} className="text-sm">
              <span className={`mr-2 font-bold ${line.speaker === "agent" ? "text-accent" : "text-ink"}`}>{line.speaker === "agent" ? "Voice agent" : "Dispatcher"}</span>
              {line.text}
            </p>
          ))}
        </div>
      )}

      {call.stage === "done" && <p className="mt-3 text-sm text-ink-2">{call.outcome}</p>}

      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-muted">Call log ({call.steps.length} steps)</summary>
        <ul className="mt-2 space-y-1">
          {call.steps.map((s, i) => <li key={i} className="text-ink-2"><span className="tabular text-muted">{fmtClockSec(s.at)}</span> {s.text}</li>)}
        </ul>
      </details>
    </div>
  );
}
