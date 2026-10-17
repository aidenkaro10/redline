/**
 * Acceptance test 21 (docs/testing/frontend-acceptance.md): connection drop.
 * Own file because it switches the app to "real backend" mode (WebSocket) before loading it.
 * Run with: npm test
 */

import { test, mock } from "node:test";
import assert from "node:assert/strict";

import type { LiveEvent, Snapshot } from "@/lib/contract";
import fixture from "./fixtures/real/replay-recording.json" with { type: "json" };

/** A stand-in for the browser's WebSocket that the test drives by hand. */
class FakeSocket {
  static all: FakeSocket[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) { this.url = url; FakeSocket.all.push(this); }
  close() { if (!this.closed) { this.closed = true; this.onclose?.(); } }
  serverSends(ev: unknown) { this.onmessage?.({ data: JSON.stringify(ev) }); }
  serverDrops() { this.closed = true; this.onclose?.(); }
}

test("21 robustness: the WebSocket drops, the UI says so, retries, and rebuilds from the fresh snapshot", async () => {
  process.env.NEXT_PUBLIC_BACKEND_URL = "http://localhost:8000";           // real-backend mode
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeSocket;
  mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"], now: 1_000_000 });
  try {
    const { connect, applyEvents, EMPTY } = await import("@/lib/live");
    const got: LiveEvent[] = [];
    const status: boolean[] = [];
    const stop = connect({ view: "crew" }, (ev) => got.push(ev), (up) => status.push(up));

    const first = FakeSocket.all[0];
    assert.equal(first.url, "ws://localhost:8000/ws/live?view=crew");
    first.onopen?.();
    const snap = structuredClone(fixture.crew.snapshot[0]) as unknown as Snapshot;
    first.serverSends(snap);
    first.serverSends(fixture.crew.alert[1]);                                // an alert that will be stale later
    assert.deepEqual(status, [true]);

    // The connection drops: "reconnecting" (status false), then a retry after 1 s.
    first.serverDrops();
    assert.deepEqual(status, [true, false]);
    assert.equal(FakeSocket.all.length, 1);
    mock.timers.tick(1000);
    assert.equal(FakeSocket.all.length, 2, "retried");

    // The backend's first message on the new connection is a fresh snapshot (same as /api/state).
    const second = FakeSocket.all[1];
    second.onopen?.();
    const fresh = { ...structuredClone(snap), alerts: [] };                  // the old alert is gone now
    second.serverSends(fresh);
    assert.deepEqual(status, [true, false, true]);

    // Rebuild: apply everything in order, the way the provider does (a snapshot replaces the past).
    const lastSnap = got.map((e, i) => [e, i] as const).filter(([e]) => e.type === "snapshot").at(-1)![1];
    const state = applyEvents(EMPTY, got.slice(lastSnap));
    assert.equal(Object.keys(state.workers).length, snap.workers.length, "no duplicate workers");
    assert.deepEqual(state.alerts, [], "no stale alerts");
    // Even applying the whole history (not just from the last snapshot) ends the same way.
    const all = applyEvents(EMPTY, got);
    assert.equal(Object.keys(all.workers).length, snap.workers.length);
    assert.deepEqual(all.alerts, []);

    // A connection that goes silent (no clock ticks) is treated as dropped and reopened.
    mock.timers.tick(10_000);
    assert.equal(FakeSocket.all.length, 3, "silent connection reopened by the watchdog");

    // Leaving the page stops retrying.
    stop();
    mock.timers.tick(60_000);
    assert.equal(FakeSocket.all.length, 3);
  } finally {
    mock.timers.reset();
  }
});
