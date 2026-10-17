/**
 * Data for the core temperature chart: the past estimates, the forecast (median + 10-90 band)
 * joined at the latest estimate, and the warning / danger lines with their labels.
 * Plain TS so the tests can check it without drawing anything.
 */
import type { Estimate } from "./contract.ts";

export interface CoreRow {
  t: number;                  // minutes from now (negative = past)
  mean?: number;
  range?: [number, number];
  kalman?: number;
  p50?: number;
  band?: [number, number];
}

export interface CoreChartData {
  rows: CoreRow[];
  warning: { y: number; label: string };
  danger: { y: number; label: string };
  domain: [number, number];
}

export function buildCoreChart(estimates: Estimate[], now: number, pastMinutes = 40): CoreChartData | null {
  const last = estimates[estimates.length - 1];
  if (!last) return null;
  const toT = (ts: number) => (ts - now) / 60;
  const rows: CoreRow[] = estimates
    .filter((e) => toT(e.timestamp) >= -pastMinutes)
    .map((e) => ({ t: toT(e.timestamp), mean: e.core.mean, range: [e.core.p10, e.core.p90] as [number, number], kalman: e.kalman }));
  if (!rows.length) rows.push({ t: toT(last.timestamp), mean: last.core.mean, range: [last.core.p10, last.core.p90], kalman: last.kalman });
  // The forecast starts at the latest estimate, so the two lines join.
  const t0 = toT(last.timestamp);
  rows[rows.length - 1] = { ...rows[rows.length - 1], p50: last.core.mean, band: [last.core.mean, last.core.mean] };
  for (const f of last.forecast ?? []) rows.push({ t: t0 + f.minutes_ahead, p50: f.p50, band: [f.p10, f.p90] });

  const { warning, danger, demo } = last.thresholds;
  const values = rows.flatMap((r) => [r.range?.[1], r.band?.[1], r.kalman, r.range?.[0], r.band?.[0]])
    .filter((x): x is number => typeof x === "number" && Number.isFinite(x));
  const lo = Math.floor(Math.min(36.8, ...values) * 2) / 2;
  const hi = Math.ceil(Math.max(danger + 0.3, ...values) * 2) / 2;
  return {
    rows,
    warning: { y: warning, label: demo ? `Demo warning ${warning.toFixed(1)}` : `warning ${warning.toFixed(1)}` },
    danger: { y: danger, label: demo ? `Demo threshold ${danger.toFixed(1)}` : `danger ${danger.toFixed(1)}` },
    domain: [lo, hi],
  };
}
