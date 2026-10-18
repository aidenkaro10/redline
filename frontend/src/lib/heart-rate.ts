/**
 * Demo heart rate (the band has no pulse sensor yet).
 * POST /api/demo/heart_rate {mode} turns it on or off; GET /api/demo/heart_rate returns {mode}.
 * While it's on, vitals and estimates for that worker carry hr_source "demo_input",
 * and the worker's own view and the demo view say so. Never shown on the crew view.
 * Plain TypeScript so Node tests can run it.
 */

import type { Request } from "./demo-actions.ts";

export type HeartRateMode = "off" | "rest" | "active";

export const HR_PATH = "/demo/heart_rate";
export const DEMO_HR_TAG = "Heart rate: demo input";

/** The three buttons, in order. */
export const HR_MODES: { mode: HeartRateMode; label: string }[] = [
  { mode: "off", label: "Off (real sensors)" },
  { mode: "rest", label: "Resting (55-70 bpm)" },
  { mode: "active", label: "Running in place (130-155 bpm)" },
];

export const heartRateRequest = (mode: HeartRateMode): Request => [HR_PATH, { mode }];

/** Reads {mode} from the GET (or POST) reply. Anything else = unknown (null). */
export function readHrMode(reply: unknown): HeartRateMode | null {
  const m = (reply as { mode?: unknown } | null)?.mode;
  return m === "off" || m === "rest" || m === "active" ? m : null;
}

/** True when any of these (latest vitals, latest estimate) says the heart rate is demo input. */
export function isDemoHr(...items: ({ hr_source?: string | null } | null | undefined)[]) {
  return items.some((x) => x?.hr_source === "demo_input");
}

/**
 * What a button press does: POST the mode once (a double tap sends it once), then show
 * the mode the backend says is on. A reply with ok: false leaves the old mode showing.
 */
export function hrPicker(post: (path: string, body: Record<string, unknown>) => Promise<unknown>, onMode: (m: HeartRateMode) => void) {
  let busy = false;
  return async (mode: HeartRateMode) => {
    if (busy) return;
    busy = true;
    try {
      const reply = await post(...heartRateRequest(mode));
      const confirmed = readHrMode(reply) ?? ((reply as { ok?: boolean } | null)?.ok === false ? null : mode);
      if (confirmed) onMode(confirmed);
    } finally {
      busy = false;
    }
  };
}
