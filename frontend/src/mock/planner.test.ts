// The day planner: fixed safety rules turn an hourly forecast into each person's plan.
import assert from "node:assert/strict";
import { test } from "node:test";

import { checkPlan, planDay, type HourWx } from "./planner.ts";

const people = [
  { id: "a", name: "Ana", breakOffset: 0, lighter: false },
  { id: "b", name: "Ben", breakOffset: 1, lighter: false },
  { id: "c", name: "Cy", breakOffset: 2, lighter: true },
];
const day = (hi: (h: number) => number, storm = (_h: number) => false): HourWx[] =>
  Array.from({ length: 24 }, (_, hour) => ({ hour, hiF: hi(hour), rain: storm(hour) ? 70 : 0, thunder: storm(hour) }));
const midnight = new Date(2026, 6, 15).getTime() / 1000;
const hourOf = (t: number) => new Date(t * 1000).getHours() + new Date(t * 1000).getMinutes() / 60;

test("planner: no heavy work at a heat index of 103 °F or more, and it passes the rule check", () => {
  const hours = day((h) => (h >= 11 && h < 16 ? 108 : 85));
  const plan = planDay(midnight, hours, people, "test");
  assert.equal(plan.checked, true);
  for (const list of Object.values(plan.slots)) {
    for (const s of list) {
      const h = hourOf(s.start);
      if (h >= 11 && h < 16) assert.notEqual(s.task, "heavy", `heavy at ${h}`);
    }
  }
  assert.ok(plan.notes.some((n) => n.includes("No heavy work")));
});

test("planner: hot day starts at 6:30, a mild day at 7:00", () => {
  assert.equal(planDay(midnight, day(() => 104), people, "t").startHour, 6.5);
  assert.equal(planDay(midnight, day(() => 82), people, "t").startHour, 7);
});

test("planner: everyone takes lunch at noon, but other breaks are staggered", () => {
  const plan = planDay(midnight, day(() => 85), people, "t");
  for (const list of Object.values(plan.slots)) assert.equal(list.find((s) => hourOf(s.start) === 12)?.task, "break");
  const breaksAt = (id: string) => plan.slots[id].filter((s) => s.task === "break" && hourOf(s.start) !== 12).map((s) => s.start).join();
  assert.notEqual(breaksAt("a"), breaksAt("b"));
});

test("planner: storms mean indoor work, and the storm hours are in the notes", () => {
  const plan = planDay(midnight, day(() => 88, (h) => h === 15 || h === 16), people, "t");
  assert.deepEqual(plan.stormWindow, [15, 17]);
  const at15 = plan.slots.a.find((s) => hourOf(s.start) === 15.5)!;
  assert.ok(at15.task === "break" || at15.label.startsWith("Indoor"));
  assert.ok(plan.notes.some((n) => n.startsWith("Storms likely")));
});

test("planner: the rule check catches a plan with heavy work in the danger heat", () => {
  const hours = day(() => 110);
  const plan = planDay(midnight, hours, people, "t");
  plan.slots.a = plan.slots.a.map((s) => (s.task === "light" ? { ...s, task: "heavy" } : s));
  assert.equal(checkPlan(plan, hours), false);
});
