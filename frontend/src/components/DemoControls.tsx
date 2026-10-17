"use client";

/**
 * Demo controls, one click each (CLAUDE.md rule): scripted demo, test hazard
 * alerts, man-down, replay on/off, replay speed, demo threshold.
 */

import { useState } from "react";

import { apiPost, useLive } from "@/lib/live";

const SPEEDS = [1, 5, 10, 30];

export function DemoControls() {
  const { demo } = useLive();
  const [busy, setBusy] = useState<string | null>(null);
  if (!demo) return null;

  const call = async (key: string, path: string, body: unknown = {}) => {
    setBusy(key);
    try { await apiPost(path, body); } finally { setBusy(null); }
  };
  const running = demo.scenario.running;

  return (
    <div className="rounded-xl border border-dashed border-line bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-xs font-bold uppercase tracking-wider text-muted">Demo</span>

        <Btn primary onClick={() => call("scenario", "/demo/scenario", { action: running ? "stop" : "start" })} busy={busy === "scenario"}>
          {running ? "■ Stop scripted demo" : "▶ Play scripted demo"}
        </Btn>
        <Btn onClick={() => call("lightning", "/demo/test-alert", { kind: "LIGHTNING" })} busy={busy === "lightning"}>Test lightning</Btn>
        <Btn onClick={() => call("flood", "/demo/test-alert", { kind: "FLOOD_FIRE" })} busy={busy === "flood"}>Test flood alert</Btn>
        <Btn onClick={() => call("mandown", "/demo/mandown", { worker_id: "worker-1" })} busy={busy === "mandown"}>Trigger man-down</Btn>
        <Btn onClick={() => call("clear", "/demo/clear-hazards")} busy={busy === "clear"}>Clear hazards</Btn>

        <span className="mx-1 hidden h-5 w-px bg-line sm:inline-block" />

        <Segmented
          label="Data"
          options={[{ v: "live", t: "Live" }, { v: "replay", t: "Replay" }]}
          value={demo.source}
          onChange={(v) => call("replay", "/demo/replay", { on: v === "replay", speed: v === "replay" ? 10 : 1 })}
        />
        <Segmented
          label="Speed"
          options={SPEEDS.map((s) => ({ v: String(s), t: `${s}x` }))}
          value={String(demo.speed)}
          disabled={demo.source !== "replay"}
          onChange={(v) => call("speed", "/demo/replay", { speed: Number(v) })}
        />
        <button
          onClick={() => call("thr", "/demo/threshold", { on: !demo.demo_threshold })}
          className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold ${demo.demo_threshold ? "border-warn bg-warn-bg text-warn-ink" : "border-line text-ink-2 hover:bg-surface-2"}`}
          title="Lowers the heat lines so a judge doesn't wait 20 minutes. Clearly labeled while on."
        >
          Demo threshold: {demo.demo_threshold ? `ON (${demo.thresholds.warning} / ${demo.thresholds.danger} °C)` : "off"}
        </button>
      </div>
      {(running || demo.scenario.step === "Finished") && (
        <p className="mt-1.5 text-xs text-ink-2">
          <span className="font-semibold">Script:</span> {demo.scenario.step ?? "starting"}
          {running && " · heat alerts come from the alert rules, not the script"}
        </p>
      )}
    </div>
  );
}

function Btn({ children, onClick, busy, primary }: { children: React.ReactNode; onClick: () => void; busy?: boolean; primary?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`rounded-lg px-2.5 py-1.5 text-xs font-semibold transition disabled:opacity-60 ${primary ? "bg-accent text-accent-ink hover:opacity-90" : "border border-line text-ink hover:bg-surface-2"}`}
    >
      {children}
    </button>
  );
}

function Segmented({ label, options, value, onChange, disabled }: {
  label: string; options: { v: string; t: string }[]; value: string; onChange: (v: string) => void; disabled?: boolean;
}) {
  return (
    <div className={`flex items-center gap-1.5 ${disabled ? "opacity-50" : ""}`}>
      <span className="text-xs text-muted">{label}</span>
      <div className="flex rounded-lg border border-line p-0.5">
        {options.map((o) => (
          <button key={o.v} disabled={disabled} onClick={() => onChange(o.v)}
            className={`rounded-md px-2 py-1 text-xs font-semibold ${o.v === value ? "bg-surface-2 text-ink shadow-sm" : "text-ink-2"}`}>
            {o.t}
          </button>
        ))}
      </div>
    </div>
  );
}
