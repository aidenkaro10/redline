// The weather and distance math behind the live hazards (no network here).
import assert from "node:assert/strict";
import { test } from "node:test";

import { heatIndexF, milesBetween, wbgtShadeC, wetBulbC } from "./realdata.ts";

test("realdata: NWS heat index matches the NWS chart", () => {
  assert.equal(Math.round(heatIndexF(90, 70)), 106);   // NWS chart: 90 °F at 70% RH -> 106 °F
  assert.equal(Math.round(heatIndexF(100, 40)), 109);  // NWS chart: 100 °F at 40% RH -> 109 °F
  assert.ok(Math.abs(heatIndexF(70, 50) - 69.1) < 1);  // below 80 °F the simple formula is used
});

test("realdata: wet-bulb (Stull) and shade WBGT match published values", () => {
  assert.ok(Math.abs(wetBulbC(20, 50) - 13.7) < 0.3);   // Stull 2011 worked example: 20 °C, 50% -> 13.7 °C
  const hot = wbgtShadeC(35, 60);                        // 95 °F, humid Florida afternoon in the shade
  const mild = wbgtShadeC(15, 60);                       // 59 °F morning
  assert.ok(hot > 29.5 && hot < 32, `hot ${hot}`);
  assert.ok(mild > 10 && mild < 14, `mild ${mild}`);
});

test("realdata: miles between two points", () => {
  // Deerhaven to J.R. Kelly, both in Gainesville: about 9 miles apart
  const d = milesBetween(29.7585, -82.3889, 29.6455, -82.3211);
  assert.ok(d > 8 && d < 10, `got ${d}`);
  assert.equal(milesBetween(29.7, -82.3, 29.7, -82.3), 0);
});

test("realdata: NWS alerts match a site by drawn area or by county name", async () => {
  const { alertsForSite } = await import("./realdata.ts");
  const box = (lat: number, lon: number): [number, number][] => [[lon - 0.1, lat - 0.1], [lon + 0.1, lat - 0.1], [lon + 0.1, lat + 0.1], [lon - 0.1, lat + 0.1], [lon - 0.1, lat - 0.1]];
  const deerhaven = { lat: 29.7585, lon: -82.3889, county: "Alachua" };
  const alerts = [
    { event: "Flood Warning", headline: "", severity: "Severe", ends: null, areaDesc: "Somewhere", polygons: [box(29.76, -82.39)] },        // drawn over the site
    { event: "Red Flag Warning", headline: "", severity: "Severe", ends: null, areaDesc: "Alachua, FL; Marion, FL", polygons: null },     // by county
    { event: "Heat Advisory", headline: "", severity: "Moderate", ends: null, areaDesc: "Inland Volusia", polygons: null },                // other county
    { event: "Wind Advisory", headline: "", severity: "Minor", ends: null, areaDesc: "Far away", polygons: [box(27, -81)] },              // drawn elsewhere
  ];
  assert.deepEqual(alertsForSite(alerts, deerhaven).map((a) => a.event), ["Flood Warning", "Red Flag Warning"]);
  assert.deepEqual(alertsForSite(alerts, { lat: 29.0, lon: -81.3, county: "Volusia" }).map((a) => a.event), ["Heat Advisory"]);
});
