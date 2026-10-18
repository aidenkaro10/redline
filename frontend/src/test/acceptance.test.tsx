/**
 * Frontend acceptance tests, numbered after docs/testing/frontend-acceptance.md.
 * Run with: npm test
 *
 * Real backend payloads come from fixtures/real/replay-recording.json (recorded from
 * `python -m backend.app.main --source replay --speed 10`, see its _about). Where the
 * live feed doesn't carry something (session D's raw emergency_status events, a red
 * worker), the test builds it and says so.
 *
 * Screens are rendered to HTML with react-dom/server inside the same LiveContext the app
 * uses, so what's checked is what a person would see (minus the map and charts' drawing).
 * A test that fails only because of backend behavior is marked `todo` with the reason
 * and listed in docs/sessions/frontend.md. Tests are never loosened to pass.
 */

import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ReactNode } from "react";
import { renderToString } from "react-dom/server";

import CrewApp from "@/app/page";
import DemoPage from "@/app/demo/page";
import ResultsPage from "@/app/demo/results/page";
import WorkerPhonePage from "@/app/worker/[id]/page";
import { AppHeader } from "@/components/AppHeader";
import { DemoControls, HeartRateButtons } from "@/components/DemoControls";
import { Shell } from "@/components/Shell";
import { buildCoreChart } from "@/lib/chart";
import type { Estimate, LiveEvent, Site, SiteStatus, ViewFilter, WorkerStatus } from "@/lib/contract";
import { primeSites } from "@/lib/crew";
import { demoActions } from "@/lib/demo-actions";
import { CALL_FAILED_TEXT, cancelCallRequest } from "@/lib/emergency";
import { parseThermometer } from "@/lib/format";
import { DEMO_HR_TAG, hrPicker, readHrMode } from "@/lib/heart-rate";
import { createHold } from "@/lib/hold";
import { acceptEvent, applyEvents, EMPTY, LiveContext, type LiveState } from "@/lib/live";
import { lightningRings, TEN_MILES_M } from "@/lib/map-layers";
import { normalizeSite } from "@/lib/normalize";
import { toOffsetUpdate } from "@/lib/offset";
import { singleFlight } from "@/lib/once";
import { rankSites } from "@/lib/ranking";
import { RESULTS } from "@/lib/results-data";
import { sendSpotCheck } from "@/lib/spotcheck";
import { MockEngine } from "@/mock/engine";
import { setRoute } from "./stubs/next-navigation.mjs";
import fixture from "./fixtures/real/replay-recording.json" with { type: "json" };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const F = fixture as any;
const DOCS = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "docs");
const CREW: ViewFilter = { view: "crew" };
const DEMO: ViewFilter = { view: "demo" };
const WORKER: ViewFilter = { view: "worker", worker_id: "demo-2" };

// The site list (GET /api/sites). Only 6 were recorded; add the crew site so names render.
const SITES: Site[] = [...F.rest.sites, { id: "eia-663", name: "Deerhaven Generating Station", operator: "GRU", tech: "gas", capacity_mw: 471, lat: 29.7597, lon: -82.3893, county: "Alachua", coastal: false }];
primeSites(SITES);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const clone = <T,>(x: T): T => structuredClone(x);
const msgs = (view: "crew" | "demo" | "worker", type: string): LiveEvent[] => clone(F[view][type] ?? []);
const snapshot = (view: "crew" | "demo" | "worker") => msgs(view, "snapshot")[0];

/** What the app would hold after these events arrive on this view (privacy gate included). */
function stateOf(filter: ViewFilter, events: LiveEvent[], base: LiveState = EMPTY): LiveState {
  const kept = events.map((e) => acceptEvent(filter, e)).filter((e): e is LiveEvent => e !== null);
  return applyEvents(base, kept);
}

/** Render a screen like the app does (the man-down screen sits on top of every page). */
function render(node: ReactNode, state: LiveState, route: { path: string; search?: string; params?: Record<string, string> } = { path: "/" }) {
  setRoute(route);
  return renderToString(<LiveContext.Provider value={state}><Shell>{node}</Shell></LiveContext.Provider>);
}

/** The visible text of some HTML. */
function text(html: string) {
  return html.replace(/<!-- -->/g, "").replace(/<(script|style)[\s\S]*?<\/\1>/g, " ").replace(/<[^>]+>/g, " ")
    .replace(/&#x27;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/\s+/g, " ");
}

/** The HTML of the crew row for one worker. */
function row(html: string, name: string) {
  const li = html.split("<li").find((x) => x.includes(`>${name}<`));
  assert.ok(li, `row for ${name}`);
  return li!;
}

function workerEvent(base: WorkerStatus, patch: Partial<WorkerStatus>): LiveEvent {
  return { type: "worker_status", worker: { ...clone(base), ...patch } };
}

const crewBase = () => stateOf(CREW, [snapshot("crew")]);
const realWorker = (id: string) => (snapshot("crew") as { workers: WorkerStatus[] }).workers.find((w) => w.worker_id === id)!;

// ---------------------------------------------------------------------------
// Contract
// ---------------------------------------------------------------------------

test("01 contract: every recorded fixture parses and keeps the fields the UI reads", () => {
  for (const [view, filter] of [["crew", CREW], ["demo", DEMO], ["worker", WORKER]] as const) {
    const all: LiveEvent[] = Object.entries(F[view] as Record<string, LiveEvent[]>)
      .sort(([a], [b]) => (a === "snapshot" ? -1 : b === "snapshot" ? 1 : 0))
      .flatMap(([, list]) => clone(list));
    const s = stateOf(filter, all);                                    // must not throw
    assert.ok(s.ready, `${view}: snapshot applied`);
    for (const ev of F[view].worker_status ?? []) {
      const w = s.workers[ev.worker.worker_id];
      for (const k of ["name", "status", "site_id", "status_reason"] as const) assert.notEqual(w[k], undefined, `${view} worker.${k}`);
    }
    for (const ev of F[view].site_status ?? []) {
      const raw = ev.site as { hazards: Record<string, unknown>[] };
      const n = normalizeSite(clone(raw) as unknown as SiteStatus);
      raw.hazards.forEach((h, i) => {
        if ("all_clear_at" in h) assert.equal(n.hazards[i].lightning?.all_clear_at, h.all_clear_at, `${view} lightning all_clear_at`);
        if ("strikes" in h) assert.deepEqual(n.hazards[i].lightning?.strikes, h.strikes);
        if (typeof h.wbgt_c === "number") assert.equal(n.hazards[i].heat?.wbgt_c, h.wbgt_c);
        assert.equal(typeof n.hazards[i].headline, "string");
      });
    }
    const lastEm = (F[view].emergency ?? []).at(-1)?.emergency;
    if (lastEm) assert.equal(s.emergency?.stage, lastEm.stage, `${view} emergency stage`);
    const lastMd = (F[view].mandown ?? []).at(-1)?.mandown;
    if (lastMd) assert.equal(s.mandown?.stage, lastMd.stage, `${view} mandown stage`);
  }
  // A's flat spot-check result and its snapshot form both come through.
  for (const ev of F.worker.offset_update) {
    const u = toOffsetUpdate(ev);
    assert.ok(u && u.offset_c === ev.offset_c && u.checks === ev.n_checks);
  }
  const fromSnap = stateOf(WORKER, [clone(F.rest.state_worker)]);
  assert.equal(fromSnap.offsets["demo-2"]?.offset_c, F.rest.state_worker.offsets["demo-2"].offset_c);
  // The real estimate's forecast and why-line arrive intact.
  const est = stateOf(WORKER, [snapshot("worker"), ...msgs("worker", "estimate")]).history["demo-2"].estimates.at(-1)!;
  assert.equal(est.forecast.length, 30);
  assert.equal(typeof est.why, "string");
});

// Session J's recorded payloads (tests/e2e/fixtures in the repo once merged; E2E_FIXTURES points elsewhere).
const J_DIR = process.env.E2E_FIXTURES ?? join(DOCS, "..", "tests", "e2e", "fixtures");
const jFile = (name: string) => JSON.parse(readFileSync(join(J_DIR, name), "utf8"));
const hasJ = existsSync(join(J_DIR, "live"));

test("01 contract (session J's fixtures): every recorded payload parses through the frontend", { skip: hasJ ? false : "session J's tests/e2e/fixtures isn't merged yet" }, () => {
  const live = readdirSync(join(J_DIR, "live")).filter((f) => f.endsWith(".json"));
  assert.ok(live.length > 5);
  for (const [view, filter] of [["crew", CREW], ["demo", DEMO], ["worker", null]] as const) {
    const snap = jFile(`live/snapshot_${view}.json`)[0];
    const f: ViewFilter = filter ?? { view: "worker", worker_id: snap.workers[0]?.worker_id };
    const events: LiveEvent[] = [snap, ...live.filter((x) => !x.startsWith("snapshot_")).flatMap((x) => jFile(`live/${x}`))];
    const s = stateOf(f, events);                                         // must not throw
    assert.ok(s.ready);
    for (const w of Object.values(s.workers)) assert.ok(w.name && w.status && w.site_id);
    for (const site of Object.values(s.sites)) for (const h of site.hazards) assert.equal(typeof h.headline, "string");
  }
  for (const ev of jFile("live/site_status.json")) {
    const n = normalizeSite(clone(ev.site));
    ev.site.hazards.forEach((h: Record<string, unknown>, i: number) => {
      if ("all_clear_at" in h) assert.equal(n.hazards[i].lightning?.all_clear_at, h.all_clear_at);
    });
  }
  for (const ev of jFile("live/offset_update.json")) assert.equal(toOffsetUpdate(ev)?.offset_c, ev.offset_c);
  for (const name of ["rest/get_api_state_crew.json", "rest/get_api_state_demo.json", "rest/get_api_state_worker.json"]) {
    const body = jFile(name).body;
    assert.equal(stateOf(DEMO, [body]).ready, true, name);
  }
  for (const site of jFile("rest/get_api_sites.json").body) assert.ok(site.id && site.name && typeof site.lat === "number");
});

test("02 contract: unknown event types are ignored", () => {
  const before = crewBase();
  const after = applyEvents(before, [{ type: "something_new", payload: { hr: 999 } } as unknown as LiveEvent]);
  assert.deepEqual(after, before);
  assert.equal(render(<CrewApp />, after), render(<CrewApp />, before));
});

test("03 contract: missing optional fields render as -- or are hidden (no NaN / undefined)", () => {
  const snap = snapshot("demo") as LiveEvent & { workers: WorkerStatus[]; sites: SiteStatus[]; history: Record<string, { estimates: Estimate[] }> };
  snap.workers = snap.workers.map((w) => ({ ...w, minutes_to_danger: null, break_at: null, on_break: false }));
  snap.sites = snap.sites.map((x) => ({ ...x, hazard_index: null, index_tomorrow: null }));
  for (const h of Object.values(snap.history)) h.estimates = h.estimates.map((e) => ({ ...e, why: null }));
  const crew = stateOf(CREW, [clone(snap)]);
  const demo = stateOf(DEMO, [clone(snap)]);
  const worker = stateOf(WORKER, [clone(snap)]);
  const pages = [
    render(<CrewApp />, crew),
    render(<CrewApp />, crew, { path: "/", search: "tab=sites" }),
    render(<WorkerPhonePage />, worker, { path: "/worker/demo-2", search: "details=1", params: { id: "demo-2" } }),
    render(<DemoPage />, demo, { path: "/demo", search: "worker=demo-2" }),
  ];
  for (const html of pages) assert.doesNotMatch(text(html), /\bNaN\b|\bundefined\b|\bnull\b/);
  assert.match(text(row(pages[0], "Jake")), /Danger -- /);                          // minutes_to_danger null
  assert.match(text(pages[1]), /--/);                                               // index null
  assert.doesNotMatch(text(pages[2]), /Next break/);                                // break_at null: tile hidden
  assert.doesNotMatch(pages[2], /data-testid="why"/);                               // why null: hidden
});

test("02 contract (session J's fixtures): unknown event types are ignored", { skip: hasJ ? false : "session J's tests/e2e/fixtures isn't merged yet" }, (t) => {
  const now = Date.now();
  t.mock.method(Date, "now", () => now);            // J's snapshot has a live SOS timer: freeze the clock between renders
  for (const [view, filter] of [["crew", CREW], ["demo", DEMO]] as const) {
    const before = stateOf(filter, [jFile(`live/snapshot_${view}.json`)[0]]);
    const after = applyEvents(before, [{ type: "something_new", payload: { hr: 999 } } as unknown as LiveEvent]);
    assert.deepEqual(after, before, view);
    assert.equal(render(<CrewApp />, after), render(<CrewApp />, before));
  }
});

test("03 contract (session J's fixtures): missing optional fields render as -- or are hidden", { skip: hasJ ? false : "session J's tests/e2e/fixtures isn't merged yet" }, () => {
  const snap = jFile("live/snapshot_demo.json")[0] as LiveEvent & { workers: WorkerStatus[]; sites: SiteStatus[]; history: Record<string, { estimates: Estimate[] }> };
  snap.workers = snap.workers.map((w) => ({ ...w, minutes_to_danger: null, break_at: null, on_break: false }));
  snap.sites = snap.sites.map((x) => ({ ...x, hazard_index: null, index_tomorrow: null }));
  for (const h of Object.values(snap.history ?? {})) h.estimates = (h.estimates ?? []).map((e) => ({ ...e, why: null }));
  const id = snap.workers[0].worker_id;
  const pages = [
    render(<CrewApp />, stateOf(CREW, [clone(snap)])),
    render(<CrewApp />, stateOf(CREW, [clone(snap)]), { path: "/", search: "tab=sites" }),
    render(<WorkerPhonePage />, stateOf({ view: "worker", worker_id: id }, [clone(snap)]), { path: `/worker/${id}`, search: "details=1", params: { id } }),
    render(<DemoPage />, stateOf(DEMO, [clone(snap)]), { path: "/demo", search: `worker=${id}` }),
  ];
  for (const html of pages) assert.doesNotMatch(text(html), /\bNaN\b|\bundefined\b|\bnull\b/);
  assert.match(text(row(pages[0], snap.workers[0].name)), /Danger -- /);            // minutes_to_danger null
  assert.doesNotMatch(text(pages[2]), /Next break/);                                // break_at null: tile hidden
  assert.doesNotMatch(pages[2], /data-testid="why"/);                               // why null: hidden
});

// ---------------------------------------------------------------------------
// Crew lead app
// ---------------------------------------------------------------------------

test("04 crew: status color and label come from the event", () => {
  const jake = realWorker("demo-1"), maria = realWorker("demo-2"), dev = realWorker("demo-3");
  const yellow = msgs("crew", "worker_status").find((e) => (e as { worker: WorkerStatus }).worker.status === "yellow")!;
  // The replay never went red while recording, so red is the same real payload with status "red".
  const s = applyEvents(crewBase(), [yellow, workerEvent(maria, { status: "red", minutes_to_danger: 25 }), workerEvent(dev, { status: "green" })]);
  const html = render(<CrewApp />, s);
  assert.match(row(html, jake.name), /border-l-warn/);
  assert.match(text(row(html, jake.name)), /Warning/);
  assert.match(row(html, maria.name), /border-l-danger/);                           // red even with 25 min to go:
  assert.match(text(row(html, maria.name)), /Danger/);                              // the UI doesn't recompute it
  assert.match(row(html, dev.name), /border-l-ok/);
  assert.match(text(row(html, dev.name)), / OK /);
});

test("05 crew: privacy, raw biometrics never show on crew screens even if a backend sends them", () => {
  const snap = snapshot("crew") as LiveEvent & { history: unknown; offsets: unknown };
  snap.history = { "demo-1": { vitals: [{ worker_id: "demo-1", timestamp: 1, hr: 163, skin_temp: 35.55, activity: 4, tilt: 0 }], estimates: [] } };
  snap.offsets = { "demo-2": { type: "offset_update", worker_id: "demo-2", offset_c: 0.4321, confidence: 0.5, n_checks: 2 } };
  const leaks: LiveEvent[] = [
    snap,
    { type: "vitals", vitals: { worker_id: "demo-1", timestamp: 2, hr: 163, skin_temp: 35.55, activity: 4, tilt: 0 } },
    ...msgs("demo", "estimate").map((e) => { (e as { estimate: Estimate }).estimate.core.mean = 39.12; return e; }),
    { type: "offset_update", worker_id: "demo-2", timestamp: 3, offset_c: 0.4321, confidence: 0.5, n_checks: 2, accepted: true } as unknown as LiveEvent,
    { type: "worker_status", worker: { ...realWorker("demo-1"), status: "yellow", minutes_to_danger: 18, hr: 171, core_temp: 39.87 } as WorkerStatus },
  ];
  const s = stateOf(CREW, leaks);
  assert.deepEqual(s.history, {});
  assert.deepEqual(s.offsets, {});
  for (const search of ["", "tab=sites"]) {
    const t = text(render(<CrewApp />, s, { path: "/", search }));
    for (const secret of ["163", "35.55", "39.12", "0.43", "-0.43", "171", "39.87"]) assert.ok(!t.includes(secret), `"${secret}" leaked on the crew screen`);
  }
  const t = text(render(<CrewApp />, s));
  assert.match(t, /Warning/);                                                       // status still shows
  assert.match(t, /in 18 min/);                                                     // and minutes to danger
});

test("06 crew: minutes to danger, 0 is now (red), null is --", () => {
  const s = applyEvents(crewBase(), [
    workerEvent(realWorker("demo-1"), { status: "red", minutes_to_danger: 0 }),
    workerEvent(realWorker("demo-2"), { status: "yellow", minutes_to_danger: 12 }),
    workerEvent(realWorker("demo-3"), { status: "green", minutes_to_danger: null }),
  ]);
  const html = render(<CrewApp />, s);
  const danger = (name: string) => row(html, name).split('data-testid="danger"')[1];
  assert.match(text(danger("Jake")), /Danger now/);
  assert.match(danger("Jake"), /text-danger-ink/);
  assert.match(text(danger("Maria")), /Danger in 12 min/);
  assert.match(text(danger("Dev")), /Danger -- /);
});

test("07 crew: lightning stop-work banner, 10-mile ring, 30-min countdown on the wall clock, reset by a new strike", () => {
  const ev = msgs("crew", "site_status").find((e) => (e as { site: SiteStatus }).site.site_id === "eia-663"
    && (e as { site: SiteStatus }).site.hazards.some((h) => h.stop_work)) as unknown as { type: "site_status"; site: { site_id: string; hazards: Record<string, unknown>[] } };
  assert.ok(ev, "recorded a real lightning stop at the crew site");
  const raw = ev.site.hazards.find((h) => (h as unknown as { kind: string }).kind === "lightning")!;
  const allClear = raw.all_clear_at as number;
  // Replay clock far from the wall clock: the countdown must still use the wall clock (real feed).
  const s = applyEvents(crewBase(), [ev as unknown as LiveEvent]);
  s.clock = { sim: allClear + 50_000, wall: allClear - 1800, speed: 10 };
  mock.timers.enable({ apis: ["Date"], now: (allClear - 1800) * 1000 });
  try {
    const t = text(render(<CrewApp />, s));
    assert.match(t, /Stop work/);
    assert.match(t, /30:00/);
    const rings = lightningRings(normalizeSite(clone(ev.site) as unknown as SiteStatus).hazards.find((h) => h.kind === "lightning"));
    assert.ok(rings.some((r) => r.kind === "stop" && r.radius_m === TEN_MILES_M), "10-mile ring");
    // A second strike 10 minutes later moves all_clear_at: back to 30:00.
    const second = clone(ev);
    const h2 = second.site.hazards.find((h) => (h as unknown as { kind: string }).kind === "lightning")!;
    h2.last_strike_at = (h2.last_strike_at as number) + 600;
    h2.all_clear_at = allClear + 600;
    mock.timers.setTime((allClear - 1200) * 1000);
    const s2 = applyEvents(s, [second as unknown as LiveEvent]);
    s2.clock = { ...s.clock, wall: allClear - 1200 };
    assert.match(text(render(<CrewApp />, s2)), /30:00/);
  } finally {
    mock.timers.reset();
  }
});

test("08 crew: sites ordered by tomorrow's index, highest first; no index last, shown as --", () => {
  const base = crewBase();
  const sites = SITES.slice(0, 4);
  const statuses: Record<string, SiteStatus> = {};
  const idx = [2.5, null, 7.1, 0];
  sites.forEach((x, i) => { statuses[x.id] = { ...(Object.values(base.sites)[0]), site_id: x.id, index_tomorrow: idx[i], hazard_index: null }; });
  assert.deepEqual(rankSites(sites, statuses).map((x) => statuses[x.id].index_tomorrow), [7.1, 2.5, 0, null]);
  const s = { ...base, sites: { ...base.sites, ...statuses } };
  const t = text(render(<CrewApp />, s, { path: "/", search: "tab=sites" }));
  const pos = (name: string) => t.indexOf(name);
  assert.ok(pos(sites[2].name) < pos(sites[0].name) && pos(sites[0].name) < pos(sites[3].name), "7.1 before 2.5 before 0");
  assert.match(t.slice(pos(sites[1].name)), /--/);
});

test("08 crew (real payload): sites with no index yet come through as null and show --", () => {
  // The merged backend sends index_tomorrow: null until session C2's index is wired in.
  const s = crewBase();
  const real = Object.values(s.sites);
  assert.ok(real.length > 0 && real.every((x) => x.index_tomorrow === null));
  const t = text(render(<CrewApp />, s, { path: "/", search: "tab=sites" }));
  assert.doesNotMatch(t, /\b0\.0\b|NaN/);
  assert.match(t, /--/);
});

test("09 crew: a confirmed man-down shows full screen on every page", () => {
  const confirmed = msgs("crew", "mandown").filter((e) => (e as { mandown: { stage: string; reason: string } | null }).mandown?.stage === "confirmed"
    && (e as { mandown: { reason: string } }).mandown.reason === "fall").at(-1)!;
  const md = (confirmed as { mandown: { worker_name: string; site_name: string } }).mandown;
  const pages: [ReactNode, ViewFilter, { path: string; search?: string; params?: Record<string, string> }][] = [
    [<CrewApp key="a" />, CREW, { path: "/" }],
    [<CrewApp key="b" />, CREW, { path: "/", search: "tab=sites" }],
    [<WorkerPhonePage key="c" />, { view: "worker", worker_id: "demo-1" }, { path: "/worker/demo-1", search: "details=1", params: { id: "demo-1" } }],
    [<DemoPage key="d" />, DEMO, { path: "/demo" }],
  ];
  for (const [node, filter, route] of pages) {
    const s = stateOf(filter, [snapshot(filter.view === "worker" ? "worker" : filter.view === "demo" ? "demo" : "crew"), clone(confirmed)]);
    const t = text(render(node, s, route));
    assert.match(t, /Man down: confirmed/i, route.path + (route.search ?? ""));
    assert.ok(t.includes(md.worker_name) && t.includes(md.site_name));
    assert.match(t, /Down for/);
    assert.match(t, /Est\. core temp/);                                             // last status (emergency exception)
  }
});

test("10 crew: an SOS shows the same full-screen alert, SOS from <worker>", () => {
  const sos = msgs("crew", "mandown").find((e) => (e as { mandown: { reason: string } | null }).mandown?.reason === "sos")!;
  const s = stateOf(CREW, [snapshot("crew"), ...msgs("crew", "sos"), clone(sos)]);
  const t = text(render(<CrewApp />, s));
  assert.match(t, /SOS from Dev/);
});

// Session D's events, in its documented order (docs/interfaces/emergency.md). The live feed
// (/ws/live) folds them into `emergency` objects, so this sequence is built here.
function dSequence(workerId: string, failed = false) {
  const base = { incident_id: `inc-${workerId}-1`, worker_id: workerId, worker_name: "Tasha", mode: "dry_run", number_masked: "+1 ••• ••• 2195" };
  const t0 = Date.now() / 1000;
  const ev = (i: number, stage: string, status: string, target: string | null, extra: Record<string, unknown> = {}) =>
    ({ type: "emergency_status", ...base, timestamp: t0 + i, stage, status, target, demo_label: target === "emergency" ? "Demo: would call 911" : null, cancel_deadline: t0 + 32, message: "", ...extra }) as unknown as LiveEvent;
  return [
    ev(0, "notify", "calling", null),
    ev(1, "notify", "completed", "crew_lead"),
    ev(2, "cancel_window", "waiting", null),
    ev(32, "emergency_call", "calling", "emergency"),
    ...(failed ? [ev(40, "done", "failed", "emergency")] : [ev(36, "emergency_call", "connected", "emergency"), ev(50, "emergency_call", "completed", "emergency"), ev(51, "done", "completed", null)]),
  ];
}

test("11 crew: emergency call steps show in order; a failed call shows the big call-911 box", () => {
  const fall = msgs("crew", "mandown").filter((e) => (e as { mandown: { stage: string; reason: string } | null }).mandown?.stage === "confirmed"
    && (e as { mandown: { reason: string } }).mandown.reason === "fall").at(-1)!;
  let s = stateOf(CREW, [snapshot("crew"), clone(fall)]);
  s = { ...s, emergency: null };
  const seen: string[] = [];
  let sawDemoLabel = false;
  for (const ev of dSequence("demo-4")) {
    s = applyEvents(s, [ev]);
    seen.push(s.emergency!.stage);
    const t = text(render(<CrewApp />, s));
    if (/Demo: would call 911/.test(t)) sawDemoLabel = true;
    if (s.emergency!.stage === "cancel_window") assert.match(t, /Calling in \d+:\d\d unless cancelled/);
  }
  assert.deepEqual([...new Set(seen)], ["notifying", "cancel_window", "calling", "connected", "done"]);
  assert.ok(sawDemoLabel, "Demo: would call 911 shown");
  let f: LiveState = { ...stateOf(CREW, [snapshot("crew"), clone(fall)]), emergency: null };
  f = applyEvents(f, dSequence("demo-4", true));
  assert.equal(f.emergency?.stage, "failed");
  assert.ok(text(render(<CrewApp />, f)).includes(CALL_FAILED_TEXT));
});

test("11 crew: a notify call that didn't go through never shows a green check", () => {
  const fall = msgs("crew", "mandown").filter((e) => (e as { mandown: { stage: string; reason: string } | null }).mandown?.stage === "confirmed"
    && (e as { mandown: { reason: string } }).mandown.reason === "fall").at(-1)!;
  let s: LiveState = { ...stateOf(CREW, [snapshot("crew"), clone(fall)]), emergency: null };
  const seq = dSequence("demo-4");
  // Same as the replay run without phone numbers in .env: the safety officer call fails.
  const skipped = { ...(seq[1] as object), target: "safety_officer", status: "failed", message: "No safety_officer number set in .env. Call skipped." } as unknown as LiveEvent;
  s = applyEvents(s, [seq[0], seq[1], skipped, seq[2]]);
  assert.deepEqual(s.emergency!.unreached, ["Safety officer"]);
  const t = text(render(<CrewApp />, s));
  assert.match(t, /! Notify crew lead \+ safety officer/);
  assert.doesNotMatch(t, /✓ Notify/);
  assert.match(t, /didn't go through to: Safety officer/);
  // The happy path keeps its check mark.
  const ok = applyEvents({ ...stateOf(CREW, [snapshot("crew"), clone(fall)]), emergency: null }, seq.slice(0, 3));
  assert.match(text(render(<CrewApp />, ok)), /✓ Notify/);
});

test("11 crew (real payload): the recorded call passes through calling and connected", { todo: "backend gap: in dry-run mode the live feed's emergency goes cancel_window -> done; no calling / connected step is ever sent, so those steps never light up" }, () => {
  const stages = new Set(msgs("crew", "emergency").map((e) => (e as { emergency: { stage: string } | null }).emergency?.stage));
  assert.ok(stages.has("calling") && stages.has("connected"), "our recording");
  if (hasJ) {
    // Session J's recording of session D's raw events, folded the way the UI does it.
    let st = stateOf(DEMO, [jFile("live/snapshot_demo.json")[0]]);
    const seen = new Set<string>();
    for (const ev of jFile("live/emergency_status.json")) { st = applyEvents(st, [ev]); seen.add(st.emergency!.stage); }
    assert.ok(seen.has("calling") && seen.has("connected"), "session J's recording");
  }
});

test("12 crew: cancel sends one request on a double tap, then the screen shows cancelled", async () => {
  const realCall = msgs("crew", "emergency").map((e) => (e as { emergency: { stage: string; worker_id: string } | null }).emergency).find((e) => e?.stage === "cancel_window")!;
  const sent: unknown[] = [];
  const cancel = singleFlight(async () => {
    const [path, body] = cancelCallRequest(realCall);
    sent.push([path, body]);
    await new Promise((r) => setTimeout(r, 5));
  });
  await Promise.all([cancel(), cancel()]);                                          // double tap
  assert.deepEqual(sent, [["/emergency/cancel", { worker_id: realCall.worker_id }]]);
  // The overlay shows the button in the window, and "cancelled" once the backend says so.
  const sos = msgs("crew", "mandown").find((e) => (e as { mandown: { reason: string } | null }).mandown?.reason === "sos")!;
  let s = stateOf(CREW, [snapshot("crew"), clone(sos), msgs("crew", "emergency").find((e) => (e as { emergency: { stage: string } }).emergency?.stage === "cancel_window")!]);
  assert.match(text(render(<CrewApp />, s)), /Cancel, false alarm/);
  s = applyEvents(s, [{ type: "emergency_status", incident_id: s.emergency!.id, worker_id: "demo-3", timestamp: Date.now() / 1000, stage: "done", status: "cancelled", target: null, message: "Cancelled by crew lead" } as unknown as LiveEvent]);
  assert.equal(s.emergency?.stage, "cancelled");
  assert.match(text(render(<CrewApp />, s)), /[Cc]ancel/);
  assert.doesNotMatch(text(render(<CrewApp />, s)), /Cancel, false alarm/);
});

// ---------------------------------------------------------------------------
// Worker app
// ---------------------------------------------------------------------------

const workerRoute = (search = "") => ({ path: "/worker/demo-2", search, params: { id: "demo-2" } });

test("13 worker: giant status color, hazard tile, next break from the planner's break_at", () => {
  const base = stateOf(WORKER, [snapshot("worker")]);
  const maria = base.workers["demo-2"];
  const s = applyEvents(base, [workerEvent(maria, { status: "green", break_at: base.clock.sim + 45 * 60, on_break: false })]);
  const html = render(<WorkerPhonePage />, s, workerRoute());
  const t = text(html);
  assert.match(t, /You're OK/);
  assert.match(html, /background:#0b7a0b/);                                         // green
  assert.match(t, /Hazard/);
  assert.match(t, /Next break in 4[45] min/);
  const y = applyEvents(s, [workerEvent(maria, { status: "yellow", minutes_to_danger: 15 })]);
  const yh = render(<WorkerPhonePage />, y, workerRoute());
  assert.match(text(yh), /Slow down/);
  assert.match(yh, /background:#fab219/);
});

test("14 worker: hold 3 s sends SOS; a short tap or letting go at 2.5 s sends nothing", () => {
  let now = 0, fired = 0;
  const hold = createHold(3000, () => { fired++; }, () => now);
  hold.start(); now = 300; hold.tick();
  assert.equal(hold.release(), false);                                              // short tap
  hold.start(); now += 2500; hold.tick();
  assert.equal(hold.release(), false);                                              // let go at 2.5 s
  assert.equal(fired, 0);
  hold.start(); now += 3000; hold.tick(); now += 500; hold.tick();
  assert.equal(fired, 1);                                                           // once, at 3 s
  assert.equal(hold.release(), true);
  const t = text(render(<WorkerPhonePage />, stateOf(WORKER, [snapshot("worker")]), workerRoute()));
  assert.match(t, /SOS hold 3 s/);
});

test("15 worker: spot check posts {worker_id, thermometer_c} once; bad input never leaves the phone", async () => {
  const posts: unknown[] = [];
  const post = async (path: string, body: unknown) => { posts.push([path, body]); return F.worker.offset_update[0]; };
  const ok = await sendSpotCheck("demo-2", "37.4", post, (o) => `${o}`);
  assert.equal(ok.sent, true);
  assert.deepEqual(posts, [["/spot-check", { worker_id: "demo-2", thermometer_c: 37.4 }]]);
  for (const bad of ["", "   ", "abc", "37,4.1", "33.9", "42.1", "-37"]) {
    const r = await sendSpotCheck("demo-2", bad, post, (o) => `${o}`);
    assert.equal(r.sent, false, `"${bad}" must not be sent`);
    assert.ok(r.text.length > 10 && !/NaN|undefined/.test(r.text), `plain message for "${bad}"`);
  }
  assert.equal(posts.length, 1);
  assert.deepEqual(parseThermometer("34.0"), { ok: true, value: 34 });
  assert.deepEqual(parseThermometer("42.0"), { ok: true, value: 42 });
});

test("16 worker: offset_update fills the learning card, and it comes back from the snapshot after a reload", () => {
  const [accepted, rejected] = msgs("worker", "offset_update");
  const live = stateOf(WORKER, [snapshot("worker"), accepted]);
  const t = text(render(<WorkerPhonePage />, live, workerRoute("details=1")));
  assert.match(t, /\+0\.07 °C hotter than average/);
  assert.match(t, /33% own history · 1 check/);
  const after = text(render(<WorkerPhonePage />, applyEvents(live, [acceptEvent(WORKER, rejected)!]), workerRoute("details=1")));
  assert.match(after, /1 check/);
  assert.match(after, /ignored/);
  // Page reload: the card comes back from /api/state's snapshot (real payload).
  const reloaded = text(render(<WorkerPhonePage />, stateOf(WORKER, [clone(F.rest.state_worker)]), workerRoute("details=1")));
  assert.match(reloaded, /\+0\.07 °C hotter than average/);
  assert.match(reloaded, /1 check/);
});

test("17 worker: why line shown when present, hidden when null", () => {
  const s = stateOf(WORKER, [snapshot("worker"), ...msgs("worker", "estimate")]);
  const why = s.history["demo-2"].estimates.at(-1)!.why!;
  const html = render(<WorkerPhonePage />, s, workerRoute("details=1"));
  assert.match(html, /data-testid="why"/);
  assert.ok(text(html).includes(why));
  const none = structuredClone(s);
  none.history["demo-2"].estimates = none.history["demo-2"].estimates.map((e) => ({ ...e, why: null }));
  assert.doesNotMatch(render(<WorkerPhonePage />, none, workerRoute("details=1")), /data-testid="why"/);
  assert.doesNotMatch(render(<DemoPage />, none, { path: "/demo", search: "worker=demo-2" }), /data-testid="why"/);
  assert.match(render(<DemoPage />, s, { path: "/demo", search: "worker=demo-2" }), /data-testid="why"/);
});

test("18 worker: forecast chart has the median, the 10-90 band and the danger line; demo threshold is labeled", () => {
  const est = clone(F.rest.state_worker.history["demo-2"].estimates) as Estimate[];
  const now = est.at(-1)!.timestamp;
  const c = buildCoreChart(est, now)!;
  const future = c.rows.filter((r) => r.t > 0);
  assert.equal(future.length, 30);
  assert.ok(future.every((r) => r.p50 !== undefined && r.band && r.band[0] <= r.p50! && r.p50! <= r.band[1]));
  assert.equal(c.danger.y, 38.5);
  assert.match(c.danger.label, /^danger 38\.5$/);
  const demo = est.map((e) => ({ ...e, thresholds: { warning: 37.3, danger: 37.5, demo: true } }));
  const d = buildCoreChart(demo, now)!;
  assert.equal(d.danger.y, 37.5);
  assert.match(d.danger.label, /Demo threshold 37\.5/);
});

// ---------------------------------------------------------------------------
// Judges' demo page
// ---------------------------------------------------------------------------

test("19 judges: results numbers come from one data file and match docs/results.md", () => {
  const row = (name: string) => RESULTS.rows.find((r) => r.name === name)!;
  // Final numbers: the 10 locked test people (docs/results.md, "FINAL: final_cv").
  assert.match(RESULTS.split, /10 unseen test people/);
  assert.equal(row("+20 min RMSE").values[0], 0.481);                              // published Kalman
  assert.equal(row("+20 min RMSE").values[RESULTS.best], 0.37);                    // ours (final_cv)
  assert.equal(row("RMSE").values[RESULTS.best], 0.347);                           // now
  assert.equal(row("+20 min coverage of p90 (want 90%)").values[RESULTS.best], 95);
  assert.equal(row("Yellow warnings caught").values[RESULTS.best], 88);
  assert.equal(row("Yellow warning lead time").values[RESULTS.best], 16.9);
  assert.equal(row("Yellow warning lead time").values[1], 13.0);                   // card 2 compares to the trend baseline
  assert.deepEqual(row("Yellow false alarms").values.slice(1), [0.23, 0.2]);
  // No red-alert win: on test the trend baseline caught 10/10, ours 9/10. The table says so.
  assert.equal(row("Red danger caught").values[1], 100);
  assert.equal(row("Red danger caught").values[RESULTS.best], 90);
  const ai = RESULTS.planner!.rows.find((r) => r.name.startsWith("AI alone"))!;
  const ours = RESULTS.planner!.rows.find((r) => r.name.startsWith("Our agent"))!;
  assert.equal(ai.runs_with_violations_pct, 92);
  assert.equal(ours.runs_with_violations_pct, 0);
  assert.equal(RESULTS.personalization!.rows[0].plus20_rmse_c, 0.308);
  assert.equal(RESULTS.personalization!.rows[1].plus20_rmse_c, 0.27);
  // The same numbers in the source documents on main.
  const results = readFileSync(join(DOCS, "results.md"), "utf8");
  const final = results.slice(results.indexOf("## FINAL: final_cv"));
  assert.ok(final.length > 100, "docs/results.md has the FINAL section");
  for (const n of ["0.481", "0.370", "0.347", "0.398", "0.95", "88%, **16.9 min**, **0.20**", "88%, 13.0 min, 0.23", "90% (9/10)", "100% (10/10)", "0.308 -> 0.270"]) {
    assert.ok(final.includes(n), `FINAL section has ${n}`);
  }
  assert.match(readFileSync(join(DOCS, "sessions", "planner.md"), "utf8"), /92% of runs have safety violations/);
  // And the page shows them (bundled copy, since no backend answers /api/results here).
  const t = text(render(<ResultsPage />, crewBase(), { path: "/demo/results" }));
  for (const n of ["0.37", "0.481", "16.9", "Simple trend: 13", "88% caught", "0.20 vs 0.23/hr", "92", "10 unseen test people", "now-only"]) assert.ok(t.includes(n), `results page shows ${n}`);
  assert.doesNotMatch(t, /Heat danger caught/);                                      // no red-alert headline
  assert.ok(!t.includes("0.456"), "the old validation Kalman number is gone (0.23 only appears in the note saying it was optimistic)");
});

test("20 judges: demo controls send the right request and show the current state", () => {
  assert.deepEqual(demoActions.testLightning(), ["/demo/test-alert", { kind: "LIGHTNING" }]);
  assert.deepEqual(demoActions.testFloodFire(), ["/demo/test-alert", { kind: "FLOOD_FIRE" }]);
  assert.deepEqual(demoActions.replay(true), ["/demo/replay", { on: true, speed: 10 }]);
  assert.deepEqual(demoActions.replay(false), ["/demo/replay", { on: false, speed: 1 }]);
  assert.deepEqual(demoActions.speed(30), ["/demo/replay", { speed: 30 }]);
  assert.deepEqual(demoActions.threshold({ demo_threshold: false }), ["/demo/threshold", { on: true }]);
  const real = crewBase();                                                          // real DemoState: replay at 10x
  const html = render(<DemoControls />, real, { path: "/demo" });
  assert.match(html, /aria-pressed="true"[^>]*>Replay</);
  assert.match(html, /aria-pressed="true"[^>]*>10x</);
  const live = { ...real, demo: { ...real.demo!, source: "live" as const, speed: 1, demo_threshold: true, thresholds: { warning: 37.3, danger: 37.5 } } };
  const lh = render(<DemoControls />, live, { path: "/demo" });
  assert.match(lh, /aria-pressed="true"[^>]*>Live</);
  assert.match(text(lh), /Demo threshold: ON \(37\.3 \/ 37\.5 °C\)/);
});

// ---------------------------------------------------------------------------
// Robustness (21 is in connection.test.tsx)
// ---------------------------------------------------------------------------

test("22 robustness: at 10x replay, worker data uses replay time; lightning and the call use the wall clock", () => {
  const callEv = msgs("crew", "emergency").find((e) => (e as { emergency: { stage: string } | null }).emergency?.stage === "cancel_window")!;
  const sos = msgs("crew", "mandown").find((e) => (e as { mandown: { reason: string } | null }).mandown?.reason === "sos")!;
  const deadline = (callEv as { emergency: { cancel_deadline: number } }).emergency.cancel_deadline;
  const wall = deadline - 20;
  let s = stateOf(CREW, [snapshot("crew"), clone(sos), callEv]);
  s = { ...s, clock: { sim: wall + 6000, wall, speed: 10 } };                       // replay ran ahead 100 minutes
  mock.timers.enable({ apis: ["Date"], now: wall * 1000 });
  try {
    assert.match(text(render(<CrewApp />, s)), /Calling in 0:20 unless cancelled/);   // D's deadline: wall clock
    const w = stateOf(WORKER, [snapshot("worker")]);
    const ws = applyEvents({ ...w, clock: { sim: wall + 6000, wall, speed: 10 } }, [workerEvent(w.workers["demo-2"], { break_at: wall + 6000 + 300 })]);
    assert.match(text(render(<WorkerPhonePage />, ws, workerRoute())), /Next break in 5 min/);   // break: replay time
  } finally {
    mock.timers.reset();
  }
});

test("21 robustness (header): the phone apps say reconnecting when the connection drops", () => {
  const t = text(render(<AppHeader title="Your crew" />, { ...crewBase(), connected: false }));
  assert.match(t, /Reconnecting/);
});

// ---------------------------------------------------------------------------
// Demo heart rate (the band has no pulse sensor): POST/GET /api/demo/heart_rate
// ---------------------------------------------------------------------------

test("demo heart rate: each button sends the right request, once on a double tap", async () => {
  const sent: [string, Record<string, unknown>][] = [];
  let release!: () => void;
  const shown: string[] = [];
  const pick = hrPicker((path, body) => { sent.push([path, body]); return new Promise((r) => { release = () => r({ ok: true, mode: body.mode }); }); }, (m) => shown.push(m));
  const first = pick("rest");
  await pick("rest");                                                                // second tap while the first is in flight
  release();
  await first;
  assert.deepEqual(sent, [["/demo/heart_rate", { mode: "rest" }]]);
  assert.deepEqual(shown, ["rest"]);
  for (const m of ["off", "active"] as const) {
    const got: unknown[] = [];
    await hrPicker(async (p, b) => { got.push([p, b]); return { mode: m }; }, () => {})(m);
    assert.deepEqual(got, [["/demo/heart_rate", { mode: m }]]);
  }
  // A rejected request leaves the old mode showing.
  const after: string[] = [];
  await hrPicker(async () => ({ ok: false, error: "nope" }), (m) => after.push(m))("active");
  assert.deepEqual(after, []);
});

test("demo heart rate: the active button comes from GET (survives a reload)", () => {
  assert.equal(readHrMode({ mode: "active" }), "active");
  assert.equal(readHrMode({ mode: "sprinting" }), null);
  assert.equal(readHrMode(null), null);
  const html = renderToString(<HeartRateButtons mode="rest" onPick={() => {}} />);
  const pressed = [...html.matchAll(/aria-pressed="true"[^>]*>([^<]+)</g)].map((m) => m[1]);
  assert.deepEqual(pressed, ["Resting (55-70 bpm)"]);
  for (const label of ["Off (real sensors)", "Running in place (130-155 bpm)"]) assert.ok(html.includes(label));
  // The mock backend answers GET with what was last POSTed.
  const engine = new MockEngine();
  assert.deepEqual(engine.handle("GET", ["demo", "heart_rate"], new URLSearchParams(), {}).json, { mode: "off" });
  engine.handle("POST", ["demo", "heart_rate"], new URLSearchParams(), { mode: "active" });
  assert.deepEqual(engine.handle("GET", ["demo", "heart_rate"], new URLSearchParams(), {}).json, { mode: "active" });
  assert.equal(engine.handle("POST", ["demo", "heart_rate"], new URLSearchParams(), { mode: "fast" }).status, 400);
});

test("demo heart rate: the tag shows only for demo_input, and never on the crew view", () => {
  const vitals = (src: string): LiveEvent => ({ type: "vitals", vitals: { worker_id: "demo-2", timestamp: 1792002700, hr: 142, skin_temp: 34.9, activity: 6, tilt: 0, hr_source: src } });
  const demoPage = (src: string) => text(render(<DemoPage />, stateOf(DEMO, [snapshot("demo"), vitals(src)]), { path: "/demo", search: "worker=demo-2" }));
  assert.ok(demoPage("demo_input").includes(DEMO_HR_TAG));
  assert.ok(!demoPage("watch").includes(DEMO_HR_TAG));
  // An estimate tagged demo_input also turns the tag on.
  const est = msgs("demo", "estimate").find((e) => (e as { estimate: Estimate }).estimate.worker_id === "demo-2");
  if (est) {
    (est as { estimate: Estimate }).estimate.hr_source = "demo_input";
    assert.ok(text(render(<DemoPage />, stateOf(DEMO, [snapshot("demo"), est]), { path: "/demo", search: "worker=demo-2" })).includes(DEMO_HR_TAG));
  }
  // Crew: vitals never get in, so no heart rate and no tag.
  const crew = stateOf(CREW, [snapshot("crew"), vitals("demo_input")]);
  assert.deepEqual(crew.history, {});
  for (const search of ["", "tab=sites"]) {
    const t = text(render(<CrewApp />, crew, { path: "/", search }));
    assert.ok(!t.includes(DEMO_HR_TAG) && !t.includes("142") && !/bpm/.test(t), "no heart rate on the crew view");
  }
});
