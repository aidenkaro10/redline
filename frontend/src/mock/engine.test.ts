/**
 * Tests for the mock backend. Run with: npm test
 *
 * They use a fake clock, so the whole 3-minute scripted demo runs in about a second.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import type { EmergencyCall, EmergencyStatus, LiveEvent, ManDown } from "../lib/contract.ts";
import { CALL_FAILED_TEXT, callForIncident, foldEmergencyStatus } from "../lib/emergency.ts";
import { MockEngine } from "./engine.ts";

/** An engine with a fake wall clock we can move by hand. */
function fakeEngine(emergencyFormat: "call" | "status" = "call") {
  let wall = 1_000_000;
  const engine = new MockEngine({ wallNow: () => wall, baseDate: new Date(2026, 9, 17, 9).getTime(), emergencyFormat });
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

// ---------------------------------------------------------------------------
// Session D's emergency_status events, folded into one EmergencyCall
// ---------------------------------------------------------------------------

/** Build one D event. Only the fields that matter for a test need to be given. */
function d(t: number, stage: EmergencyStatus["stage"], status: EmergencyStatus["status"], more: Partial<EmergencyStatus> = {}): EmergencyStatus {
  return {
    incident_id: "inc-worker-1-1000", worker_id: "worker-1", worker_name: "Maria", timestamp: t,
    stage, status, target: null, number_masked: null, mode: "dry_run", demo_label: null, cancel_deadline: null, message: "",
    ...more,
  };
}

const EM = { target: "emergency" as const, demo_label: "Demo: would call 911", number_masked: "+1 ••• ••• 1234" };

/** Session D's "typical sequence" from docs/interfaces/emergency.md, up to (not including) the end. */
function dSequence(): EmergencyStatus[] {
  return [
    d(1000, "notify", "calling", { message: "Maria is down. Notifying crew." }),
    d(1001, "notify", "calling", { target: "crew_lead", number_masked: "+1 ••• ••• 0101" }),
    d(1002, "notify", "connected", { target: "crew_lead" }),
    d(1003, "notify", "completed", { target: "crew_lead" }),
    d(1004, "notify", "failed", { target: "safety_officer", message: "No answer" }),   // a crew call failing is NOT the 911 leg failing
    d(1005, "cancel_window", "waiting", { cancel_deadline: 1035, message: "Emergency call in 30 s unless cancelled." }),
    d(1035, "emergency_call", "calling", EM),
    d(1040, "emergency_call", "connected", EM),
  ];
}

/** Run a list of D events through the fold, the way live.tsx does. */
function fold(events: EmergencyStatus[], mandown: ManDown | null = null) {
  let call: EmergencyCall | null = null;
  const stages: string[] = [];
  for (const ev of events) {
    call = foldEmergencyStatus(call, ev, mandown);
    stages.push(call.stage);
  }
  return { call: call!, stages };
}

test("emergency_status: D's normal sequence folds into the same stages the overlay shows", () => {
  const { call, stages } = fold([...dSequence(), d(1060, "emergency_call", "completed", EM), d(1060, "done", "completed", { message: "Emergency report delivered." })]);
  assert.deepEqual([...new Set(stages)], ["notifying", "cancel_window", "calling", "connected", "done"]);
  assert.equal(call.id, "inc-worker-1-1000");
  assert.equal(call.started_at, 1000);
  assert.equal(call.cancel_deadline, 1035);
  assert.equal(call.demo, true);
  assert.equal(call.target_label, "Demo: would call 911");
  assert.equal(call.mode, "dry_run");
  assert.equal(call.outcome, "Emergency report delivered.");
  assert.equal(call.steps.length, 10, "one log line per event");
  assert.match(call.steps[1].text, /Crew lead \(\+1 ••• ••• 0101\)/);
});

test("emergency_status: a failed emergency call ends in 'failed' with the call-911 text", () => {
  const { call } = fold([...dSequence().slice(0, 7), d(1045, "emergency_call", "failed", { ...EM, message: "No answer" }), d(1045, "done", "failed", { message: "whatever" })]);
  assert.equal(call.stage, "failed");
  assert.equal(call.outcome, CALL_FAILED_TEXT);   // always the exact safety text
  assert.equal(CALL_FAILED_TEXT, "Emergency call FAILED. Call 911 yourself now.");
});

test("emergency_status: cancel, no going backwards, and a new incident starts fresh", () => {
  const cancelled = fold([...dSequence().slice(0, 6), d(1010, "done", "cancelled", { message: "Cancelled as a false alarm by crew lead." })]);
  assert.equal(cancelled.call.stage, "cancelled");
  assert.match(cancelled.call.outcome!, /false alarm/);

  // A late "calling" after "connected" doesn't drag the stage back; nothing moves it out of an ending.
  const late = fold([...dSequence(), d(1041, "emergency_call", "calling", EM)]);
  assert.equal(late.call.stage, "connected");
  const after = foldEmergencyStatus(cancelled.call, d(1011, "emergency_call", "calling", EM), null);
  assert.equal(after.stage, "cancelled");

  // A different incident_id replaces the old call.
  const next = foldEmergencyStatus(cancelled.call, d(2000, "notify", "calling", { incident_id: "inc-worker-1-2000" }), null);
  assert.equal(next.id, "inc-worker-1-2000");
  assert.equal(next.stage, "notifying");
  assert.equal(next.steps.length, 1);
});

test("emergency_status: the call is linked to the man-down on screen, even before the ids match", () => {
  const md = { id: "md-7", worker_id: "worker-1", fell_at: 990 } as ManDown;
  // Event arrives after the man-down: linked by id.
  const linked = fold(dSequence().slice(0, 1), md).call;
  assert.equal(linked.mandown_id, "md-7");
  assert.equal(callForIncident(linked, md), linked);
  // Event arrived before the man-down: still found by worker + time.
  const early = fold(dSequence().slice(0, 1), null).call;
  assert.equal(callForIncident(early, md), early);
  // A different worker's call is not shown on this man-down.
  assert.equal(callForIncident({ ...early, worker_id: "worker-2" }, md), null);
});

test("mock in 'status' format: sends D's events, which fold into a finished call", () => {
  const { engine, run } = fakeEngine("status");
  const events: LiveEvent[] = [];
  engine.subscribe({ view: "crew" }, (ev) => events.push(structuredClone(ev)));
  engine.handle("POST", ["demo", "mandown"], new URLSearchParams(), { worker_id: "worker-4" });
  run(90);
  assert.ok(!events.some((e) => e.type === "emergency"), "no aggregated events in status format");
  const statuses = events.flatMap((e) => (e.type === "emergency_status" ? [e] : []));
  const { call, stages } = fold(statuses, engine.mandown);
  assert.deepEqual([...new Set(stages)], ["notifying", "cancel_window", "calling", "connected", "done"]);
  assert.equal(call.id, engine.emergency!.id, "same id as the snapshot's call, so a reconnect continues it");
  assert.equal(call.mandown_id, engine.mandown!.id);
  assert.ok(statuses.filter((e) => e.target === "emergency").every((e) => e.demo_label === "Demo: would call 911"));
});

test("mock: a failed emergency call (both formats)", () => {
  for (const format of ["call", "status"] as const) {
    const { engine, run } = fakeEngine(format);
    const events: LiveEvent[] = [];
    engine.subscribe({ view: "crew" }, (ev) => events.push(structuredClone(ev)));
    const res = engine.handle("POST", ["demo", "emergency"], new URLSearchParams(), { fail_next: true });
    assert.deepEqual(res.json, { format, fail_next: true });
    engine.handle("POST", ["demo", "sos"], new URLSearchParams(), { worker_id: "worker-2" });
    run(60);
    assert.equal(engine.emergency?.stage, "failed", format);
    assert.equal(engine.emergency?.outcome, CALL_FAILED_TEXT);
    assert.equal(engine.failNextCall, false, "only the next call fails");
    if (format === "status") {
      const statuses = events.flatMap((e) => (e.type === "emergency_status" ? [e] : []));
      assert.ok(statuses.some((e) => e.stage === "done" && e.status === "failed"));
      assert.equal(fold(statuses).call.stage, "failed");
    }
  }
});

test("crew lead can cancel by worker_id (session D's route); too late is refused", () => {
  const { engine, run } = fakeEngine("status");
  engine.handle("POST", ["demo", "mandown"], new URLSearchParams(), { worker_id: "worker-4" });
  run(30);
  assert.equal(engine.emergency?.stage, "cancel_window");
  const wrong = engine.handle("POST", ["emergency", "cancel"], new URLSearchParams(), { worker_id: "worker-3" });
  assert.equal((wrong.json as { ok: boolean }).ok, false);
  const res = engine.handle("POST", ["emergency", "cancel"], new URLSearchParams(), { worker_id: "worker-4" });
  assert.deepEqual(res.json, { ok: true });
  assert.equal(engine.emergency?.stage, "cancelled");

  // Second incident: wait until the call is ringing, then it's too late to cancel.
  const late = fakeEngine();
  late.engine.handle("POST", ["demo", "sos"], new URLSearchParams(), { worker_id: "worker-5" });
  late.run(15);
  assert.equal(late.engine.emergency?.stage, "calling");
  const tooLate = late.engine.handle("POST", ["emergency", "cancel"], new URLSearchParams(), { worker_id: "worker-5" });
  assert.deepEqual(tooLate.json, { ok: false, error: "too late to cancel" });
});

test("spot checks learn a personal offset with shrinkage, cap and bad-reading guard", () => {
  const { engine, run } = fakeEngine();
  const evs: LiveEvent[] = [];
  engine.subscribe({ view: "worker", worker_id: "worker-1" }, (ev) => evs.push(structuredClone(ev)));
  const crew: LiveEvent[] = [];
  engine.subscribe({ view: "crew" }, (ev) => crew.push(ev));
  run(5);
  const w = engine.workers.get("worker-1")!;
  // One reading 0.3 °C hotter than the model: offset = -0.3 / (1 + 2) = -0.1, confidence 1/3.
  const r1 = engine.handle("POST", ["spot-check"], new URLSearchParams(), { worker_id: "worker-1", thermometer_c: w.rawEstimate + 0.3 });
  const j1 = r1.json as { accepted: boolean; offset: { offset_c: number; confidence: number; checks: number } };
  assert.equal(j1.accepted, true);
  assert.ok(Math.abs(j1.offset.offset_c + 0.1) < 0.002);
  assert.ok(Math.abs(j1.offset.confidence - 1 / 3) < 0.002);
  // The estimate moves right away (model - offset).
  const est = w.estimates[w.estimates.length - 1];
  assert.ok(Math.abs(est.core.mean - (w.rawEstimate + 0.1)) < 0.01);
  // A crazy reading is ignored.
  const r2 = engine.handle("POST", ["spot-check"], new URLSearchParams(), { worker_id: "worker-1", thermometer_c: w.rawEstimate + 2.5 });
  assert.equal((r2.json as { accepted: boolean }).accepted, false);
  // Many readings 1.4 °C off: offset is capped at 0.6.
  for (let i = 0; i < 30; i++) engine.handle("POST", ["spot-check"], new URLSearchParams(), { worker_id: "worker-1", thermometer_c: w.rawEstimate - 1.4 });
  assert.equal(engine.offsetOf("worker-1"), 0.6);
  // Privacy: the worker gets offset_update, the crew view doesn't.
  assert.ok(evs.some((e) => e.type === "offset_update"));
  assert.ok(!crew.some((e) => e.type === "offset_update"));
});
