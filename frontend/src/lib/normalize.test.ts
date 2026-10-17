// Run with: npm test
import { test } from "node:test";
import assert from "node:assert/strict";

import type { Hazard, SiteStatus } from "./contract.ts";
import { normalizeSite, secondsUntil } from "./normalize.ts";

test("C1's flat lightning/heat fields become the nested shape; nulls are safe", () => {
  const site = {
    site_id: "eia-663", index_tomorrow: null, index_drivers: [], worker_flag: "none", workers_on_site: 4, updated_at: 1,
    hazards: [
      { kind: "heat", level: "none", headline: "No hazard", detail: null, source: null, updated_at: null, stop_work: false, band_pattern: null, wbgt_c: 28.8, heat_index_f: 90.9 },
      { kind: "lightning", level: "extreme", headline: "Lightning 3 mi", detail: null, source: "GOES-19 GLM", updated_at: 5, stop_work: true, band_pattern: "LIGHTNING", nearest_miles: 3, last_strike_at: 4, all_clear_at: 1804, strikes: [{ lat: 1, lon: 2, at: 4, miles: 3 }] },
    ],
  } as unknown as SiteStatus;
  const n = normalizeSite(site);
  const l = n.hazards[1] as Hazard;
  assert.equal(l.lightning?.all_clear_at, 1804);
  assert.equal(l.lightning?.strikes.length, 1);
  assert.equal(n.hazards[0].heat?.wbgt_c, 28.8);
  assert.equal(n.hazards[0].detail, "");
  assert.equal(n.index_tomorrow, null);
});

test("secondsUntil picks the clock the time belongs to", () => {
  const sim = 2_000_000, wall = 1_000_000;
  assert.equal(secondsUntil(sim + 30, sim, wall), 30);     // replay-time deadline
  assert.equal(secondsUntil(wall + 45, sim, wall), 45);    // real-clock deadline (D's call, live feeds)
});
