/**
 * Small fixes so the UI copes with the real backend's shapes (plain TS, tested with Node).
 *
 * 1. Session C1's SiteStatus puts lightning and heat numbers straight on the hazard
 *    (nearest_miles, all_clear_at, strikes, wbgt_c, heat_index_f) instead of inside
 *    `lightning` / `heat`. We accept both.
 * 2. index_tomorrow can be null (session C2's index not computed yet).
 * 3. Two clocks: during replay, worker data runs on replay time, but live hazard feeds
 *    (lightning all-clear) and the emergency call (session D) use the real clock.
 *    secondsUntil() measures against whichever clock the time belongs to.
 */

import type { Hazard, SiteStatus } from "./contract.ts";

type Loose = Hazard & {
  nearest_miles?: number | null; last_strike_at?: number | null; all_clear_at?: number | null;
  strikes?: { lat: number; lon: number; at: number; miles: number }[];
  wbgt_c?: number | null; heat_index_f?: number | null;
};

export function normalizeHazard(raw: Hazard): Hazard {
  const h = { ...(raw as Loose) };
  if (!h.lightning && (h.all_clear_at !== undefined || h.strikes !== undefined || h.nearest_miles !== undefined)) {
    h.lightning = { nearest_miles: h.nearest_miles ?? null, last_strike_at: h.last_strike_at ?? null, all_clear_at: h.all_clear_at ?? null, strikes: h.strikes ?? [] };
  }
  if (!h.heat && typeof h.wbgt_c === "number") h.heat = { wbgt_c: h.wbgt_c, heat_index_f: h.heat_index_f ?? NaN };
  h.detail = h.detail ?? "";
  h.source = h.source ?? "";
  h.headline = h.headline ?? "";
  return h;
}

export function normalizeSite(s: SiteStatus): SiteStatus {
  return { ...s, hazards: (s.hazards ?? []).map(normalizeHazard), index_drivers: s.index_drivers ?? [] };
}

/** Seconds from now until `t`, on whichever clock (replay or real) `t` is closest to. */
export function secondsUntil(t: number, simNow: number, wallNow = Date.now() / 1000): number {
  const a = t - simNow;
  const b = t - wallNow;
  return Math.abs(a) <= Math.abs(b) ? a : b;
}
