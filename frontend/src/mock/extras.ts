/**
 * Mock endpoints for model results, twins and planner chat.
 *
 * - /api/results: the REAL numbers from docs/results.md (v1 on Mendeley validation people).
 * - /api/twins: PLACEHOLDER numbers (twins aren't built yet). Marked mock: true, the UI says so.
 * - /api/planner/chat: canned answers, shaped like session B's real endpoint.
 */

import type { ChatReply, ModelResults, TwinReport } from "../lib/contract-extra.ts";

export const RESULTS: ModelResults = {
  updated: "2026-10-17",
  split: "Mendeley validation people (10 people, 19 sessions). Never trained on. Final test people still untouched.",
  models: ["Kalman (ECTemp), best baseline", "Forecaster v1", "v1 + conformal"],
  rows: [
    { group: "Estimate now", name: "RMSE", unit: "°C", better: "lower", values: [0.414, 0.236, 0.236], text: ["0.414 (0.33–0.48)", "0.236 (0.18–0.28)", "0.236"] },
    { group: "Estimate now", name: "Bias", unit: "°C", better: "closer", target: 0, values: [-0.077, -0.033, -0.033] },
    { group: "Estimate now", name: "95% limits of agreement", unit: "°C", better: "lower", values: [1.6, 0.92, 0.92], text: ["−0.88 to +0.72", "−0.49 to +0.43", "−0.49 to +0.43"] },
    { group: "Forecast", name: "+10 min RMSE", unit: "°C", better: "lower", values: [0.445, 0.254, 0.254] },
    { group: "Forecast", name: "+20 min RMSE", unit: "°C", better: "lower", values: [0.456, 0.282, 0.282] },
    { group: "Forecast", name: "+30 min RMSE", unit: "°C", better: "lower", values: [0.457, 0.311, 0.311] },
    { group: "Forecast", name: "+20 min quantile loss", unit: "", better: "lower", values: [0.1155, 0.0723, null] },
    { group: "Calibration", name: "+20 min coverage of p90 (want 90%)", unit: "%", better: "closer", target: 90, values: [90, 88, 96] },
    { group: "Alerts", name: "Yellow warning lead time", unit: "min", better: "higher", values: [19.6, 25.0, null] },
    { group: "Alerts", name: "Yellow false alarms", unit: "/hr", better: "lower", values: [0.17, 0.2, null] },
    { group: "Alerts", name: "Red danger caught", unit: "%", better: "higher", values: [75, 75, 88], text: ["75–88%", "75% (6/8)", "88% (7/8)"] },
    { group: "Alerts", name: "Red false alarms", unit: "/hr", better: "lower", values: [0.41, 0.27, 0.61] },
  ],
  rmse_by_horizon: [
    { minutes_ahead: 0, values: [0.414, 0.236, 0.236] },
    { minutes_ahead: 10, values: [0.445, 0.254, 0.254] },
    { minutes_ahead: 20, values: [0.456, 0.282, 0.282] },
    { minutes_ahead: 30, values: [0.457, 0.311, 0.311] },
  ],
  notes: [
    "Split by person, never by time. Validation people were used for early stopping, so these numbers are slightly optimistic.",
    "Raw ECTemp's own uncertainty covers only 34–45% at the 90th percentile (should be 90%): it's overconfident.",
    "Conformal calibration fixes coverage but raises red false alarms. Next: 5-model ensemble, then personal twins.",
  ],
};

export function twins(workers: { worker_id: string; name: string }[]): TwinReport {
  return {
    available: true,
    mock: true,
    note: "PLACEHOLDER numbers from the frontend mock. Personal twins (PLAN.md Phase 6) aren't trained yet.",
    workers: workers.slice(0, 3).map((w, i) => ({
      worker_id: w.worker_id, name: w.name,
      days: Array.from({ length: 14 }, (_, d) => {
        const hr = 9.5 - 4.2 * (1 - Math.exp(-d / (4 + i))) + Math.sin(d * 1.7 + i) * 0.35;
        return { day: d + 1, hr_rmse_bpm: Math.round(hr * 100) / 100, core_rmse_c: Math.round((0.31 - 0.08 * (1 - Math.exp(-d / 5))) * 1000) / 1000, kept: !(d === 6 && i === 1) };
      }),
    })),
  };
}

const sessions = new Map<string, number>();

/** Canned planner answers. The real one (session B) runs Claude with tools; numbers come from its optimizer. */
export function plannerChat(message: string, sessionId?: string): ChatReply {
  const sid = sessionId || Math.random().toString(16).slice(2, 10);
  sessions.set(sid, (sessions.get(sid) ?? 0) + 1);
  const m = message.toLowerCase();
  let reply: string;
  let tool_calls: ChatReply["tool_calls"] = [{ tool: "get_crew_status", input: {} }];
  if (m.includes("storm") || m.includes("lightning")) {
    tool_calls = [{ tool: "get_forecast", input: { hazard: "lightning" } }, { tool: "run_optimizer", input: { avoid: "storm_window" } }, { tool: "simulate_schedule", input: {} }];
    reply = "Storms are likely from 3 to 7 PM. I moved the heavy line work to the morning and left 3 to 5 PM for light inspection near the trucks, so a lightning stop costs the least. Everyone stays under the heat limit in the simulation.";
  } else if (m.includes("jake") || m.includes("who")) {
    tool_calls = [{ tool: "get_crew_status", input: {} }, { tool: "get_forecast", input: { worker: "Jake" } }];
    reply = "Jake runs hottest: his 90th percentile core temp peaks around 38.2 °C on today's plan. I'd give him the light tasks after 1 PM and an extra shade break at 2:30.";
  } else if (m.includes("hour") || m.includes("finish") || m.includes("longer") || m.includes("what if")) {
    tool_calls = [{ tool: "simulate_schedule", input: { change: message } }];
    reply = "I simulated it. Two people would cross the heat limit after 2 PM, so the rule checker blocks that version. The closest safe option is starting 30 minutes earlier instead.";
  } else {
    reply = "Today's plan passes the safety rule checker. Heavy work is before 11 AM, breaks every 90 minutes from noon to 4 PM, and nobody's 90th percentile goes over the limit. Ask me a what-if, like \"what if we finish the line work after lunch?\"";
  }
  return { session_id: sid, reply, tool_calls, plan: null, llm: false, cost_usd: 0 };
}
