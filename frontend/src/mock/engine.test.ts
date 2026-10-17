/**
 * Tests for the mock backend. Run with: npm test
 *
 * They use a fake clock, so the whole 3-minute scripted demo runs in about a second.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import type { LiveEvent } from "../lib/contract.ts";
import { MockEngine } from "./engine.ts";

/** An engine with a fake wall clock we can move by hand. */
function fakeEngine() {
  let wall = 1_000_000;
  const engine = new MockEngine({ wallNow: () => wall, baseDate: new Date(2026, 9, 17, 9).getTime() });
  const run = (seconds: number) => {
    for (let i = 0; i < seconds * 2; i++) {
      wall += 0.5;
      engine.tick();
    }
  };
  return { engine, run };
}

test("the scripted demo plays end to end: yellow, red, lightning, man-down, emergency call", () => {
  const { engine, run } = fakeEngine();
  const events: LiveEvent[] = [];
  engine.subscribe({ view: "demo" }, (ev) => events.push(structuredClone(ev)));
  engine.handle("POST", ["demo", "scenario"], new URLSearchParams(), { action: "start" });

  run(4 * 60); // 4 real minutes is more than enough

  const alerts = events.flatMap((e) => (e.type === "alert" ? [e.alert] : []));
  const wearer = (k: string) => alerts.some((a) => a.kind === k && a.worker_id === "worker-1");
  assert.ok(wearer("HEAT_YELLOW"), "wearer went yellow");
  assert.ok(wearer("HEAT_RED"), "wearer went red");
  assert.ok(alerts.some((a) => a.kind === "LIGHTNING" && a.site_id === "fl-deerhaven"), "lightning alert");
  assert.ok(wearer("MANDOWN"), "man-down confirmed");

  // Yellow comes before red, and both come before the man-down.
  const first = (k: string) => alerts.find((a) => a.kind === k && a.worker_id === "worker-1")!.started_at;
  assert.ok(first("HEAT_YELLOW") < first("HEAT_RED"));
  assert.ok(first("HEAT_RED") < first("MANDOWN"));

  // The red alert got acknowledged by the scripted button press.
  assert.ok(alerts.some((a) => a.kind === "HEAT_RED" && a.acknowledged_at !== null));

  // The emergency call went through every stage and ended in demo mode.
  const calls = events.flatMap((e) => (e.type === "emergency" && e.emergency ? [e.emergency] : []));
  const stages = new Set(calls.map((c) => c.stage));
  for (const s of ["notifying", "cancel_window", "calling", "connected", "done"]) assert.ok(stages.has(s as never), `stage ${s}`);
  const last = calls[calls.length - 1];
  assert.equal(last.demo, true);
  assert.match(last.target_label, /would call 911/);
  assert.ok(last.transcript.length >= 3, "agent read the report and answered");

  // Band got the right patterns.
  const cmds = events.flatMap((e) => (e.type === "band_command" && e.band.worker_id === "worker-1" ? [e.band.command] : []));
  for (const c of ["BUZZ:HEAT_YELLOW", "BUZZ:HEAT_RED", "BUZZ:MANDOWN"]) assert.ok(cmds.includes(c), c);

  // Jake's SOS: announced, escalated straight away (no "checking"), then cancelled as a false alarm.
  const sos = events.flatMap((e) => (e.type === "sos" ? [e.sos] : []));
  assert.equal(sos.length, 1);
  assert.equal(sos[0].worker_id, "worker-2");
  assert.ok(alerts.some((a) => a.kind === "SOS" && a.worker_id === "worker-2"), "SOS alert");
  assert.ok(calls.some((c) => c.worker_id === "worker-2" && c.stage === "cancelled"), "SOS call cancelled by crew lead");

  // And the scenario says it's finished.
  assert.equal(engine.demo.scenario.running, false);
});

test("privacy: the crew view never receives raw vitals or core temp", () => {
  const { engine, run } = fakeEngine();
  const crew: LiveEvent[] = [];
  engine.subscribe({ view: "crew" }, (ev) => crew.push(ev));
  run(30);
  assert.ok(!crew.some((e) => e.type === "vitals" || e.type === "estimate"));
  const snap = crew[0];
  assert.equal(snap.type, "snapshot");
  if (snap.type === "snapshot") assert.deepEqual(snap.history, {});
  // And REST refuses too.
  const res = engine.handle("GET", ["workers", "worker-1", "history"], new URLSearchParams("view=crew"), {});
  assert.equal(res.status, 403);
});

test("worker view only gets its own vitals", () => {
  const { engine, run } = fakeEngine();
  const evs: LiveEvent[] = [];
  engine.subscribe({ view: "worker", worker_id: "worker-2" }, (ev) => evs.push(ev));
  run(30);
  const ids = new Set(evs.flatMap((e) => (e.type === "vitals" ? [e.vitals.worker_id] : [])));
  assert.deepEqual([...ids], ["worker-2"]);
});

test("button press cancels a man-down check before it escalates", () => {
  const { engine, run } = fakeEngine();
  engine.handle("POST", ["demo", "mandown"], new URLSearchParams(), { worker_id: "worker-3" });
  run(12);
  assert.equal(engine.mandown?.stage, "checking");
  engine.handle("POST", ["workers", "worker-3", "button"], new URLSearchParams(), {});
  assert.equal(engine.mandown, null);
  run(30);
  assert.equal(engine.emergency, null, "no call after a cancel");
});

test("crew lead can cancel the emergency call inside the cancel window", () => {
  const { engine, run } = fakeEngine();
  engine.handle("POST", ["demo", "mandown"], new URLSearchParams(), { worker_id: "worker-4" });
  run(30); // still 10 s + escalate 15 s + notify 3 s
  assert.equal(engine.emergency?.stage, "cancel_window");
  const res = engine.handle("POST", ["emergency", engine.emergency!.id, "cancel"], new URLSearchParams(), {});
  assert.deepEqual(res.json, { ok: true });
  assert.equal(engine.emergency?.stage, "cancelled");
});

test("lightning: stop work for 30 min after the last strike within 10 miles, then all clear", () => {
  const { engine, run } = fakeEngine();
  engine.handle("POST", ["demo", "test-alert"], new URLSearchParams(), { kind: "LIGHTNING", site_id: "fl-kelly" });
  const l = () => engine.siteStatus("fl-kelly").hazards.find((h) => h.kind === "lightning")!;
  assert.equal(l().stop_work, true);
  engine.handle("POST", ["demo", "replay"], new URLSearchParams(), { on: true, speed: 30 });
  run(55); // 27.5 replay minutes
  assert.equal(l().stop_work, true);
  run(10); // past 30 minutes
  assert.equal(l().stop_work, false);
});

test("there are 50+ sites, each with all six hazards and a 0-10 index", () => {
  const { engine } = fakeEngine();
  const snap = engine.snapshot({ view: "crew" });
  assert.ok(snap.sites.length >= 50);
  for (const s of snap.sites) {
    assert.equal(s.hazards.length, 6);
    assert.ok(s.index_tomorrow >= 0 && s.index_tomorrow <= 10);
  }
});

test("starting the scripted demo keeps the real timer running", () => {
  const engine = new MockEngine();
  engine.start(1000);
  engine.handle("POST", ["demo", "scenario"], new URLSearchParams(), { action: "start" });
  assert.notEqual(engine.timer, null);
  engine.stop();
});

test("SOS escalates right away, never reaches the band as a command, and isn't doubled", () => {
  const { engine, run } = fakeEngine();
  const events: LiveEvent[] = [];
  engine.subscribe({ view: "demo" }, (ev) => events.push(structuredClone(ev)));
  const res = engine.handle("POST", ["workers", "worker-5", "sos"], new URLSearchParams(), {});
  assert.deepEqual(res.json, { ok: true, action: "escalated" });
  assert.equal(engine.mandown?.reason, "sos");
  assert.equal(engine.mandown?.stage, "confirmed");          // no "checking" stage
  assert.equal(engine.emergency?.stage, "notifying");        // escalation already started
  // A second SOS (or a man-down) for someone while an incident is active is ignored.
  const again = engine.handle("POST", ["demo", "sos"], new URLSearchParams(), { worker_id: "worker-6" });
  assert.deepEqual(again.json, { ok: true, action: "ignored_active_incident" });
  run(60);
  assert.equal(engine.emergency?.stage, "done");
  const cmds = events.flatMap((e) => (e.type === "band_command" ? [e.band.command] : []));
  assert.ok(!cmds.includes("BUZZ:SOS"), "SOS is not a band command");
  const calls = events.flatMap((e) => (e.type === "emergency" && e.emergency ? [e.emergency] : []));
  assert.match(calls[calls.length - 1].transcript[0].text, /pressed SOS/);
});
