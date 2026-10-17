// Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";

import { toOffsetUpdate } from "./offset.ts";

test("session A's flat offset_update becomes an OffsetUpdate with a growing history", () => {
  const a = toOffsetUpdate({ type: "offset_update", worker_id: "demo-1", timestamp: 100, offset_c: -0.1, confidence: 1 / 3, n_checks: 1, accepted: true, thermometer_c: 37.5, model_c: 37.2, miss_c: -0.3 })!;
  assert.equal(a.checks, 1);
  assert.equal(a.history.length, 2);                 // the "average person" start + this check
  assert.equal(a.last_check?.thermometer_c, 37.5);
  const b = toOffsetUpdate({ type: "offset_update", worker_id: "demo-1", timestamp: 200, offset_c: -0.15, confidence: 0.5, n_checks: 2, accepted: true, thermometer_c: 37.6, model_c: 37.3, miss_c: -0.3 }, a)!;
  assert.equal(b.history.length, 3);
  // A rejected reading doesn't add a history point.
  const c = toOffsetUpdate({ type: "offset_update", worker_id: "demo-1", timestamp: 300, offset_c: -0.15, confidence: 0.5, n_checks: 2, accepted: false, thermometer_c: 40, model_c: 37.3, miss_c: -2.7 }, b)!;
  assert.equal(c.history.length, 3);
  assert.equal(c.last_check?.accepted, false);
});

test("our own shape passes through, junk returns null", () => {
  const ours = { worker_id: "w", timestamp: 1, offset_c: 0, confidence: 0, checks: 0, last_check: null, history: [] };
  assert.deepEqual(toOffsetUpdate({ type: "offset_update", offset: ours }), ours);
  assert.equal(toOffsetUpdate({ type: "offset_update" }), null);
  assert.equal(toOffsetUpdate(null), null);
});
