"use client";

/**
 * Demo controls, one click each (CLAUDE.md rule): scripted demo, test hazard
 * alerts, man-down, replay on/off, replay speed, demo threshold, demo heart rate.
 */

import { useEffect, useMemo, useState } from "react";

import { useCrew } from "@/lib/crew";
import { demoActions, SPEEDS, type Request } from "@/lib/demo-actions";
import { HR_MODES, HR_PATH, hrPicker, readHrMode, type HeartRateMode } from "@/lib/heart-rate";
import { apiGet, apiPost, useLive } from "@/lib/live";

/** `target`: who the man-down / SOS buttons act on (defaults to the first worker). */
export function DemoControls({ target }: { target?: string }) {
  const { demo, workers } = useLive();
  const { firstWorkerId } = useCrew();
  const [busy, setBusy] = useState<string | null>(null);
  if (!demo) return null;
  const who = target && workers[target] ? target : firstWorkerId;
  const whoName = who ? workers[who]?.name.split(" ")[0] : "";

  const call = async (key: string, [path, body]: Request) => {
    setBusy(key);
    try { await apiPost(path, body); } finally { setBusy(null); }
  };
  const running = demo.scenario.running;

  return (
    <div className="rounded-xl border border-dashed border-line bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-xs font-bold uppercase tracking-wider text-muted">Demo</span>

        <Btn primary onClick={() => call("scenario", demoActions.scenario(demo))} busy={busy === "scenario"}>
          {running ? "■ Stop scripted demo" : "▶ Play scripted demo"}
        </Btn>
        <Btn onClick={() => call("lightning", demoActions.testLightning())} busy={busy === "lightning"}>Test lightning</Btn>
        <Btn onClick={() => call("flood", demoActions.testFloodFire())} busy={busy === "flood"}>Test flood alert</Btn>
        <Btn onClick={() => call("mandown", demoActions.manDown(who))} busy={busy === "mandown"}>Man-down: {whoName}</Btn>
        <Btn onClick={() => call("sos", demoActions.sos(who))} busy={busy === "sos"}>SOS: {whoName}</Btn>
        <Btn onClick={() => call("clear", demoActions.clearHazards())} busy={busy === "clear"}>Clear hazards</Btn>

        <span className="mx-1 hidden h-5 w-px bg-line sm:inline-block" />

        <Segmented
          label="Data"
          options={[{ v: "live", t: "Live" }, { v: "replay", t: "Replay" }]}
          value={demo.source}
          onChange={(v) => call("replay", demoActions.replay(v === "replay"))}
        />
        <Segmented
          label="Speed"
          options={SPEEDS.map((s) => ({ v: String(s), t: `${s}x` }))}
          value={String(demo.speed)}
          disabled={demo.source !== "replay"}
          onChange={(v) => call("speed", demoActions.speed(Number(v)))}
        />
        <button
          onClick={() => call("thr", demoActions.threshold(demo))}
          className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold ${demo.demo_threshold ? "border-warn bg-warn-bg text-warn-ink" : "border-line text-ink-2 hover:bg-surface-2"}`}
          title="Lowers the heat lines so a judge doesn't wait 20 minutes. Clearly labeled while on."
        >
          Demo threshold: {demo.demo_threshold ? `ON (${demo.thresholds.warning} / ${demo.thresholds.danger} °C)` : "off"}
        </button>
        <DemoHeartRate />
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

/** Demo heart rate (the band has no pulse sensor). Asks the backend which mode is on, so it survives a reload. */
function DemoHeartRate() {
  const [mode, setMode] = useState<HeartRateMode | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    apiGet(HR_PATH).then((r) => setMode(readHrMode(r))).catch(() => {});   // older backend: no mode shown
  }, []);
  const pick = useMemo(() => {
    const send = hrPicker((path, body) => apiPost(path, body), setMode);
    return async (m: HeartRateMode) => { setBusy(true); try { await send(m); } finally { setBusy(false); } };
  }, []);
  return <HeartRateButtons mode={mode} busy={busy} onPick={pick} />;
}

/** The three heart rate buttons. `mode` null = not known yet (none shows as on). */
export function HeartRateButtons({ mode, busy, onPick }: { mode: HeartRateMode | null; busy?: boolean; onPick: (m: HeartRateMode) => void }) {
  return (
    <div className="flex items-center gap-1.5" title="The band has no pulse sensor. These feed a made-up heart rate, clearly tagged as demo input.">
      <span className="text-xs text-muted">Heart rate</span>
      <div className="flex rounded-lg border border-line p-0.5">
        {HR_MODES.map((o) => (
          <button key={o.mode} disabled={busy} onClick={() => onPick(o.mode)} aria-pressed={o.mode === mode}
            className={`rounded-md px-2 py-1 text-xs font-semibold disabled:opacity-60 ${o.mode === mode ? (o.mode === "off" ? "bg-surface-2 text-ink shadow-sm" : "bg-warn-bg text-warn-ink shadow-sm") : "text-ink-2"}`}>
            {o.label}
          </button>
        ))}
      </div>
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
            aria-pressed={o.v === value}
            className={`rounded-md px-2 py-1 text-xs font-semibold ${o.v === value ? "bg-surface-2 text-ink shadow-sm" : "text-ink-2"}`}>
            {o.t}
          </button>
        ))}
      </div>
    </div>
  );
}
