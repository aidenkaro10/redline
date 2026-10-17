"use client";

/**
 * Results: every model next to the Kalman baseline (CLAUDE.md rule), on people
 * the model never trained on. Plus personalization, the planner's safety result and figures.
 *
 * Data: GET /api/results. If the backend doesn't serve
 * results yet, we show the numbers bundled from docs/results.md.
 */

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Nav, useIsDark } from "@/components/Nav";
import { Card } from "@/components/ui";
import type { MetricRow, ModelResults } from "@/lib/contract-extra";
import { useFetch } from "@/lib/live";
import { RESULTS as BUNDLED } from "@/lib/results-data";

export default function ResultsPage() {
  const { data, error } = useFetch<ModelResults>("/results");
  // Show the bundled numbers right away; if the backend serves /api/results, use those instead.
  const r = data ?? BUNDLED;

  const groups = [...new Set(r.rows.map((x) => x.group))];
  return (
    <>
    <Nav />
    <main className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6">
      <div>
        <p className="text-xs font-bold uppercase tracking-wider text-accent">Results</p>
        <h1 className="text-3xl font-bold tracking-tight">Our model vs. the Army&apos;s Kalman filter</h1>
        <p className="mt-1 text-sm text-ink-2">{r.split} Updated {r.updated}.{!data && error && " (Bundled copy of docs/results.md: this backend doesn't serve /api/results yet.)"}</p>
      </div>

      <section className="grid gap-4 lg:grid-cols-3">
        <Headline label="Forecast error 20 min ahead" base={findRow(r, "+20 min RMSE")?.values[0]} ours={findRow(r, "+20 min RMSE")?.values[r.best]} unit="°C" />
        {/* The net's own win is warning earlier than the best simple baseline (catch rate ties at 88%).
            Not red: on the test people our red alerts didn't beat that baseline (9/10 vs 10/10). */}
        <Headline label="Heat warning, minutes ahead" unit="min" higherIsBetter
          base={findRow(r, "Yellow warning lead time")?.values[1]} ours={findRow(r, "Yellow warning lead time")?.values[r.best]} baseLabel="Simple trend"
          note={`${findRow(r, "Yellow warnings caught")?.values[r.best] ?? "--"}% caught, fewer false alarms: ${findRow(r, "Yellow false alarms")?.values[r.best]?.toFixed(2) ?? "--"} vs ${findRow(r, "Yellow false alarms")?.values[1]?.toFixed(2) ?? "--"}/hr`} />
        {r.planner
          ? <Headline label="Plans that broke a safety rule" base={r.planner.rows.find((x) => x.name.startsWith("AI alone"))?.runs_with_violations_pct} ours={r.planner.rows.find((x) => x.name.startsWith("Our agent"))?.runs_with_violations_pct} unit="%" baseLabel="Same AI without our rule checker" hideChange />
          : <Headline label="Core temp error now" base={r.rows[0].values[0]} ours={r.rows[0].values[r.best]} unit="°C" />}
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

      {r.personalization && (
        <Card title="Learning each body: thermometer spot checks" right="lower is better" bodyClass="p-0">
          <p className="px-4 pt-3 text-sm text-ink-2">{r.personalization.source} Most of the remaining error is a steady per-person offset, and heart rate can&apos;t see it. One ear-thermometer reading can.</p>
          <SimpleTable
            head={["", "Error now (°C)", "Error 20 min ahead (°C)"]}
            rows={r.personalization.rows.map((x) => [x.name, x.now_rmse_c.toFixed(3), x.plus20_rmse_c.toFixed(3)])}
            bestCol={[1, 2]} lowerIsBetter
          />
        </Card>
      )}

      {r.planner && (
        <Card title="The planner: AI suggests, the rule checker decides" bodyClass="p-0">
          <p className="px-4 pt-3 text-sm text-ink-2">{r.planner.source}</p>
          <SimpleTable
            head={["Planner", "Runs with a safety violation", "Violations per run", "Peak 90th pct core (°C)", "Work done"]}
            rows={r.planner.rows.map((x) => [x.name, `${x.runs_with_violations_pct}%`, x.violations_per_run.toFixed(1), x.peak_p90_c.toFixed(2), `${Math.round(x.throughput * 100)}%`])}
            highlightRow={r.planner.rows.findIndex((x) => x.name.startsWith("Our agent"))}
          />
          <p className="px-4 pb-3 text-xs text-muted">Same AI with and without the code rule checker: 92% of AI-alone runs break a safety rule, 0% with the checker. The AI never makes a safety call.</p>
        </Card>
      )}

      {r.figures && r.figures.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-bold">Figures</h2>
          <div className="grid gap-4 md:grid-cols-2">
            {r.figures.map((f) => <Figure key={f.file} {...f} />)}
          </div>
        </section>
      )}

    </main>
    </>
  );
}

function findRow(r: ModelResults, name: string) {
  return r.rows.find((x) => x.name === name);
}

function Headline({ label, base, ours, unit, higherIsBetter, baseLabel = "Kalman", hideChange, note }: { label: string; base?: number | null; ours?: number | null; unit: string; higherIsBetter?: boolean; baseLabel?: string; hideChange?: boolean; note?: string }) {
  // Percent metrics (catch rates) compare in points (88% vs 12% = +76 pts); the rest as a relative change.
  const pts = unit === "%";
  const change = !hideChange && base && ours !== null && ours !== undefined ? (pts ? ours - base : ((ours - base) / base) * 100) : null;
  const good = change !== null && (higherIsBetter ? change > 0 : change < 0);
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <p className="text-sm text-ink-2">{label}</p>
      <p className="mt-1 text-4xl font-bold">{ours ?? "--"}<span className="ml-1 text-base font-medium text-ink-2">{unit}</span></p>
      <p className="mt-1 text-sm">
        <span className="text-muted">{baseLabel}: {base ?? "--"} {unit}</span>
        {change !== null && <span className={`ml-2 font-semibold ${good ? "text-ok-ink" : "text-danger-ink"}`}>{change > 0 ? "+" : ""}{Math.round(change)}{pts ? " pts" : "%"}</span>}
      </p>
      {note && <p className="mt-1 text-xs text-ink-2">{note}</p>}
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
  const rows = r.rmse_by_horizon.map((h) => ({ name: h.minutes_ahead === 0 ? "Now" : `+${h.minutes_ahead} min`, kalman: h.values[0], ours: h.values[r.best] }));
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
          <Bar dataKey="ours" name={r.models[r.best]} fill={c.ours} radius={[4, 4, 0, 0]} isAnimationActive={false}>
            <LabelList dataKey="ours" position="top" fill={c.ink} fontSize={11} fontWeight={700} formatter={(v: unknown) => Number(v).toFixed(2)} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function SimpleTable({ head, rows, bestCol, lowerIsBetter, highlightRow }: {
  head: string[]; rows: string[][]; bestCol?: number[]; lowerIsBetter?: boolean; highlightRow?: number;
}) {
  // Winner per numeric column (for the personalization table).
  const best = new Map<number, number>();
  for (const c of bestCol ?? []) {
    const vals = rows.map((r) => Number(r[c]));
    const pick = lowerIsBetter ? Math.min(...vals) : Math.max(...vals);
    best.set(c, vals.indexOf(pick));
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            {head.map((h, i) => <th key={i} className={`px-4 py-2 font-medium ${i ? "text-right" : ""}`}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} className={`border-b border-line last:border-0 ${ri === highlightRow ? "bg-ok-bg" : ""}`}>
              {r.map((v, ci) => (
                <td key={ci} className={`px-4 py-2 ${ci ? "text-right tabular" : ""} ${ri === highlightRow || best.get(ci) === ri ? "font-bold" : "text-ink-2"}`}>
                  {v}{best.get(ci) === ri && " ✓"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One result figure. The PNGs come from docs/figures (copied to /figures when the dev server starts). */
function Figure({ file, title, caption }: { file: string; title: string; caption: string }) {
  const [missing, setMissing] = useState(false);
  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-surface">
      {missing ? (
        <div className="grid h-56 place-items-center bg-surface-2 p-4 text-center text-sm text-muted">
          Figure {file} not found yet. It shows up once docs/figures (session H) is merged; restart npm run dev.
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={`/figures/${file}`} alt={title} onError={() => setMissing(true)} className="w-full bg-white" />
      )}
      <figcaption className="p-3">
        <p className="text-sm font-semibold">{title}</p>
        <p className="text-xs text-ink-2">{caption}</p>
      </figcaption>
    </figure>
  );
}
