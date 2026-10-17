/**
 * What each demo control sends (one click each, CLAUDE.md rule).
 * Paths are the contract's REST routes (docs/interfaces/frontend-contract.md); the backend
 * answers with a `demo` event and the controls redraw from it.
 */
import type { DemoState } from "./contract.ts";

export type Request = [path: string, body: Record<string, unknown>];

export const demoActions = {
  scenario: (demo: Pick<DemoState, "scenario">): Request => ["/demo/scenario", { action: demo.scenario.running ? "stop" : "start" }],
  testLightning: (): Request => ["/demo/test-alert", { kind: "LIGHTNING" }],
  testFloodFire: (): Request => ["/demo/test-alert", { kind: "FLOOD_FIRE" }],
  manDown: (workerId: string | null): Request => ["/demo/mandown", { worker_id: workerId }],
  sos: (workerId: string | null): Request => ["/demo/sos", { worker_id: workerId }],
  clearHazards: (): Request => ["/demo/clear-hazards", {}],
  /** Data source: replay on (at 10x) or live (1x). */
  replay: (on: boolean): Request => ["/demo/replay", { on, speed: on ? 10 : 1 }],
  speed: (speed: number): Request => ["/demo/replay", { speed }],
  threshold: (demo: Pick<DemoState, "demo_threshold">): Request => ["/demo/threshold", { on: !demo.demo_threshold }],
};

export const SPEEDS = [1, 5, 10, 30];
