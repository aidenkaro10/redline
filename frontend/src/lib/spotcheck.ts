/**
 * Sending one thermometer spot check. The reading is checked first (lib/format.ts);
 * nothing goes out unless it's a number between 34.0 and 42.0 °C.
 * `post` is apiPost in the app and a fake in the tests.
 */
import { parseThermometer } from "./format.ts";

type Post = (path: string, body: unknown) => Promise<unknown>;

export interface SpotCheckOutcome { sent: boolean; accepted: boolean; text: string }

export async function sendSpotCheck(workerId: string, input: string, post: Post, describe: (offsetC: number) => string): Promise<SpotCheckOutcome> {
  const parsed = parseThermometer(input);
  if (!parsed.ok) return { sent: false, accepted: false, text: parsed.message };
  try {
    // Our mock answers with SpotCheckResult; session A answers with its flat offset_update event.
    const r = (await post("/spot-check", { worker_id: workerId, thermometer_c: parsed.value })) as
      { accepted?: boolean; message?: string; offset_c?: number; reason?: string | null; detail?: unknown } | null;
    const accepted = r?.accepted === true;
    const text = r?.message
      ?? (accepted && typeof r?.offset_c === "number" ? `Learned: ${describe(r.offset_c)}.` : null)
      ?? (typeof r?.reason === "string" ? r.reason : null)
      ?? (r?.detail ? "The backend refused that reading." : accepted ? "Reading added." : "Reading ignored (too far from the model, probably a bad reading).");
    return { sent: true, accepted, text };
  } catch {
    return { sent: true, accepted: false, text: "Couldn't send the reading. Is the backend running?" };
  }
}
