// The two saved crews: who's in each, and the friends passcode rule.
import assert from "node:assert/strict";
import { test } from "node:test";

import { MockEngine } from "./engine.ts";
import { checkPasscode, friendNames, ownerName, teams } from "./teams.ts";

function withEnv(vars: Record<string, string | undefined>, fn: () => void) {
  const old: Record<string, string | undefined> = {};
  for (const k of Object.keys(vars)) { old[k] = process.env[k]; if (vars[k] === undefined) delete process.env[k]; else process.env[k] = vars[k]; }
  try { fn(); } finally { for (const k of Object.keys(vars)) { if (old[k] === undefined) delete process.env[k]; else process.env[k] = old[k]; } }
}

test("teams: friends' names and the owner come from the environment, with placeholders locally", () => {
  withEnv({ REDLINE_FRIENDS: undefined, REDLINE_OWNER_NAME: undefined }, () => {
    assert.equal(ownerName(), "Aiden");
    assert.deepEqual(friendNames(), ["Friend 1", "Friend 2", "Friend 3"]);
  });
  withEnv({ REDLINE_FRIENDS: " Sam, Priya ,, Luis ", REDLINE_OWNER_NAME: "Aiden K." }, () => {
    assert.equal(ownerName(), "Aiden K.");
    assert.deepEqual(friendNames(), ["Sam", "Priya", "Luis"]);
  });
});

test("teams: demo crew is open; friends crew needs the passcode; on Render an unset passcode refuses", () => {
  withEnv({ REDLINE_FRIENDS_PASSCODE: undefined, RENDER: undefined }, () => {
    assert.equal(checkPasscode("demo", "").ok, true);
    assert.equal(checkPasscode("friends", "redline").ok, true);         // local testing default
    assert.equal(checkPasscode("friends", "nope").ok, false);
    assert.equal(checkPasscode("nobody", "x").ok, false);
  });
  withEnv({ REDLINE_FRIENDS_PASSCODE: "gators26", RENDER: "true" }, () => {
    assert.equal(checkPasscode("friends", "gators26").ok, true);
    assert.equal(checkPasscode("friends", "redline").ok, false);         // the local default never works when a real one is set
  });
  withEnv({ REDLINE_FRIENDS_PASSCODE: undefined, RENDER: "true" }, () => {
    const r = checkPasscode("friends", "redline");
    assert.equal(r.ok, false);
    assert.match(r.ok ? "" : r.message, /REDLINE_FRIENDS_PASSCODE/);
  });
  assert.deepEqual(teams().map((t) => [t.id, t.locked]), [["demo", false], ["friends", true]]);
});

test("teams: the engine puts the 6 made-up workers + the owner in demo, and the owner + friends in friends", () => {
  withEnv({ REDLINE_FRIENDS: "Sam, Priya", REDLINE_OWNER_NAME: "Aiden" }, () => {
    const e = new MockEngine();
    const snap = e.snapshot({ view: "crew" });
    const byTeam = (t: string) => snap.workers.filter((w) => w.team === t).map((w) => w.name);
    assert.equal(byTeam("demo").length, 7);
    assert.ok(byTeam("demo").includes("Aiden") && byTeam("demo").includes("Maria Lopez"));
    assert.deepEqual(byTeam("friends"), ["Aiden", "Sam", "Priya"]);
    e.stop?.();
  });
});
