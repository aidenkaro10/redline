/**
 * More of the frontend contract: model results and planner chat.
 * (Kept apart from contract.ts so parallel edits don't collide. Same rules apply.)
 *
 *   GET  /api/results          ModelResults  (ML session: harness output, always next to the Kalman baseline)
 *   POST /api/planner/chat     {message, session_id?} -> ChatReply   (session B, already built)
 */

export interface MetricRow {
  group: string;                  // "Estimate now", "Forecast", "Calibration", "Alerts"
  name: string;                   // "RMSE", "+20 min coverage of p90"
  unit: string;                   // "°C", "%", "min", "/hr", ""
  better: "lower" | "higher" | "closer";   // how to pick the winner
  target?: number;                // for "closer" (e.g. coverage 0.90)
  values: (number | null)[];      // one per model, same order as ModelResults.models
  text?: (string | null)[];       // optional display text per model (ranges, CIs)
}

export interface ModelResults {
  updated: string;                // date of the run, "2026-10-17"
  split: string;                  // "Mendeley validation people (10 people, 19 sessions)"
  models: string[];               // first one is the Kalman baseline
  rows: MetricRow[];
  /** RMSE by horizon for the bar chart: minutes_ahead 0 = "now". */
  rmse_by_horizon: { minutes_ahead: number; values: (number | null)[] }[];
  best: number;                   // index in `models` of our headline model
  notes: string[];
  /** Planner safety (session B): same LLM with and without the rule checker. */
  planner?: {
    source: string;
    rows: { name: string; runs_with_violations_pct: number; violations_per_run: number; peak_p90_c: number; throughput: number; note: string }[];
  };
  /** Personal offset from thermometer spot checks (backend/models/personal_offset.py). */
  personalization?: {
    source: string;
    rows: { name: string; now_rmse_c: number; plus20_rmse_c: number }[];
  };
  /** Result figures (PNG files in docs/figures, copied to /figures by `npm run dev`). */
  figures?: { file: string; title: string; caption: string }[];
}

export interface ChatReply {
  session_id: string;
  reply: string;
  tool_calls: { tool: string; input: Record<string, unknown> }[];
  plan: unknown | null;           // the verified plan view, if any
  llm: boolean;                   // false = no API key, optimizer-only answer
  cost_usd?: number;
}
