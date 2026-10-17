/**
 * Session D's emergency events -> the one EmergencyCall the UI shows.
 *
 * Session D (backend/emergency) sends a flat `emergency_status` event every time
 * anything changes: "calling the crew lead", "crew lead connected", "cancel window
 * open", "emergency call connected", "done". The man-down screen wants ONE object
 * with the current stage and a log of steps. This file turns the stream of small
 * events into that one object, so session A can pass D's events straight through.
 *
 * Plain TypeScript (no React), so the tests can run it with Node.
 */

import type { EmergencyCall, EmergencyStage, EmergencyStatus, ManDown } from "./contract.ts";

/** Shown very big when the emergency call fails. Same words as session D. */
export const CALL_FAILED_TEXT = "Emergency call FAILED. Call 911 yourself now.";

/** The prototype never dials 911: the emergency leg always calls a teammate's test phone. */
export const DEFAULT_DEMO_LABEL = "Demo: would call 911";

/** The end of the line. Once a call is here, later events can't move it. */
export function isFinalStage(stage: EmergencyStage) {
  return stage === "done" || stage === "cancelled" || stage === "failed";
}

// How far along each stage is. The stage only ever moves forward, so a late or
// out-of-order event can't drag the screen back (e.g. from "connected" to "calling").
const ORDER: Record<EmergencyStage, number> = {
  notifying: 0, cancel_window: 1, calling: 2, connected: 3, done: 4, cancelled: 4, failed: 4,
};

/**
 * Map one D event to a UI stage (the mapping agreed with session E):
 *   notify                -> notifying
 *   cancel_window         -> cancel_window
 *   emergency_call + calling   -> calling
 *   emergency_call + connected -> connected
 *   done + completed      -> done
 *   done + cancelled      -> cancelled
 *   done + failed         -> failed
 * Returns null when the event doesn't change the stage (e.g. the emergency call
 * "completed": the `done` event comes right after it).
 */
export function stageFor(ev: EmergencyStatus): EmergencyStage | null {
  // A cancel or a failed emergency call ends things whatever stage it arrives in.
  if (ev.status === "cancelled") return "cancelled";
  if (ev.status === "failed" && (ev.stage === "emergency_call" || ev.stage === "done")) return "failed";
  switch (ev.stage) {
    case "notify": return "notifying";              // includes the crew lead / safety officer calls
    case "cancel_window": return "cancel_window";
    case "emergency_call":
      if (ev.status === "connected") return "connected";
      if (ev.status === "calling") return "calling";
      return null;
    case "done": return "done";
  }
  return null;
}

const WHO: Record<NonNullable<EmergencyStatus["target"]>, string> = {
  crew_lead: "Crew lead",
  safety_officer: "Safety officer",
  emergency: "Emergency call",
};

/** One line for the call log, e.g. "Crew lead (+1 ••• ••• 0101): connected". */
export function stepText(ev: EmergencyStatus): string {
  const what = ev.message || ev.status;
  if (!ev.target) return what;
  const who = WHO[ev.target] + (ev.number_masked ? ` (${ev.number_masked})` : "");
  return `${who}: ${what}`;
}

/**
 * Fold one `emergency_status` event into the current EmergencyCall.
 * Returns a NEW object (React needs that to notice the change).
 *
 * - A new incident_id starts a fresh call.
 * - `mandown` is the incident on screen right now. If it's the same worker, we link
 *   the call to it so the man-down screen finds it.
 */
export function foldEmergencyStatus(prev: EmergencyCall | null, ev: EmergencyStatus, mandown: ManDown | null): EmergencyCall {
  const fresh = !prev || prev.id !== ev.incident_id;
  const call: EmergencyCall = fresh
    ? {
        id: ev.incident_id,
        mandown_id: ev.incident_id,
        worker_id: ev.worker_id,
        stage: "notifying",
        started_at: ev.timestamp,
        cancel_deadline: null,
        demo: true,                       // the prototype never dials 911 (session D's rule)
        target_label: DEFAULT_DEMO_LABEL,
        steps: [],
        transcript: [],                   // D doesn't send a transcript
        outcome: null,
      }
    : { ...prev, steps: [...prev.steps] };

  if (mandown && mandown.worker_id === ev.worker_id) call.mandown_id = mandown.id;
  if (ev.cancel_deadline != null) call.cancel_deadline = ev.cancel_deadline;
  if (ev.demo_label) call.target_label = ev.demo_label;
  if (ev.mode) call.mode = ev.mode;

  // Move the stage forward (never back, never out of an ending).
  const next = stageFor(ev);
  if (next && !isFinalStage(call.stage) && ORDER[next] >= ORDER[call.stage]) {
    call.stage = next;
    if (next === "failed") call.outcome = CALL_FAILED_TEXT;   // always these exact words, whatever the message says
    else if (isFinalStage(next)) call.outcome = ev.message || (next === "done" ? "Call done." : "Cancelled.");
  }

  call.steps.push({ at: ev.timestamp, stage: next ?? call.stage, text: stepText(ev) });
  return call;
}

/**
 * The call that belongs to this man-down/SOS, or null.
 * Matches by id first. Falls back to "same worker, started after the fall", because
 * session D's incident ids are its own (`inc-<worker>-<time>`), not the man-down id.
 */
export function callForIncident(em: EmergencyCall | null, md: ManDown): EmergencyCall | null {
  if (!em) return null;
  if (em.mandown_id === md.id) return em;
  if (em.worker_id === md.worker_id && em.started_at >= md.fell_at - 1) return em;
  return null;
}
