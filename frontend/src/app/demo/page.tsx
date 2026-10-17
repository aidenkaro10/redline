"use client";

/**
 * Demo view for judges: one worker's live heart rate, skin temp, and core temp
 * with the shaded forecast band and the "why" line.
 *
 * This page connects as view=demo, the only crew-side view allowed to see raw
 * biometrics. It's clearly labeled, for the wearer who agreed to show it.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AlertFeed } from "@/components/AlertFeed";
import { CoreChart, CoreLegend, Sparkline } from "@/components/Charts";
import { DemoControls } from "@/components/DemoControls";
import { Nav } from "@/components/Nav";
import { LearningCard } from "@/components/LearningCard";
import { Card, StatusBadge, fmtClockSec } from "@/components/ui";
import type { BandCommand } from "@/lib/contract";
import { useLive, useSimNow } from "@/lib/live";
import { useCrew, useSites } from "@/lib/crew";

export default function DemoPage() {
  return <Suspense><DemoView /></Suspense>;
}

function DemoView() {
  const params = useSearchParams();
  const router = useRouter();
  const live = useLive();
  const now = useSimNow(500);
  const crew = useCrew();
  const { byId } = useSites();
  const id = params.get("worker") ?? crew.firstWorkerId ?? "";
  const w = live.workers[id];
  const hist = live.history[id] ?? { vitals: [], estimates: [] };
  const lastV = hist.vitals[hist.vitals.length - 1];
  const lastE = hist.estimates[hist.estimates.length - 1];
  const band = [...live.band].reverse().filter((b) => b.worker_id === id);
  const at20 = lastE?.forecast[19];

  return (
    <>
    <Nav />
    <main className="mx-auto flex max-w-[1500px] flex-col gap-4 px-4 py-4">
      <DemoControls target={id} />

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-accent">Demo view · raw biometrics shown with the wearer&apos;s consent</p>
          <h1 className="text-2xl font-bold tracking-tight">{w?.name ?? "…"}</h1>
          <p className="text-sm text-ink-2">{w?.role} · {w ? byId[w.site_id]?.name ?? w.site_id : ""}</p>
        </div>
        <div className="flex flex-wrap gap-1">
          {crew.workers.map((x) => (
            <button key={x.worker_id} onClick={() => router.replace(`/demo?worker=${x.worker_id}`)}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold ${x.worker_id === id ? "bg-ink text-page" : "border border-line text-ink-2 hover:bg-surface-2"}`}>
              {x.name.split(" ")[0]}
            </button>
          ))}
        </div>
      </div>

      {/* Big numbers */}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Status">{w ? <StatusBadge status={w.status} big /> : "--"}<p className="mt-1 text-xs text-ink-2">{w?.status_reason}</p></Stat>
        <Stat label="Heart rate" value={lastV?.hr != null ? Math.round(lastV.hr) : "--"} unit="bpm" />
        <Stat label="Skin temp" value={lastV?.skin_temp?.toFixed(1) ?? "--"} unit="°C" />
        <Stat label="Core temp now" value={lastE?.core.mean.toFixed(2) ?? "--"} unit="°C"
          sub={lastE ? `range ${lastE.core.p10.toFixed(1)}–${lastE.core.p90.toFixed(1)} · Kalman ${lastE.kalman.toFixed(2)}${lastE.offset_applied_c ? ` · personal ${lastE.offset_applied_c > 0 ? "−" : "+"}${Math.abs(lastE.offset_applied_c).toFixed(2)}` : ""}` : ""} />
        <Stat label="In 20 min (90th pct)" value={at20?.p90.toFixed(2) ?? "--"} unit="°C" sub={at20 ? `median ${at20.p50.toFixed(2)}` : ""} />
        <Stat label="Predicted danger" value={w ? (w.minutes_to_danger === null ? "none" : w.minutes_to_danger === 0 ? "now" : w.minutes_to_danger) : "--"}
          unit={w?.minutes_to_danger ? "min" : ""} sub="within the next 30 min" />
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2" title="Core temperature: now and the next 30 minutes" right={lastE?.model}>
          <CoreChart estimates={hist.estimates} now={now} height={360} />
          <div className="mt-3 flex flex-col gap-2">
            <p className="rounded-lg bg-surface-2 px-3 py-2 text-base font-medium">
              <span className="mr-2 text-xs font-bold uppercase tracking-wider text-muted">Why</span>
              {lastE ? lastE.why ?? <span className="text-muted">No explanation for this minute.</span> : "…"}
            </p>
            <CoreLegend />
            <p className="text-xs text-muted">
              Alerts use the cautious 90th percentile: yellow when the 20-minute forecast crosses the warning line,
              red when the estimate or the 10-minute forecast crosses the danger line.
              {lastE?.thresholds.demo && " Demo thresholds are ON (lowered so judges don't wait 20 minutes)."}
            </p>
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          {w && <LearningCard workerId={id} name={w.name} offset={live.offsets[id]} />}
          <Card title="Heart rate" right="Apple Watch via Pulsoid, last 15 min">
            <Sparkline vitals={hist.vitals} field="hr" now={now} unit="bpm" height={110} pad={4} />
          </Card>
          <Card title="Skin temperature" right="Band diode, last 15 min">
            <Sparkline vitals={hist.vitals} field="skin_temp" now={now} unit="°C" height={110} digits={1} pad={0.3} />
          </Card>
          <Card title="Band" right={lastV ? `tilt ${lastV.tilt} · activity ${lastV.activity}/s` : ""}>
            <BandPanel cmds={band} connected={w?.band_connected ?? true}
              sosActive={live.mandown?.worker_id === id && live.mandown.reason === "sos" && now - live.mandown.fell_at < 10} />
          </Card>
        </div>
      </section>

      <Card title="Alerts and log">
        <AlertFeed />
      </Card>

      <p className="text-center text-xs text-muted">
        Crew leads never see this page&apos;s numbers. <Link className="text-accent hover:underline" href="/">Back to the crew dashboard</Link>
      </p>
    </main>
    </>
  );
}

function Stat({ label, value, unit, sub, children }: { label: string; value?: React.ReactNode; unit?: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-3">
      <p className="text-xs text-muted">{label}</p>
      {children ?? (
        <p className="mt-0.5 text-3xl font-bold tabular leading-tight">
          {value}<span className="ml-1 text-sm font-medium text-ink-2">{unit}</span>
        </p>
      )}
      {sub && <p className="mt-0.5 text-xs text-ink-2 tabular">{sub}</p>}
    </div>
  );
}

/**
 * The band has ONE status light. What it shows for each command (docs/firmware-contract.md).
 * `anim` is a CSS class from globals.css that mimics the real blink pattern.
 */
const LED: Record<string, { color: string; anim: string; light: string; sound: string }> = {
  "BUZZ:OFF": { color: "#22c55e", anim: "", light: "Solid green: all clear", sound: "Silent" },
  "BUZZ:HEAT_YELLOW": { color: "#facc15", anim: "", light: "Solid yellow: heat warning", sound: "One long beep" },
  "BUZZ:HEAT_RED": { color: "#ef4444", anim: "led-flash-2", light: "Flashing red: heat danger", sound: "Long beep every 10 s until a short press" },
  "BUZZ:LIGHTNING": { color: "#ffffff", anim: "led-flash-5", light: "Rapid white flashes: lightning", sound: "Rapid short beeps every 10 s" },
  "BUZZ:FLOOD_FIRE": { color: "#3b82f6", anim: "", light: "Solid blue: flood or fire weather", sound: "Two short beeps" },
  "BUZZ:MANDOWN": { color: "#ef4444", anim: "led-breathe", light: "Red breathing pulse: man-down check", sound: "Continuous beeping until a short press" },
  SOS: { color: "#a855f7", anim: "led-blink", light: "Blinking purple: SOS sent (band does this itself)", sound: "3 beeps" },
  NO_LINK: { color: "#22d3ee", anim: "led-blink-slow", light: "Slow cyan blink: no command for 60 s (check the cable)", sound: "Silent" },
};

function BandPanel({ cmds, sosActive, connected }: { cmds: BandCommand[]; sosActive: boolean; connected: boolean }) {
  const cur = cmds[0];
  const key = !connected ? "NO_LINK" : sosActive ? "SOS" : cur?.command ?? "BUZZ:OFF";
  const led = LED[key] ?? LED["BUZZ:OFF"];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-black" title="The band's single status light">
          <span className={`h-6 w-6 rounded-full ${led.anim}`} style={{ background: led.color, boxShadow: `0 0 14px 3px ${led.color}` }} />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold">{led.light}</p>
          <p className="text-xs text-ink-2">{led.sound} · last command <span className="font-mono">{cur?.command ?? "BUZZ:OFF"}</span></p>
        </div>
      </div>
      <ul className="max-h-28 space-y-0.5 overflow-y-auto font-mono text-[11px] text-ink-2">
        {cmds.slice(0, 8).map((c, i) => <li key={i}><span className="text-muted">{fmtClockSec(c.at)}</span> → {c.command}</li>)}
      </ul>
    </div>
  );
}
