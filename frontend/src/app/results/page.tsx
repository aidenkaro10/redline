"use client";

/**
 * Results: every model next to the Kalman baseline (CLAUDE.md rule), on people
 * the model never trained on. Plus the personal twins panel.
 *
 * Data: GET /api/results and GET /api/twins. If the backend doesn't serve
 * results yet, we show the numbers bundled from docs/results.md.
 */

import { Bar, BarChart, CartesianGrid, LabelList, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useIsDark } from "@/components/Nav";
import { Card } from "@/components/ui";
import type { MetricRow, ModelResults, TwinReport } from "@/lib/contract-extra";
import { useFetch } from "@/lib/live";
import { RESULTS as BUNDLED } from "@/mock/extras";

export default function ResultsPage() {
  const { data, error } = useFetch<ModelResults>("/results");
  const { data: twins } = useFetch<TwinReport>("/twins");
  const r = data ?? (error ? BUNDLED : null);
  if (!r) return <main className="p-6 text-muted">Loading results…</main>;

  const groups = [...new Set(r.rows.map((x) => x.group))];
  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6">
      <div>
        <p className="text-xs font-bold uppercase tracking-wider text-accent">Results</p>
        <h1 className="text-3xl font-bold tracking-tight">Our model vs. the Army&apos;s Kalman filter</h1>
        <p className="mt-1 text-sm text-ink-2">{r.split} Updated {r.updated}.{!data && " (Bundled copy of docs/results.md: this backend doesn't serve /api/results yet.)"}</p>
      </div>

      <section className="grid gap-4 lg:grid-cols-3">
        <Headline label="Core temp error now" base={r.rows[0].values[0]} ours={r.rows[0].values[1]} unit="°C" />
        <Headline label="Error 30 min ahead" base={r.rmse_by_horizon[3]?.values[0]} ours={r.rmse_by_horizon[3]?.values[1]} unit="°C" />
        <Headline label="Heat warning lead time" base={findRow(r, "Yellow warning lead time")?.values[0]} ours={findRow(r, "Yellow warning lead time")?.values[1]} unit="min" higherIsBetter />
      </section>

      <Card title="Average error (RMSE) by how far ahead" right="lower is better">
        <RmseChart r={r} />
      </Card>

      <Card title="Every metric" bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="px-4 py-2 font-medium">Metric</th>
                {r.models.map((m) => <th key={m} className="px-3 py-2 text-right font-medium">{m}</th>)}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <GroupRows key={g} group={g} rows={r.rows.filter((x) => x.group === g)} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
        {r.notes.map((n) => <li key={n}>{n}</li>)}
      </ul>

      <TwinsPanel twins={twins} />
    </main>
  );
}

function findRow(r: ModelResults, name: string) {
  return r.rows.find((x) => x.name === name);
}

function Headline({ label, base, ours, unit, higherIsBetter }: { label: string; base?: number | null; ours?: number | null; unit: string; higherIsBetter?: boolean }) {
  const change = base && ours ? ((ours - base) / base) * 100 : null;
  const good = change !== null && (higherIsBetter ? change > 0 : change < 0);
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="text-sm text-ink-2">{label}</p>
      <p className="mt-1 text-4xl font-bold">{ours ?? "--"}<span className="ml-1 text-base font-medium text-ink-2">{unit}</span></p>
      <p className="mt-1 text-sm">
        <span className="text-muted">Kalman: {base ?? "--"} {unit}</span>
        {change !== null && <span className={`ml-2 font-semibold ${good ? "text-ok-ink" : "text-danger-ink"}`}>{change > 0 ? "+" : ""}{Math.round(change)}%</span>}
      </p>
    </div>
  );
}

function winner(row: MetricRow): number | null {
  let best: number | null = null;
  row.values.forEach((v, i) => {
    if (v === null) return;
    const score = row.better === "lower" ? -v : row.better === "higher" ? v : -Math.abs(v - (row.target ?? 0));
    const cur = best === null ? null : row.values[best]!;
    const curScore = cur === null ? -Infinity : row.better === "lower" ? -cur : row.better === "higher" ? cur : -Math.abs(cur - (row.target ?? 0));
    if (score > curScore) best = i;
  });
  return best;
}

function GroupRows({ group, rows }: { group: string; rows: MetricRow[] }) {
  return (
    <>
      <tr><td colSpan={99} className="bg-surface-2 px-4 py-1.5 text-xs font-bold uppercase tracking-wider text-muted">{group}</td></tr>
      {rows.map((row) => {
        const w = winner(row);
        return (
          <tr key={row.name} className="border-b border-line last:border-0">
            <td className="px-4 py-2">{row.name}{row.unit && <span className="text-muted"> ({row.unit})</span>}</td>
            {row.values.map((v, i) => (
              <td key={i} className={`px-3 py-2 text-right tabular ${i === w ? "font-bold text-ink" : "text-ink-2"}`}>
                {v === null ? "--" : row.text?.[i] ?? v}
                {i === w && " ✓"}
              </td>
            ))}
          </tr>
        );
      })}
    </>
  );
}

function RmseChart({ r }: { r: ModelResults }) {
  const dark = useIsDark();
  const c = dark ? { base: "#898781", ours: "#3987e5", grid: "#2c2c2a", axis: "#898781", ink: "#f0efec" } : { base: "#898781", ours: "#2a78d6", grid: "#e1e0d9", axis: "#898781", ink: "#0b0b0b" };
  const rows = r.rmse_by_horizon.map((h) => ({ name: h.minutes_ahead === 0 ? "Now" : `+${h.minutes_ahead} min`, kalman: h.values[0], ours: h.values[1] }));
  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 20, right: 8, bottom: 0, left: 0 }} barGap={2}>
          <CartesianGrid stroke={c.grid} vertical={false} />
          <XAxis dataKey="name" stroke={c.axis} tick={{ fill: c.axis, fontSize: 12 }} tickLine={false} />
          <YAxis stroke={c.axis} tick={{ fill: c.axis, fontSize: 11 }} tickFormatter={(v: number) => `${v.toFixed(1)}°`} axisLine={false} tickLine={false} width={40} />
          <Tooltip cursor={{ fill: c.grid, opacity: 0.4 }} isAnimationActive={false}
            content={({ active, payload, label }) => active && payload?.length ? (
              <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
                <p className="font-semibold">{label}</p>
                {payload.map((p) => <p key={String(p.dataKey)} className="text-ink-2">{p.name}: <b className="text-ink tabular">{Number(p.value).toFixed(3)} °C</b></p>)}
              </div>
            ) : null} />
          <Legend wrapperStyle={{ fontSize: 12 }} formatter={(v: string) => <span style={{ color: c.ink }}>{v}</span>} />
          <Bar dataKey="kalman" name={r.models[0]} fill={c.base} radius={[4, 4, 0, 0]} isAnimationActive={false}>
            <LabelList dataKey="kalman" position="top" fill={c.axis} fontSize={11} formatter={(v: unknown) => Number(v).toFixed(2)} />
          </Bar>
          <Bar dataKey="ours" name={r.models[1]} fill={c.ours} radius={[4, 4, 0, 0]} isAnimationActive={false}>
            <LabelList dataKey="ours" position="top" fill={c.ink} fontSize={11} fontWeight={700} formatter={(v: unknown) => Number(v).toFixed(2)} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// Categorical slots 1-3 from the dataviz palette (validated together for up to 3 series).
const SERIES = { light: ["#2a78d6", "#eb6834", "#1baf7a"], dark: ["#3987e5", "#d95926", "#199e70"] };

function TwinsPanel({ twins }: { twins: TwinReport | null }) {
  const dark = useIsDark();
  const colors = dark ? SERIES.dark : SERIES.light;
  const axis = "#898781";
  const grid = dark ? "#2c2c2a" : "#e1e0d9";
  if (!twins || !twins.available) {
    return (
      <Card title="Personal twins: learning each worker">
        <p className="text-sm text-muted">Twins aren&apos;t trained yet. This panel fills in once the ML session serves GET /api/twins.</p>
      </Card>
    );
  }
  const days = twins.workers[0]?.days.map((d) => d.day) ?? [];
  const rows = days.map((day) => Object.fromEntries([["day", day], ...twins.workers.map((w) => [w.worker_id, w.days.find((d) => d.day === day)?.hr_rmse_bpm ?? null])]));
  return (
    <Card title="Personal twins: learning each worker" right={twins.mock ? <span className="rounded bg-warn-bg px-1.5 py-0.5 font-bold text-warn-ink">MOCK DATA</span> : "heart rate prediction error, per day"}>
      <p className="mb-2 text-sm text-ink-2">
        Each worker&apos;s twin adapts after every shift using heart rate only (core temp is never measured live).
        An update is kept only if it predicts the end of the shift better. {twins.note}
      </p>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={grid} vertical={false} />
            <XAxis dataKey="day" stroke={axis} tick={{ fill: axis, fontSize: 11 }} tickLine={false} tickFormatter={(d: number) => `day ${d}`} />
            <YAxis stroke={axis} tick={{ fill: axis, fontSize: 11 }} axisLine={false} tickLine={false} width={40} tickFormatter={(v: number) => `${v}`} label={{ value: "bpm", angle: -90, position: "insideLeft", fill: axis, fontSize: 10 }} />
            <Tooltip isAnimationActive={false} cursor={{ stroke: axis }}
              content={({ active, payload, label }) => active && payload?.length ? (
                <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
                  <p className="font-semibold">Day {label}</p>
                  {payload.map((p) => <p key={String(p.dataKey)} className="text-ink-2">{p.name}: <b className="text-ink tabular">{Number(p.value).toFixed(1)} bpm</b></p>)}
                </div>
              ) : null} />
            <Legend wrapperStyle={{ fontSize: 12 }} formatter={(v: string) => <span style={{ color: dark ? "#f0efec" : "#0b0b0b" }}>{v}</span>} />
            {twins.workers.map((w, i) => (
              <Line key={w.worker_id} dataKey={w.worker_id} name={w.name} stroke={colors[i % 3]} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
