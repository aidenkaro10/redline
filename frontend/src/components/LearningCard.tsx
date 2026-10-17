"use client";

/**
 * "Learning your body": the personal offset learned from thermometer spot checks.
 *
 * The demo centerpiece: a judge takes one ear-thermometer reading, types it in,
 * and watches the system personalize (the card updates and the core temp chart shifts).
 *
 * Math lives in the backend (backend/models/personal_offset.py):
 *   miss = model estimate - thermometer;  offset = sum(misses) / (checks + 2), capped ±0.6 °C
 * offset_c > 0 means the model reads this worker hotter than they really are,
 * so "your body vs average" is -offset_c.
 */

import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { OffsetUpdate } from "@/lib/contract";
import { apiPost, USING_MOCK } from "@/lib/live";
import { sendSpotCheck } from "@/lib/spotcheck";
import { useIsDark } from "./Nav";

/** "+0.12 °C hotter than average" / "0.08 °C cooler than average" / "same as average". */
export function bodyText(offsetC: number) {
  const body = -offsetC;
  if (Math.abs(body) < 0.005) return "Same as the average person";
  return `${body > 0 ? "+" : "−"}${Math.abs(body).toFixed(2)} °C ${body > 0 ? "hotter" : "cooler"} than average`;
}

export function LearningCard({ workerId, name, offset, tone = "surface" }: {
  workerId: string; name: string; offset: OffsetUpdate | undefined; tone?: "surface" | "dark-glass";
}) {
  const checks = offset?.checks ?? 0;
  const conf = offset?.confidence ?? 0;
  const glass = tone === "dark-glass";
  const first = name.split(" ")[0];
  return (
    <div className={glass ? "rounded-2xl bg-black/25 p-4" : "rounded-xl border border-line bg-surface p-4"}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className={`text-xs font-bold uppercase tracking-widest ${glass ? "opacity-80" : "text-accent"}`}>Learning your body</p>
          <p className="mt-1 text-2xl font-bold leading-tight">{checks ? bodyText(offset!.offset_c) : "Not personalized yet"}</p>
          <p className={`text-sm ${glass ? "opacity-90" : "text-ink-2"}`}>
            {checks
              ? `${first}'s estimates now include this. Learned from ${checks} spot check${checks === 1 ? "" : "s"}.`
              : `Using the average person. One ear or mouth thermometer reading starts learning ${first}'s body.`}
          </p>
        </div>
      </div>

      {/* Confidence: how much we trust this person's own history vs the average person */}
      <div className="mt-3">
        <div className={`flex justify-between text-xs ${glass ? "opacity-80" : "text-muted"}`}>
          <span>Confidence</span>
          <span className="tabular">{Math.round(conf * 100)}% own history · {checks} check{checks === 1 ? "" : "s"}</span>
        </div>
        <div className={`mt-1 h-2.5 overflow-hidden rounded-full ${glass ? "bg-white/20" : "bg-surface-2"}`}>
          <div className={`h-full rounded-full transition-[width] duration-700 ${glass ? "bg-white" : "bg-accent"}`} style={{ width: `${Math.round(conf * 100)}%` }} />
        </div>
      </div>

      {offset && offset.history.length > 1 && !glass && <OffsetHistory offset={offset} />}

      {offset?.last_check && (
        <p className={`mt-3 text-xs ${glass ? "opacity-90" : "text-ink-2"}`}>
          Last reading <b className="tabular">{offset.last_check.thermometer_c.toFixed(1)} °C</b> vs model <span className="tabular">{offset.last_check.model_estimate_c.toFixed(2)} °C</span>
          {offset.last_check.accepted ? " · used" : " · ignored (too far off, probably a bad reading)"}
        </p>
      )}

      <SpotCheckForm workerId={workerId} glass={glass} />
      <p className={`mt-2 text-[11px] ${glass ? "opacity-70" : "text-muted"}`}>Capped at ±0.6 °C. It never changes the safety limits.</p>
    </div>
  );
}

/** How the learned offset changed with each check (one step per check). */
function OffsetHistory({ offset }: { offset: OffsetUpdate }) {
  const dark = useIsDark();
  const c = dark ? { line: "#3987e5", grid: "#2c2c2a", axis: "#898781" } : { line: "#2a78d6", grid: "#e1e0d9", axis: "#898781" };
  const rows = offset.history.map((h) => ({ checks: h.checks, body: Math.round(-h.offset_c * 1000) / 1000, conf: h.confidence }));
  const span = Math.max(0.3, ...rows.map((r) => Math.abs(r.body))) + 0.05;
  return (
    <div className="mt-3 h-28 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={c.grid} vertical={false} />
          <XAxis dataKey="checks" type="number" domain={[0, "dataMax"]} allowDecimals={false} stroke={c.axis} tick={{ fill: c.axis, fontSize: 10 }} tickLine={false}
            label={{ value: "spot checks", position: "insideBottomRight", offset: -2, fill: c.axis, fontSize: 10 }} />
          <YAxis domain={[-span, span]} stroke={c.axis} tick={{ fill: c.axis, fontSize: 10 }} tickFormatter={(v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`} width={34} axisLine={false} tickLine={false} />
          <ReferenceLine y={0} stroke={c.axis} strokeDasharray="3 3" label={{ value: "average person", position: "insideTopLeft", fill: c.axis, fontSize: 10 }} />
          <Line type="stepAfter" dataKey="body" stroke={c.line} strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
          <Tooltip isAnimationActive={false} cursor={{ stroke: c.axis }}
            content={({ active, payload }) => {
              const r = (active && payload?.[0]?.payload) as { checks: number; body: number; conf: number } | undefined;
              if (!r) return null;
              return <div className="rounded-md border border-line bg-surface px-2 py-1 text-xs shadow"><b>{r.checks} check{r.checks === 1 ? "" : "s"}</b>: {bodyText(-r.body)} · {Math.round(r.conf * 100)}% confident</div>;
            }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** "Enter ear/oral thermometer reading" -> POST /api/spot-check {worker_id, thermometer_c}. */
export function SpotCheckForm({ workerId, glass }: { workerId: string; glass?: boolean }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;                       // a double tap sends it once
    setBusy(true);
    try {
      const r = await sendSpotCheck(workerId, value, apiPost, bodyText);
      setMsg({ ok: r.accepted, text: r.text });
      if (r.accepted) setValue("");
    } finally {
      setBusy(false);
    }
  };

  const sample = async () => {
    const r = await apiPost<{ thermometer_c?: number }>("/demo/sample-thermometer", { worker_id: workerId });
    if (r.thermometer_c) setValue(r.thermometer_c.toFixed(1));
  };

  return (
    <form onSubmit={submit} className="mt-3">
      <label className={`text-xs font-semibold ${glass ? "" : "text-ink-2"}`} htmlFor={`spot-${workerId}`}>Enter ear/oral thermometer reading</label>
      <div className="mt-1 flex gap-2">
        <div className="relative flex-1">
          <input id={`spot-${workerId}`} inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} placeholder="37.2"
            className={`min-h-11 w-full rounded-lg border px-3 py-2 pr-9 text-base tabular ${glass ? "border-white/40 bg-white/15 text-inherit placeholder:text-white/50" : "border-line bg-surface"}`} />
          <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm opacity-60">°C</span>
        </div>
        <button disabled={busy || !value.trim()} className={`min-h-11 rounded-lg px-4 py-2 text-sm font-bold disabled:opacity-50 ${glass ? "bg-white text-black" : "bg-accent text-accent-ink"}`}>
          {busy ? "…" : "Add"}
        </button>
      </div>
      {USING_MOCK && (
        <button type="button" onClick={sample} className={`mt-1 inline-flex min-h-11 items-center text-xs underline ${glass ? "opacity-80" : "text-accent"}`}>
          Mock: fill in a sample reading
        </button>
      )}
      {msg && <p className={`mt-1.5 text-xs font-medium ${glass ? "" : msg.ok ? "text-ok-ink" : "text-danger-ink"}`}>{msg.text}</p>}
    </form>
  );
}
