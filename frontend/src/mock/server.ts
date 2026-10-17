/**
 * One shared mock engine for the whole dev server.
 *
 * Next.js may load this file more than once (hot reload, separate routes),
 * so the engine lives on globalThis and is only created the first time.
 * When you edit engine.ts, hot reload brings a new MockEngine class: the old
 * engine is shut down (browsers reconnect by themselves) and a fresh one starts.
 */

import { MockEngine } from "./engine.ts";

const g = globalThis as unknown as { __planitMock?: MockEngine };

export function getEngine(): MockEngine {
  if (g.__planitMock && !(g.__planitMock instanceof MockEngine)) {
    // An engine from before the last code edit (a different class object).
    (g.__planitMock as { shutdown?: () => void; stop?: () => void }).shutdown?.();
    (g.__planitMock as { stop?: () => void }).stop?.();
    g.__planitMock = undefined;
  }
  if (!g.__planitMock) {
    // MOCK_EMERGENCY_EVENTS=status in .env.local makes the mock send session D's
    // emergency_status events instead of the whole EmergencyCall.
    const format = process.env.MOCK_EMERGENCY_EVENTS === "status" ? "status" : "call";
    g.__planitMock = new MockEngine({ emergencyFormat: format });
    g.__planitMock.start();
  }
  return g.__planitMock;
}
