"use client";

/**
 * Charts for the demo view and the worker's phone.
 *
 * CoreChart: core temp over the last ~40 min (model estimate with its range,
 * plus the Kalman baseline as a dashed line), then the 30-minute forecast:
 * median line + shaded 10th-90th percentile band. Warning and danger lines on top.
 *
 * Sparkline: one measure over time (heart rate or skin temp). One axis per chart, always.
 */

import {
  Area, CartesianGrid, ComposedChart, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import type { Estimate, Vitals } from "@/lib/contract";
import { useIsDark } from "./Nav";

/** Chart colors per theme (dataviz reference palette). Text stays in ink colors. */
function palette(dark: boolean) {
  return dark
    ? { series: "#3987e5", band: "#3987e5", kalman: "#898781", grid: "#2c2c2a", axis: "#898781", ink: "#f0efec", warn: "#fab219", danger: "#e66767", surface: "#1a1a19" }
    : { series: "#2a78d6", band: "#2a78d6", kalman: "#898781", grid: "#e1e0d9", axis: "#898781", ink: "#0b0b0b", warn: "#b07800", danger: "#d03b3b", surface: "#fcfcfb" };
}

interface Row {
  t: number;                  // minutes from now (negative = past)
  mean?: number;
  range?: [number, number];
  kalman?: number;
  p50?: number;
  band?: [number, number];
}

export function CoreChart({ estimates, now, height = 340 }: { estimates: Estimate[]; now: number; height?: number }) {
  const dark = useIsDark();
  const c = palette(dark);
  const last = estimates[estimates.length - 1];
  if (!last) return <div style={{ height }} className="grid place-items-center text-sm text-muted">Waiting for the first estimate…</div>;

  const toT = (ts: number) => (ts - now) / 60;
  const rows: Row[] = estimates
    .filter((e) => toT(e.timestamp) >= -40)
    .map((e) => ({ t: toT(e.timestamp), mean: e.core.mean, range: [e.core.p10, e.core.p90], kalman: e.kalman }));
  // The forecast starts at the latest estimate, so the two lines join.
  const t0 = toT(last.timestamp);
  rows[rows.length - 1] = { ...rows[rows.length - 1], p50: last.core.mean, band: [last.core.mean, last.core.mean] };
  for (const f of last.forecast) rows.push({ t: t0 + f.minutes_ahead, p50: f.p50, band: [f.p10, f.p90] });

  const { warning, danger, demo } = last.thresholds;
  const values = rows.flatMap((r) => [r.range?.[1], r.band?.[1], r.kalman, r.range?.[0], r.band?.[0]]).filter((x): x is number => x !== undefined);
  const lo = Math.floor(Math.min(36.8, ...values) * 2) / 2;
  const hi = Math.ceil(Math.max(danger + 0.3, ...values) * 2) / 2;

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 18, right: 92, bottom: 4, left: 0 }}>
          <CartesianGrid stroke={c.grid} vertical={false} />
          <XAxis
            dataKey="t" type="number" domain={[-40, 30]} ticks={[-40, -30, -20, -10, 0, 10, 20, 30]}
            tickFormatter={(t: number) => (t === 0 ? "now" : t > 0 ? `+${t}` : `${t}`)}
            stroke={c.axis} tick={{ fill: c.axis, fontSize: 11 }} tickLine={false}
            label={{ value: "minutes", position: "insideBottomRight", offset: -2, fill: c.axis, fontSize: 10 }}
          />
          <YAxis domain={[lo, hi]} ticks={Array.from({ length: Math.round((hi - lo) / 0.5) + 1 }, (_, i) => lo + i * 0.5)} tickFormatter={(v: number) => v.toFixed(1)} stroke={c.axis} tick={{ fill: c.axis, fontSize: 11 }} width={40} tickLine={false} axisLine={false} />

          {/* forecast band (10th-90th percentile) and the estimate's own range */}
          <Area dataKey="band" stroke="none" fill={c.band} fillOpacity={0.22} isAnimationActive={false} connectNulls={false} />
          <Area dataKey="range" stroke="none" fill={c.band} fillOpacity={0.12} isAnimationActive={false} />

          <ReferenceLine y={warning} stroke={c.warn} strokeDasharray="5 4" strokeWidth={1.5}
            label={{ value: `${demo ? "DEMO " : ""}warning ${warning.toFixed(1)}`, position: "right", fill: c.warn, fontSize: 11, fontWeight: 600 }} />
          <ReferenceLine y={danger} stroke={c.danger} strokeWidth={2}
            label={{ value: `${demo ? "DEMO " : ""}danger ${danger.toFixed(1)}`, position: "right", fill: c.danger, fontSize: 11, fontWeight: 700 }} />
          <ReferenceLine x={0} stroke={c.ink} strokeOpacity={0.5} strokeWidth={1} label={{ value: "now", position: "top", fill: c.ink, fontSize: 11 }} />

          <Line dataKey="kalman" stroke={c.kalman} strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          <Line dataKey="mean" stroke={c.series} strokeWidth={2.5} dot={false} isAnimationActive={false} />
          <Line dataKey="p50" stroke={c.series} strokeWidth={2} strokeDasharray="6 4" dot={false} isAnimationActive={false} />

          <Tooltip
            cursor={{ stroke: c.axis, strokeWidth: 1 }}
            isAnimationActive={false}
            content={({ active, payload }) => {
              const r = (active && payload?.[0]?.payload) as Row | undefined;
              if (!r) return null;
              return (
                <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
                  <p className="font-semibold text-ink">{r.t <= 0.01 ? `${Math.round(-r.t)} min ago` : `${Math.round(r.t)} min ahead`}</p>
                  {r.mean !== undefined && <p className="text-ink-2">Estimate <b className="text-ink tabular">{r.mean.toFixed(2)} °C</b>{r.range && <span className="tabular"> ({r.range[0].toFixed(2)}–{r.range[1].toFixed(2)})</span>}</p>}
                  {r.kalman !== undefined && <p className="text-ink-2">Kalman (ECTemp) <b className="text-ink tabular">{r.kalman.toFixed(2)} °C</b></p>}
                  {r.p50 !== undefined && r.t > 0.01 && <p className="text-ink-2">Forecast <b className="text-ink tabular">{r.p50.toFixed(2)} °C</b>{r.band && <span className="tabular"> (10th–90th: {r.band[0].toFixed(2)}–{r.band[1].toFixed(2)})</span>}</p>}
                </div>
              );
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Legend for CoreChart (two or more series always get one). */
export function CoreLegend() {
  const dark = useIsDark();
  const c = palette(dark);
  const item = (swatch: React.ReactNode, text: string) => <span className="flex items-center gap-1.5">{swatch}{text}</span>;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {item(<span className="h-[3px] w-5 rounded" style={{ background: c.series }} />, "Core temp estimate")}
      {item(<span className="h-3 w-5 rounded-sm" style={{ background: c.band, opacity: 0.3 }} />, "Forecast, 10th–90th percentile")}
      {item(<span className="w-5 border-t-2 border-dashed" style={{ borderColor: c.series }} />, "Forecast median")}
      {item(<span className="w-5 border-t-2 border-dashed" style={{ borderColor: c.kalman }} />, "Kalman baseline (ECTemp)")}
    </div>
  );
}

export function Sparkline({ vitals, field, now, unit, height = 90, digits = 0, pad = 3 }: {
  vitals: Vitals[]; field: "hr" | "skin_temp"; now: number; unit: string; height?: number; digits?: number; pad?: number;
}) {
  const dark = useIsDark();
  const c = palette(dark);
  const rows = vitals
    .filter((v) => v[field] !== null && v.timestamp >= now - 15 * 60)
    .map((v) => ({ t: (v.timestamp - now) / 60, y: v[field] as number }));
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 6, right: 4, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={c.grid} vertical={false} />
          <XAxis dataKey="t" type="number" domain={[-15, 0]} ticks={[-15, -10, -5, 0]} tickFormatter={(t: number) => (t === 0 ? "now" : `${t}m`)} stroke={c.axis} tick={{ fill: c.axis, fontSize: 10 }} tickLine={false} />
          <YAxis domain={[(d: number) => Math.floor((d - pad) * 10) / 10, (d: number) => Math.ceil((d + pad) * 10) / 10]} tickFormatter={(v: number) => v.toFixed(digits)} stroke={c.axis} tick={{ fill: c.axis, fontSize: 10 }} width={34} tickLine={false} axisLine={false} tickCount={4} />
          <Line dataKey="y" stroke={c.series} strokeWidth={2} dot={false} isAnimationActive={false} />
          <Tooltip
            isAnimationActive={false}
            cursor={{ stroke: c.axis }}
            content={({ active, payload }) => {
              const r = (active && payload?.[0]?.payload) as { t: number; y: number } | undefined;
              if (!r) return null;
              return <div className="rounded-md border border-line bg-surface px-2 py-1 text-xs shadow"><b className="tabular">{r.y.toFixed(digits)} {unit}</b> <span className="text-muted">{Math.round(-r.t * 60)} s ago</span></div>;
            }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
