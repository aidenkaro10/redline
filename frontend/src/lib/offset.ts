/**
 * Spot-check results come in two shapes:
 *   - this contract: {type: "offset_update", offset: OffsetUpdate}
 *   - session A's live system (passed through as-is):
 *     {type: "offset_update", worker_id, timestamp, offset_c, confidence, n_checks,
 *      accepted, thermometer_c, model_c, miss_c, reason}
 * This turns either one into an OffsetUpdate, building the history on our side
 * when the backend doesn't send one. Plain TypeScript so Node tests can run it.
 */

import type { OffsetUpdate } from "./contract.ts";

/** A's flat shape (every field optional: we only trust what's there). */
export interface FlatOffset {
  worker_id?: string;
  timestamp?: number;
  offset_c?: number;
  confidence?: number;
  n_checks?: number;
  accepted?: boolean;
  thermometer_c?: number;
  model_c?: number;
  miss_c?: number;
  reason?: string;
}

export function toOffsetUpdate(raw: unknown, prev?: OffsetUpdate): OffsetUpdate | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { offset?: OffsetUpdate } & FlatOffset;
  if (r.offset && typeof r.offset === "object") return r.offset;          // our shape, wrapped
  const full = raw as Partial<OffsetUpdate>;
  if (typeof full.checks === "number" && Array.isArray(full.history) && full.worker_id) return full as OffsetUpdate;   // our shape, bare
  if (typeof r.offset_c !== "number" || !r.worker_id) return null;          // not an offset event

  const t = r.timestamp ?? Date.now() / 1000;
  const checks = r.n_checks ?? prev?.checks ?? 0;
  const confidence = r.confidence ?? checks / (checks + 2);
  let history = prev?.history ?? [];
  if (!history.length) history = [{ timestamp: t - 1, offset_c: 0, confidence: 0, checks: 0 }];
  if (checks !== history[history.length - 1].checks) history = [...history, { timestamp: t, offset_c: r.offset_c, confidence, checks }];
  return {
    worker_id: r.worker_id, timestamp: t, offset_c: r.offset_c, confidence, checks,
    last_check: typeof r.thermometer_c === "number"
      ? { thermometer_c: r.thermometer_c, model_estimate_c: r.model_c ?? r.thermometer_c + (r.miss_c ?? 0), miss_c: r.miss_c ?? 0, accepted: r.accepted !== false, at: t }
      : prev?.last_check ?? null,
    history,
  };
}
