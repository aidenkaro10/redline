/**
 * The scripted demo, as a list of timed steps (replay minutes since start).
 *
 * At the default 10x replay speed, one replay minute = 6 real seconds.
 * The heat alerts are NOT scripted: they come from the alert rules reacting
 * to the rising forecast, the same way the real system works.
 *
 *   min 0    Maria (wearing the band) is doing light work. Everyone green.
 *   min 3    She starts hard exercise. Forecast climbs -> yellow about a minute later.
 *   ~min 11  Forecast crosses the danger line -> red. She presses the band button (ack).
 *   min 13   Thunderstorm: strikes at 8.6, 6.2, then 4.8 miles. Stop-work + 30-min countdown.
 *   min 14   Jake holds his band button 3 s: SOS. Full-screen "SOS from Jake Turner", replay drops
 *            to 1x, escalation starts right away. 5 s into the cancel window the crew lead
 *            checks in and presses "Cancel, false alarm" (if the presenter hasn't already).
 *            Replay goes back to 10x.
 *   min 17   She "collapses". Replay drops to 1x. Man-down check, then confirmed,
 *            then the emergency call (cancel window, "Demo: would call 911", transcript).
 *
 * Whole thing: a little over 3 minutes.
 */

import type { MockEngine } from "./engine.ts";

export interface ScenarioStep {
  at_min: number;
  label: string;
  run: (e: MockEngine) => void;
}

const SITE = "fl-deerhaven";
const WEARER = "worker-1";
const SOS_WORKER = "worker-2";
const SCENARIO_SPEED = 10;

export const SCENARIO: ScenarioStep[] = [
  {
    at_min: 0,
    label: "Light work, everyone green",
    run: (e) => e.addLog("Replay: Maria is doing light work", "demo script"),
  },
  {
    at_min: 3,
    label: "Hard exercise starts",
    run: (e) => {
      const w = e.workers.get(WEARER);
      if (w) w.scriptedIntensity = 0.95;
      e.addLog("Replay: Maria starts hard exercise. Watch the forecast band climb.", "demo script");
    },
  },
  {
    at_min: 13,
    label: "Thunderstorm moving in",
    run: (e) => e.addStrike(SITE, 8.6, 250),
  },
  {
    at_min: 14,
    label: "Jake holds SOS (crew lead will cancel: false alarm)",
    run: (e) => { e.sos(SOS_WORKER, "band"); },
  },
  { at_min: 14.5, label: "Lightning getting closer", run: (e) => e.addStrike(SITE, 6.2, 240) },
  { at_min: 15.5, label: "Lightning getting closer", run: (e) => e.addStrike(SITE, 4.8, 255) },
  {
    at_min: 17,
    label: "Maria collapses",
    run: (e) => e.collapse(WEARER, "fall"),
  },
];

/** The demo ends after this minute, once the emergency call has finished. */
export const SCENARIO_END_MIN = 17.5;

/**
 * Things the script does in reaction to what happens, checked every step:
 * - The wearer presses the band button ~20 s after a red alert starts (shows the ack flow).
 * - 5 s into the SOS cancel window, the crew lead cancels it (false alarm), then replay speeds back up.
 */
export function scenarioReactions(e: MockEngine) {
  const red = e.activeAlertsFor(WEARER).find((a) => a.kind === "HEAT_RED" && !a.acknowledged_at && a.worker_id);
  if (red && e.sim - red.started_at >= 20) e.buttonPress(WEARER);

  const em = e.emergency;
  if (em && em.worker_id === SOS_WORKER && em.stage === "cancel_window" && em.cancel_deadline !== null) {
    const opened = em.steps.find((x) => x.stage === "cancel_window")?.at ?? e.sim;
    if (e.sim - opened >= 5) {
      e.addLog("Script: crew lead called Jake. He's fine, the SOS was a false alarm.", "demo script");
      e.cancelEmergency(em.id);
    }
  }
  // Once the SOS is over (by script or by the presenter), go back to the replay speed.
  if (em && em.worker_id === SOS_WORKER && (em.stage === "cancelled" || em.stage === "done") && !e.mandown
    && ![...e.workers.values()].some((w) => w.collapsed) && e.demo.speed !== SCENARIO_SPEED) {
    e.demo.speed = SCENARIO_SPEED;
    e.addLog(`Replay back to ${SCENARIO_SPEED}x`, "demo");
    e.emitDemo();
  }
}
