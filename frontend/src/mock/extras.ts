/**
 * Mock data for the results page (judges).
 *
 * - /api/results: the REAL numbers from docs/results.md (v1 on Mendeley validation people).
 */

import type { ModelResults } from "../lib/contract-extra.ts";

export const RESULTS: ModelResults = {
  updated: "2026-10-17",
  split: "Mendeley validation people (10 people, 19 sessions). Never trained on. Final test people still untouched.",
  models: ["Kalman (ECTemp), best baseline", "Forecaster v1", "5-model ensemble (ens5)"],
  best: 2,
  rows: [
    { group: "Estimate now", name: "RMSE", unit: "°C", better: "lower", values: [0.414, 0.236, 0.239], text: ["0.414 (0.33–0.48)", "0.236 (0.18–0.28)", "0.239"] },
    { group: "Estimate now", name: "Bias", unit: "°C", better: "closer", target: 0, values: [-0.077, -0.033, 0.007] },
    { group: "Estimate now", name: "95% limits of agreement", unit: "°C", better: "lower", values: [1.6, 0.92, 0.94], text: ["−0.88 to +0.72", "−0.49 to +0.43", "−0.46 to +0.48"] },
    { group: "Forecast", name: "+10 min RMSE", unit: "°C", better: "lower", values: [0.445, 0.254, 0.229] },
    { group: "Forecast", name: "+20 min RMSE", unit: "°C", better: "lower", values: [0.456, 0.282, 0.23] },
    { group: "Forecast", name: "+30 min RMSE", unit: "°C", better: "lower", values: [0.457, 0.311, 0.238] },
    { group: "Forecast", name: "+20 min quantile loss", unit: "", better: "lower", values: [0.1155, 0.0723, 0.0634] },
    { group: "Calibration", name: "+20 min coverage of p90 (want 90%)", unit: "%", better: "closer", target: 90, values: [90, 88, 96] },
    { group: "Alerts", name: "Yellow warning lead time", unit: "min", better: "higher", values: [19.6, 25.0, null] },
    { group: "Alerts", name: "Yellow false alarms", unit: "/hr", better: "lower", values: [0.17, 0.2, null] },
    { group: "Alerts", name: "Red danger caught", unit: "%", better: "higher", values: [75, 75, 88], text: ["75–88%", "75% (6/8)", "88% (7/8)"] },
    { group: "Alerts", name: "Red false alarms", unit: "/hr", better: "lower", values: [0.41, 0.27, 0.55] },
  ],
  rmse_by_horizon: [
    { minutes_ahead: 0, values: [0.414, 0.236, 0.239] },
    { minutes_ahead: 10, values: [0.445, 0.254, 0.229] },
    { minutes_ahead: 20, values: [0.456, 0.282, 0.23] },
    { minutes_ahead: 30, values: [0.457, 0.311, 0.238] },
  ],
  notes: [
    "Split by person, never by time. Validation people were used for early stopping, so these numbers are slightly optimistic.",
    "Kalman red catch is 75–88% depending on the baseline variant; the headline uses the lower end next to our 88%.",
    "The ensemble's ranges are too wide (96% coverage, want 90%), which costs red false alarms. Final calibration on 20 people will tighten them.",
    "Heart-rate twins did not help on real people (logged as a negative result). The personal offset from thermometer spot checks did.",
  ],
  planner: {
    source: "Session B, held-out test set: 12 scenarios × 5 runs, Claude Opus 5.5. Heat from our planning model (PHS ensemble), not real people.",
    rows: [
      { name: "What crews do today (fixed plan)", runs_with_violations_pct: 100, violations_per_run: 8.9, peak_p90_c: 40.48, throughput: 0.86, note: "No heat or storm planning" },
      { name: "AI alone (LLM, no checker)", runs_with_violations_pct: 92, violations_per_run: 3.1, peak_p90_c: 38.8, throughput: 0.87, note: "Mostly heat violations" },
      { name: "Optimizer only", runs_with_violations_pct: 0, violations_per_run: 0, peak_p90_c: 38.0, throughput: 0.64, note: "Safe but gets less done" },
      { name: "Our agent (AI + safety rule checker)", runs_with_violations_pct: 0, violations_per_run: 0, peak_p90_c: 38.0, throughput: 0.65, note: "Fairest heat spread" },
    ],
  },
  personalization: {
    source: "19 unseen real people: 3 spot checks from one session applied to their other session.",
    rows: [
      { name: "No correction (average person)", now_rmse_c: 0.306, plus20_rmse_c: 0.308 },
      { name: "Personal offset from spot checks", now_rmse_c: 0.285, plus20_rmse_c: 0.27 },
      { name: "Start-of-shift check fading to offset", now_rmse_c: 0.278, plus20_rmse_c: 0.267 },
    ],
  },
  figures: [
    { file: "04_rmse_by_horizon.png", title: "Error by how far ahead", caption: "Ensemble 0.23–0.24 °C and flat; v1 0.25–0.31; Kalman 0.44–0.46." },
    { file: "01_predicted_vs_measured.png", title: "Forecast vs measured, 20 min ahead", caption: "Three heat sessions that cross 38.5 °C: median forecast with the 10–90 band under the measured core temp." },
    { file: "05_alert_timeline.png", title: "Red alert vs real crossing", caption: "Median-lead example (26 min). On this one the Kalman fires about as early; the table has the overall comparison." },
    { file: "02_bland_altman.png", title: "Agreement (Bland-Altman)", caption: "Typical error 0.41 (Kalman) vs 0.24 °C (ours). 95% limits −0.88/+0.72 vs −0.46/+0.48." },
    { file: "03_coverage_by_horizon.png", title: "Are the ranges honest?", caption: "Share of truths under the 90th percentile. Ensemble 96% from 12 min (too cautious); Kalman and v1 overconfident." },
  ],
};
