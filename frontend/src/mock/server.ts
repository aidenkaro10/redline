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
    g.__planitMock = new MockEngine();
    g.__planitMock.start();
  }
  return g.__planitMock;
}
