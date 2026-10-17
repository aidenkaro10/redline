import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToString } from "react-dom/server";

import CrewApp from "@/app/page";
import { applyEvents, EMPTY, LiveContext } from "@/lib/live";
import fixture from "./fixtures/real/replay-recording.json" with { type: "json" };

test("smoke: crew app renders from a real snapshot", () => {
  const state = applyEvents(EMPTY, [fixture.crew.snapshot[0] as never]);
  const html = renderToString(<LiveContext.Provider value={state}><CrewApp /></LiveContext.Provider>);
  assert.match(html, /Jake/);
});
