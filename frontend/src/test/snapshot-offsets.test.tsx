/**
 * e2e bug #3 (docs/sessions/e2e-tests.md): reloading the demo page after a spot check crashed it.
 * The snapshot's `offsets` arrive in session A's flat shape. The store has to turn them into
 * OffsetUpdate (with a history list), because LearningCard reads offset.history.length.
 * The payload below is copied from the real recording (tests/e2e/fixtures/live/snapshot_demo.json).
 * Run with: npm test
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import type { LiveEvent } from "@/lib/contract";
import { applyEvents, EMPTY } from "@/lib/live";

const SNAPSHOT = {
  type: "snapshot", sim_time: 1792002625.0, workers: [], sites: [], alerts: [], mandown: null, emergency: null,
  demo: { source: "replay", speed: 30.0, demo_threshold: false, mock: false },
  reports: [], log: [], history: {}, band: [],
  offsets: {
    "demo-1": { type: "offset_update", worker_id: "demo-1", timestamp: 1792002625.0, accepted: true, offset_c: 0.0653, confidence: 0.333, n_checks: 1 },
    "demo-2": { type: "offset_update", worker_id: "demo-2", timestamp: 1792002625.0, accepted: true, offset_c: 0.0, confidence: 0.0, n_checks: 0 },
  },
} as unknown as LiveEvent;

test("e2e #3: a snapshot with A's flat offsets gives every worker a full OffsetUpdate", () => {
  const s = applyEvents(EMPTY, [SNAPSHOT]);
  const jake = s.offsets["demo-1"];
  assert.ok(jake, "demo-1's saved offset survives the reload");
  assert.ok(Array.isArray(jake.history) && jake.history.length >= 1, "history is a list, so LearningCard can read .length");
  assert.equal(jake.checks, 1);
  assert.equal(jake.offset_c, 0.0653);
  assert.ok(Array.isArray(s.offsets["demo-2"].history), "a worker with no checks yet still gets a history list");
});
