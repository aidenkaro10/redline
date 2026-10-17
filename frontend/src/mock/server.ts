/**
 * One shared mock engine for the whole dev server.
 *
 * Next.js may load this file more than once (hot reload, separate routes),
 * so the engine lives on globalThis and is only created the first time.
 * If you change engine.ts, restart `npm run dev` to pick it up.
 */

import { MockEngine } from "./engine.ts";

const g = globalThis as unknown as { __planitMock?: MockEngine };

export function getEngine(): MockEngine {
  if (!g.__planitMock) {
    // MOCK_EMERGENCY_EVENTS=status in .env.local makes the mock send session D's
    // emergency_status events instead of the whole EmergencyCall.
    const format = process.env.MOCK_EMERGENCY_EVENTS === "status" ? "status" : "call";
    g.__planitMock = new MockEngine({ emergencyFormat: format });
    g.__planitMock.start();
  }
  return g.__planitMock;
}
