/**
 * More of the frontend contract: model results, personal twins, planner chat.
 * (Kept apart from contract.ts so parallel edits don't collide. Same rules apply.)
 *
 *   GET  /api/results          ModelResults  (ML session: harness output, always next to the Kalman baseline)
 *   GET  /api/twins            TwinReport    (ML session, Phase 6)
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
  notes: string[];
}

export interface TwinReport {
  available: boolean;             // false until the twins exist
  mock: boolean;                  // true = placeholder numbers from the frontend mock
  note: string;
  workers: {
    worker_id: string;
    name: string;
    days: { day: number; hr_rmse_bpm: number; core_rmse_c: number | null; kept: boolean }[];  // kept = passed the gate
  }[];
}

export interface ChatReply {
  session_id: string;
  reply: string;
  tool_calls: { tool: string; input: Record<string, unknown> }[];
  plan: unknown | null;           // the verified plan view, if any
  llm: boolean;                   // false = no API key, optimizer-only answer
  cost_usd?: number;
}
