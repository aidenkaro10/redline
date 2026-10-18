/**
 * THE results data file for the judges' page (/demo/results). Every number on that page comes
 * from here, copied from docs/results.md on main ("FINAL: final_cv", the locked test people).
 * The acceptance test 19 checks it against docs/results.md.
 */

import type { ModelResults } from "./contract-extra.ts";

export const RESULTS: ModelResults = {
  updated: "2026-10-17",
  split: "10 unseen test people (20 sessions). Locked away: never used for training, model choice or calibration. Scored once.",
  models: ["Kalman (ECTemp), the published now-only method", "Kalman flat + 90% range (baseline)", "Kalman + trend (baseline)", "Our model (final_cv, 5-model ensemble)"],
  best: 3,
  rows: [
    { group: "Estimate now", name: "RMSE", unit: "°C", better: "lower", values: [0.398, 0.398, 0.398, 0.347], text: ["0.398 (0.33–0.44)", "0.398", "0.398", "0.347 (0.21–0.40)"] },
    { group: "Forecast", name: "+10 min RMSE", unit: "°C", better: "lower", values: [0.442, 0.439, 0.384, 0.363] },
    { group: "Forecast", name: "+20 min RMSE", unit: "°C", better: "lower", values: [0.481, 0.462, 0.38, 0.37] },
    { group: "Forecast", name: "+30 min RMSE", unit: "°C", better: "lower", values: [0.5, 0.465, 0.4, 0.371] },
    { group: "Forecast", name: "+20 min quantile loss", unit: "", better: "lower", values: [0.1582, 0.1143, 0.0988, 0.0882] },
    { group: "Calibration", name: "+20 min coverage of p90 (want 90%)", unit: "%", better: "closer", target: 90, values: [52, 92, 98, 95] },
    { group: "Alerts", name: "Yellow warnings caught", unit: "%", better: "higher", values: [12, 88, 88, 88] },
    { group: "Alerts", name: "Yellow warning lead time", unit: "min", better: "higher", values: [29.0, 16.1, 13.0, 16.9] },
    { group: "Alerts", name: "Yellow false alarms", unit: "/hr", better: "lower", values: [0.03, 0.2, 0.23, 0.2] },
    { group: "Alerts", name: "Red danger caught", unit: "%", better: "higher", values: [70, 90, 100, 90], text: ["70%", "90% (9/10)", "100% (10/10)", "90% (9/10)"] },
    { group: "Alerts", name: "Red lead time", unit: "min", better: "higher", values: [18.3, 33.6, 36.1, 31.9] },
    { group: "Alerts", name: "Red false alarms", unit: "/hr", better: "lower", values: [0.26, 0.3, 0.3, 0.46] },
  ],
  rmse_by_horizon: [
    { minutes_ahead: 0, values: [0.398, 0.398, 0.398, 0.347] },
    { minutes_ahead: 10, values: [0.442, 0.439, 0.384, 0.363] },
    { minutes_ahead: 20, values: [0.481, 0.462, 0.38, 0.37] },
    { minutes_ahead: 30, values: [0.5, 0.465, 0.4, 0.371] },
  ],
  notes: [
    "Split by person, never by time. The 10 test people were locked away until the end and scored once. Nothing was tuned on them afterwards.",
    "Vs the published now-only Kalman method: forecasts 18–26% more accurate 10 to 30 min ahead, 13% better right now, and the ranges are honest (Kalman's 90% range only covers about half the time).",
    "Vs our own Kalman + trend baseline: only 3–7% more accurate. On red alerts it caught 9 of 10 vs 10 of 10, with more false alarms. 10 episodes is a small sample, but that's the result.",
    "Early yellow warnings come mostly from forecasting with an honest range, not from the network alone: a flat Kalman forecast with a 90% range warns nearly as early (16.1 vs 16.9 min, same 88% caught, same false alarms).",
    "Earlier validation numbers (0.23 °C at +20 min) were optimistic, because those people were used to pick the model. 0.370 °C is the number to quote.",
    "Ranges run slightly wide (95% vs 90% wanted), which errs on the safe side.",
    "The personal offset from thermometer spot checks comes on top of this (its own test, below).",
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
    { file: "04_rmse_by_horizon.png", title: "Error by how far ahead", caption: "From the earlier validation run (before the final test). The table above has the final numbers." },
    { file: "01_predicted_vs_measured.png", title: "Forecast vs measured, 20 min ahead", caption: "Validation run: three heat sessions that cross 38.5 °C, median forecast with the 10–90 band under the measured core temp." },
    { file: "05_alert_timeline.png", title: "Red alert vs real crossing", caption: "Validation run: one example of when the red alert fires vs when the worker really crossed." },
    { file: "02_bland_altman.png", title: "Agreement (Bland-Altman)", caption: "From the earlier validation run (before the final test)." },
    { file: "03_coverage_by_horizon.png", title: "Are the ranges honest?", caption: "Validation run: share of truths under the 90th percentile, by minutes ahead." },
  ],
};
