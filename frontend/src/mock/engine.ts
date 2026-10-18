/**
 * The MOCK backend. It pretends to be session A's live system so the
 * frontend works with no Python running and no hardware.
 *
 * What it does:
 * - Simulates 6 workers on 2 sites (heart rate, skin temp, core temp).
 * - Runs the real ECTemp Kalman equations on the fake heart rate (thin baseline line).
 * - Makes a fake 30-minute forecast with a 10/50/90th percentile band.
 * - Applies the spec's heat alert rules, man-down, lightning 10-mile rule, etc.
 * - Plays the scripted demo (see scenario.ts).
 * - Sends everything as LiveEvents, filtered by the privacy rule (canSee).
 *
 * The physiology here is FAKE and simple. It only has to look believable.
 * The real numbers come from backend/models once session A wires them up.
 */

import type {
  Alert, AlertKind, BandCommand, Briefing, DemoState, EmergencyCall, EmergencyStage, EmergencyStatus, Estimate, Hazard,
  HazardIndex, HazardKind, HazardLevel, LiveEvent, LogEntry, ManDown, PhotoReport, ReportKind, Schedule, SiteStatus,
  OffsetUpdate, Snapshot, SosPress, SpotCheckResult, Status, TaskKind, ViewFilter, Vitals, WorkerStatus,
} from "../lib/contract.ts";
import { canSee, HAZARD_ORDER } from "../lib/contract.ts";
import { CALL_FAILED_TEXT, isFinalStage } from "../lib/emergency.ts";
import { SITE_BY_ID, SITES } from "../lib/sites.ts";
import { SCENARIO, SCENARIO_END_MIN, scenarioReactions } from "./scenario.ts";
import { DEMO_TEAM, FRIENDS_TEAM, friendNames, ownerName } from "./teams.ts";
import { alertsForSite, fetchFloridaAlerts, fetchSighting, fetchWeather, heatIndexF, wbgtShadeC, type NwsAlert, type Sighting, type SiteWeather } from "./realdata.ts";
import { LABEL, RECORDED_JULY_DAY, planDay, type DayPlan, type HourWx } from "./planner.ts";

// ---------------------------------------------------------------------------
// Numbers mirrored from backend/config.py (the backend is the source of truth;
// the real server sends the thresholds it uses in every Estimate).
// ---------------------------------------------------------------------------
export const CFG = {
  HEAT_WARNING_C: 38.0,
  HEAT_DANGER_C: 38.5,
  DEMO_HEAT_WARNING_C: 37.3,
  DEMO_HEAT_DANGER_C: 37.5,
  ALERT_CLEAR_MARGIN_C: 0.1,
  HEAT_RED_ACK_TIMEOUT_S: 60,
  KALMAN: { gamma: 0.022, sigma: 18.88, b2: -4.5714, b1: 384.4286, b0: -7887.1, ct0: 37.1 },
  LIGHTNING_RADIUS_MI: 10,
  LIGHTNING_ALL_CLEAR_MIN: 30,
};

// Shortened timings so the man-down part of the demo fits in about a minute.
// Real values (MANDOWN_STILL_S = 45, MANDOWN_ESCALATE_S = 60) live in backend/config.py.
export const DEMO_TIMINGS = {
  MANDOWN_STILL_S: 10,       // no movement this long -> man-down check (band buzzes)
  MANDOWN_ESCALATE_S: 15,    // not cancelled this long -> confirmed, crew lead alerted
  NOTIFY_S: 3,               // crew lead + safety officer notified, then the cancel window opens
  CANCEL_WINDOW_S: 10,       // crew lead can press "cancel, false alarm" (= EMERGENCY_DEMO_CANCEL_WINDOW_S, session D)
  RING_S: 4,                 // phone rings before the dispatcher picks up
};

// Personal offset (same numbers as backend/models/personal_offset.py).
export const OFFSET = { PRIOR_CHECKS: 2, MAX_OFFSET_C: 0.6, MAX_CHECK_MISS_C: 1.5 };

const VITALS_EVERY_S = 5;        // Apple Watch sends heart rate about every 5 s
const HISTORY_VITALS_S = 20 * 60;
const HISTORY_ESTIMATES_MIN = 90;
const FORECAST_MIN = 30;
const SITE_WBGT_DEFAULT = 31;    // °C, a hot Gainesville afternoon

export const CREW_LEAD = "Chris Alvarez";
export const PRIMARY_SITE = "fl-deerhaven";
export const DEMO_WORKER = "worker-1";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/** Seeded random numbers, so every run of the demo looks the same. */
function makeRng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

const round = (x: number, d = 2) => Math.round(x * 10 ** d) / 10 ** d;
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));

/** Today at hh:mm, local time, as Unix seconds. */
function todayAt(hh: number, mm = 0, base = Date.now()): number {
  const d = new Date(base);
  d.setHours(hh, mm, 0, 0);
  return d.getTime() / 1000;
}

function clockText(t: number): string {
  return new Date(t * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

/** A point `miles` away from (lat, lon) in direction `bearingDeg`. */
function offset(lat: number, lon: number, miles: number, bearingDeg: number) {
  const b = (bearingDeg * Math.PI) / 180;
  const dLat = (miles / 69.0) * Math.cos(b);
  const dLon = (miles / (69.0 * Math.cos((lat * Math.PI) / 180))) * Math.sin(b);
  return { lat: lat + dLat, lon: lon + dLon };
}

// ---------------------------------------------------------------------------
// Workers
// ---------------------------------------------------------------------------

interface WorkerDef {
  worker_id: string;
  name: string;
  role: string;
  site_id: string;
  restHr: number;
  effort: number;          // how hard this person works on a "heavy" slot (0-1)
  breakOffset: number;     // which half-hour their breaks fall on
  trueOffset: number;      // how much hotter (+) this body really runs than the model reads (hidden; spot checks find it)
  team?: string;           // which saved crew ("account") this person is in; missing = demo (see teams.ts)
}

const WORKERS: WorkerDef[] = [
  { worker_id: "worker-1", name: "Maria Lopez", role: "Lineworker (wearing the band)", site_id: "fl-deerhaven", restHr: 68, effort: 0.3, breakOffset: 0, trueOffset: 0.25 },
  { worker_id: "worker-2", name: "Jake Turner", role: "Lineworker", site_id: "fl-deerhaven", restHr: 72, effort: 0.55, breakOffset: 1, trueOffset: 0.1 },
  { worker_id: "worker-3", name: "Dev Patel", role: "Electrician", site_id: "fl-deerhaven", restHr: 64, effort: 0.35, breakOffset: 2, trueOffset: -0.15 },
  { worker_id: "worker-4", name: "Sam Rivera", role: "Equipment operator", site_id: "fl-deerhaven", restHr: 70, effort: 0.3, breakOffset: 3, trueOffset: 0 },
  { worker_id: "worker-5", name: "Priya Shah", role: "Technician", site_id: "fl-kelly", restHr: 66, effort: 0.38, breakOffset: 1, trueOffset: -0.1 },
  { worker_id: "worker-6", name: "Marcus Bell", role: "Groundworker", site_id: "fl-kelly", restHr: 74, effort: 0.36, breakOffset: 2, trueOffset: 0.2 },
];

/**
 * Everyone the engine simulates: the demo crew (the 6 made-up workers above + Aiden) and the
 * friends crew (Aiden + friends). Real names come from Render's private settings (teams.ts).
 */
function allWorkers(): WorkerDef[] {
  const demo: WorkerDef[] = WORKERS.map((w) => ({ ...w, team: DEMO_TEAM }));
  demo.push({ worker_id: "worker-7", name: ownerName(), role: "Crew lead", site_id: "fl-deerhaven", restHr: 66, effort: 0.32, breakOffset: 3, trueOffset: 0.05, team: DEMO_TEAM });
  const friends: WorkerDef[] = [ownerName(), ...friendNames()].map((name, i) => ({
    worker_id: `friend-${i + 1}`, name, role: i === 0 ? "Crew lead" : "Crew", site_id: "fl-deerhaven",
    restHr: 64 + ((i * 5) % 11), effort: 0.28 + ((i * 7) % 5) * 0.04, breakOffset: i % 4,
    trueOffset: (((i * 3) % 5) - 2) * 0.06, team: FRIENDS_TEAM,
  }));
  return [...demo, ...friends];
}

interface WorkerSim {
  def: WorkerDef;
  core: number;                 // the "true" simulated core temp
  intensity: number;            // current workload, 0-1
  scriptedIntensity: number | null;
  hr: number;
  skin: number;
  collapsed: boolean;
  stillSince: number | null;    // when movement stopped (collapsed)
  minuteHr: number[];           // HR readings in the current minute (for 1-min average)
  hrByMinute: number[];         // 1-min average HR history (for the "why" line)
  kalman: { ct: number; v: number };
  status: Status;
  vitals: Vitals[];
  estimates: Estimate[];
  lastBand: string;
  heatAlertId: string | null;
  rawEstimate: number;          // the model's estimate before the personal offset
}

/** Fake physiology: core temp drifts toward a target set by workload and heat. */
function coreTarget(intensity: number, wbgt: number) {
  return 36.9 + 1.9 * intensity + 0.045 * (wbgt - 26);
}
const CORE_RATE = 0.06; // per minute

function fakeHr(w: WorkerSim, rng: () => number) {
  const base = w.def.restHr + 68 * w.intensity + 22 * (w.core - 37);
  return base + (rng() - 0.5) * 6;
}

/** ECTemp (Buller et al. 2013), the same equations as backend/models/kalman.py. */
function kalmanStep(k: { ct: number; v: number }, hr: number) {
  const { gamma, sigma, b2, b1, b0 } = CFG.KALMAN;
  const ctPrior = k.ct;
  const vPrior = k.v + gamma * gamma;
  const c = 2 * b2 * ctPrior + b1;
  const gain = (vPrior * c) / (c * c * vPrior + sigma * sigma);
  const h = b2 * ctPrior * ctPrior + b1 * ctPrior + b0;
  k.ct = ctPrior + gain * (hr - h);
  k.v = (1 - gain * c) * vPrior;
}

// ---------------------------------------------------------------------------
// Site hazards (baseline per site, plus anything the demo triggers)
// ---------------------------------------------------------------------------

interface SiteSim {
  hazards: Record<HazardKind, Hazard>;
  index: number;
  drivers: string[];
  wbgt: number;
  hazardIndex: HazardIndex;
}

function heatLevel(wbgt: number): HazardLevel {
  if (wbgt >= 32) return "extreme";
  if (wbgt >= 30) return "high";
  if (wbgt >= 27.8) return "moderate";
  return "low";
}

function noneHazard(kind: HazardKind, t: number, headline: string, source: string): Hazard {
  return { kind, level: "none", headline, detail: "", source, updated_at: t, stop_work: false, band_pattern: null };
}

/** One hazard's slice of tomorrow's 0-10 index (same shape as session C2's predict_index). */
function indexPart(x: number) {
  return {
    index: round(x, 1), p_advisory: round(clamp(x / 5, 0, 1), 2), p_warning: round(clamp((x - 3) / 6, 0, 1), 2),
    p_damage: round(clamp((x - 6) / 10, 0, 1), 2), climatology_index: round(x * 0.55, 1),
  };
}

const CREW_SITES = new Set(WORKERS.map((w) => w.site_id).concat("fl-deerhaven"));

function baselineSite(siteId: string, t: number): SiteSim {
  const s = SITE_BY_ID[siteId];
  const rng = makeRng(hashString(siteId));
  // Hotter further south, a bit cooler on the coast. Crew sites pinned to a known value.
  const south = clamp((30.6 - s.lat) / 5, 0, 1);
  let wbgt = round(28.2 + 2.6 * south + (rng() - 0.5) * 2.2 - (s.coastal ? 0.4 : 0), 1);
  if (siteId === "fl-deerhaven" || siteId === "fl-kelly" || siteId === "fl-grec") wbgt = SITE_WBGT_DEFAULT;
  const hi = Math.round(92 + (wbgt - 27) * 4.2);

  const hazards = {} as Record<HazardKind, Hazard>;
  hazards.heat = {
    kind: "heat", level: heatLevel(wbgt),
    headline: `WBGT ${wbgt} °C · heat index ${hi} °F`,
    detail: wbgt >= 30 ? "Hard work needs breaks in the shade every hour." : "Normal heat precautions.",
    source: "Open-Meteo + Liljegren WBGT", updated_at: t, stop_work: false, band_pattern: null,
    heat: { wbgt_c: wbgt, heat_index_f: hi },
  };
  hazards.lightning = { ...noneHazard("lightning", t, "No strikes within 10 mi", "GOES-19 GLM"),
    lightning: { nearest_miles: null, last_strike_at: null, all_clear_at: null, strikes: [] } };

  const floodRoll = rng();
  hazards.flood = floodRoll > 0.88
    ? { kind: "flood", level: "moderate", headline: "River at minor flood stage", detail: "Nearest gauge is above minor flood stage. Avoid low access roads.", source: "NWS river gauges (sample)", updated_at: t, stop_work: false, band_pattern: null }
    : s.coastal && floodRoll > 0.6
      ? { kind: "flood", level: "low", headline: "High tide this evening", detail: "Water level 0.4 ft below minor flood.", source: "NOAA Tides & Currents (sample)", updated_at: t, stop_work: false, band_pattern: null }
      : noneHazard("flood", t, "No flood alerts", "NWS alerts (sample)");

  const fireRoll = rng();
  hazards.fire = siteId === "fl-crist" || siteId === "fl-hopkins"
    ? { kind: "fire", level: "high", headline: "Red Flag Warning", detail: "Low humidity and gusty wind. No hot work outdoors.", source: "NWS alerts (sample)", updated_at: t, stop_work: false, band_pattern: "FLOOD_FIRE" }
    : fireRoll > 0.85
      ? { kind: "fire", level: "low", headline: "Satellite hotspot 14 mi away", detail: "One VIIRS detection today, likely a prescribed burn.", source: "NASA FIRMS (sample)", updated_at: t, stop_work: false, band_pattern: null }
      : noneHazard("fire", t, "No fires nearby", "NASA FIRMS + NIFC (sample)");

  const soil = Math.round(35 + rng() * 55);
  hazards.soil = {
    kind: "soil", level: soil >= 80 ? "moderate" : soil >= 65 ? "low" : "none",
    headline: `Soil moisture ${soil}% of saturation`,
    detail: soil >= 80 ? "Soft ground. Check footing for bucket trucks and outriggers." : "Ground is firm enough for heavy equipment.",
    source: "Open-Meteo soil moisture (sample)", updated_at: t, stop_work: false, band_pattern: null,
  };

  rng();   // (keeps the random sequence the same for everything below)
  // Real sightings replace this once iNaturalist answers (see MockEngine.applyLive). Checked at crew sites only.
  hazards.wildlife = siteId === "fl-deerhaven"
    ? { kind: "wildlife", level: "low", headline: "Alligator seen 0.4 mi away", detail: "Sample sighting near the retention pond.", source: "iNaturalist (sample)", updated_at: t, stop_work: false, band_pattern: null }
    : noneHazard("wildlife", t, "Checked at crew sites only", "iNaturalist");

  // Tomorrow's 0-10 index: heat drives most of it, plus some weather luck.
  const storms = rng();
  let index = 1.2 + (wbgt - 27) * 1.1 + storms * 2.6 + (hazards.flood.level !== "none" ? 1.2 : 0) + (hazards.fire.level === "high" ? 2 : 0) + (soil >= 80 ? 0.6 : 0);
  if (siteId === "fl-deerhaven") index = 7.4;
  index = round(clamp(index, 0.3, 9.8), 1);
  const drivers: string[] = [`Heat index ${hi + 2} °F tomorrow`];
  if (storms > 0.5 || siteId === "fl-deerhaven") drivers.push(`Thunderstorms ${Math.round(30 + storms * 50)}% after 3 PM`);
  if (hazards.flood.level !== "none") drivers.push(hazards.flood.headline);
  if (hazards.fire.level === "high") drivers.push("Red Flag Warning");
  if (soil >= 80) drivers.push("Saturated soil");
  // Same shape as session C2's predict_index(), with mock numbers.
  const heatIdx = clamp((wbgt - 27) * 1.15, 0, 7);
  const stormIdx = clamp(storms * 3.2 + (siteId === "fl-deerhaven" ? 1.5 : 0), 0, 7);
  const floodIdx = hazards.flood.level === "moderate" ? 2.5 : hazards.flood.level === "low" ? 0.8 : 0.2;
  const fireIdx = hazards.fire.level === "high" ? 3 : hazards.fire.level === "low" ? 0.6 : 0.1;
  const tropIdx = s.coastal ? 0.4 : 0.1;
  const part = (x: number) => ({
    index: round(x, 1), p_advisory: round(clamp(x / 5, 0, 1), 2), p_warning: round(clamp((x - 3) / 6, 0, 1), 2),
    p_damage: round(clamp((x - 6) / 10, 0, 1), 2), climatology_index: round(x * 0.55, 1),
  });
  const reasons: HazardIndex["top_reasons"] = [
    { hazard: "heat", feature: "hi_max_today", value: hi + 2, points: round(heatIdx * 0.45, 1), text: `Heat index reached ${hi + 2}°F by 6 PM today` },
  ];
  if (storms > 0.5 || siteId === "fl-deerhaven") reasons.push({ hazard: "storm", feature: "spc_day2_cat", value: 2, points: round(stormIdx * 0.4, 1), text: "SPC storm outlook for tomorrow: marginal to slight risk" });
  if (hazards.flood.level !== "none") reasons.push({ hazard: "flood", feature: "rain_72h", value: 3.1, points: 0.8, text: "3.1 in of rain in the last 3 days" });
  if (hazards.fire.level === "high") reasons.push({ hazard: "fire", feature: "red_flag", value: 1, points: 1.5, text: "Red Flag Warning in effect into tomorrow" });
  const tomorrow = new Date(t * 1000 + 86400000);
  const hazardIndex: HazardIndex = {
    site_id: siteId, date: tomorrow.toISOString().slice(0, 10), index,
    band: index < 2 ? "low" : index < 4 ? "moderate" : index < 6 ? "high" : "extreme",
    climatology_index: round(index * 0.6, 1),
    p_advisory: round(clamp(index / 5, 0, 1), 2), p_warning: round(clamp((index - 3) / 6, 0, 1), 2), p_damage: round(clamp((index - 6) / 10, 0, 1), 2),
    hazards: { heat: part(heatIdx), storm: part(stormIdx), flood: part(floodIdx), fire: part(fireIdx), tropical: part(tropIdx) },
    top_reasons: reasons.sort((a, b) => b.points - a.points).slice(0, 3),
    cutoff: new Date(todayAt(18, 0, t * 1000) * 1000).toISOString(),
    data_mode: "mock", model_version: "mock",
    notes: ["MOCK numbers from the frontend. The real index comes from session C2 (ml/hazard_index)."],
  };
  return { hazards, index, drivers: drivers.slice(0, 3), wbgt, hazardIndex };
}

// ---------------------------------------------------------------------------
// The engine
// ---------------------------------------------------------------------------

interface Subscriber {
  filter: ViewFilter;
  send: (ev: LiveEvent) => void;
  close?: () => void;          // ends the connection (used when this engine is replaced)
}

export interface EngineOptions {
  /** Wall clock in seconds. Tests pass a fake one. */
  wallNow?: () => number;
  /** "Today", used to anchor the sim clock. */
  baseDate?: number;
  /** How the emergency call is sent. See MockEngine.emergencyFormat. */
  emergencyFormat?: EmergencyFormat;
  /** Run on the real clock (the hosted site). Off = a fixed 1 PM start (tests, scripted replay). */
  realTime?: boolean;
}

/**
 * "call"   = one `emergency` event with the whole EmergencyCall (has the live transcript).
 * "status" = session D's small `emergency_status` events, folded by the UI (no transcript, like D).
 */
export type EmergencyFormat = "call" | "status";

// Pretend phone numbers for the "status" format, already masked like session D does.
const MASKED: Record<"crew_lead" | "safety_officer" | "emergency", string> = {
  crew_lead: "+1 ••• ••• 0101",
  safety_officer: "+1 ••• ••• 0102",
  emergency: "+1 ••• ••• 0199",
};

/** The fields of a D event that change from step to step. The rest are filled in by emitCall. */
type StatusStep = Pick<EmergencyStatus, "stage" | "status"> & Partial<Pick<EmergencyStatus, "target" | "message">>;

export class MockEngine {
  wallNow: () => number;
  baseDate: number;
  rng = makeRng(42);
  subs = new Set<Subscriber>();
  lastWall = 0;
  sim = 0;                        // sim clock, Unix seconds
  workers = new Map<string, WorkerSim>();
  sites = new Map<string, SiteSim>();
  alerts: Alert[] = [];
  mandown: ManDown | null = null;
  emergency: EmergencyCall | null = null;
  reports: PhotoReport[] = [];
  log: LogEntry[] = [];
  band: BandCommand[] = [];
  demo: DemoState;
  scenarioStart: number | null = null;   // sim time the scripted demo started
  scenarioDone = new Set<number>();      // which scenario steps already ran
  nextId = 1;
  timer: ReturnType<typeof setInterval> | null = null;
  /** Which event shape the emergency call goes out as ("call" or session D's "status"). */
  emergencyFormat: EmergencyFormat;
  /** Demo switch: the next emergency call fails instead of connecting (tests the "Call 911 yourself" screen). */
  failNextCall = false;
  /** On the real clock? (see EngineOptions.realTime) */
  realTime = false;
  /** Day plans by day, cached (see planFor). */
  planCache = new Map<string, DayPlan>();
  /** Changes to today's plan during the day (heat alerts): who, when, what. */
  adjustments: { worker_id: string; from: number; to: number; task: TaskKind; label: string }[] = [];
  /** What changed today and why, shown under the plan ("1:12 PM · Noah: heat warning ..."). */
  planChanges: { at: number; text: string }[] = [];
  /** When each site's current lightning stop-work began (so the plan only marks it from then on). */
  stopStart = new Map<string, number>();
  /** Video mode: which crew the scenes act on, and timed follow-ups (red-alert ack, second strike). */
  videoTeam = DEMO_TEAM;
  videoHeatTarget: string | null = null;
  videoTimers: { at: number; run: () => void }[] = [];
  /** Phone locations workers chose to share with their crew lead (kept across demo resets). */
  locations = new Map<string, { lat: number; lon: number; accuracy_m: number | null; at: number }>();
  /** Real outside data (Open-Meteo weather for every site, iNaturalist wildlife at crew sites). */
  live: { weather: Record<string, SiteWeather>; sightings: Record<string, Sighting | null>; alerts: NwsAlert[] | null; weatherAt: number; wildlifeAt: number } =
    { weather: {}, sightings: {}, alerts: null, weatherAt: 0, wildlifeAt: 0 };
  liveTimer: ReturnType<typeof setInterval> | null = null;

  constructor(opts: EngineOptions = {}) {
    this.wallNow = opts.wallNow ?? (() => Date.now() / 1000);
    this.baseDate = opts.baseDate ?? Date.now();
    this.emergencyFormat = opts.emergencyFormat ?? "call";
    this.realTime = !!opts.realTime;
    this.demo = {
      source: "live", speed: 1, demo_threshold: false,
      thresholds: { warning: CFG.HEAT_WARNING_C, danger: CFG.HEAT_DANGER_C },
      scenario: { running: false, step: null, started_at: null }, mock: true,
    };
    this.reset(this.realTime ? Math.floor(this.wallNow()) : todayAt(13, 0, this.baseDate));
  }

  /** Back to the real clock after a replay (hosted site only). */
  backToRealTime() {
    if (!this.realTime) return;
    this.demo = { ...this.demo, source: "live", speed: 1 };
    this.reset(Math.floor(this.wallNow()));
    this.resendSnapshots();
    this.emitDemo();
  }

  // ---- lifecycle ---------------------------------------------------------

  /** Start ticking on a real timer (the dev server calls this once). */
  start(periodMs = 500) {
    if (this.timer) return;
    this.timer = setInterval(() => this.tick(), periodMs);
  }

  /**
   * Pull real weather and wildlife (the server calls this; tests don't, so they never touch the network).
   * Checks every minute: weather when 15 min old, wildlife when 1 h old. Failures keep the last good data.
   */
  startLiveData() {
    if (this.liveTimer) return;
    const run = () => { void this.refreshLive(); };
    run();
    this.liveTimer = setInterval(run, 60_000);
  }

  async refreshLive(now = Date.now()) {
    const jobs: Promise<unknown>[] = [];
    if (now - this.live.weatherAt >= 15 * 60_000) {
      this.live.weatherAt = now;
      jobs.push(fetchWeather(SITES)
        .then((w) => { Object.assign(this.live.weather, w); })
        .catch((e) => { this.live.weatherAt = now - 13 * 60_000; console.warn("live weather failed, retrying in 2 min:", String(e)); }));
      jobs.push(fetchFloridaAlerts()
        .then((a) => { this.live.alerts = a; })
        .catch((e) => console.warn("NWS alerts failed (keeping the last ones):", String(e))));
    }
    if (now - this.live.wildlifeAt >= 60 * 60_000) {
      this.live.wildlifeAt = now;
      jobs.push((async () => {
        for (const id of CREW_SITES) {
          const site = SITE_BY_ID[id];
          if (!site) continue;
          try { this.live.sightings[id] = await fetchSighting(site, now); }
          catch (e) { console.warn(`live wildlife failed for ${id}:`, String(e)); }
        }
      })());
    }
    if (!jobs.length) return;
    await Promise.all(jobs);
    for (const s of SITES) { this.applyLive(s.id); this.emitSite(s.id); }
  }

  /** The recorded July day (video replay) as a weather reading for the current sim hour. */
  recordedWeather(): SiteWeather {
    const hour = new Date(this.sim * 1000).getHours();
    const r = RECORDED_JULY_DAY.find((h) => h.hour === hour) ?? RECORDED_JULY_DAY[0];
    const his = RECORDED_JULY_DAY.map((h) => Math.round(heatIndexF(h.tempF, h.rh)));
    return {
      tempF: r.tempF, rh: r.rh, windMph: 6, hiNowF: Math.round(heatIndexF(r.tempF, r.rh)),
      wbgtC: Math.round(wbgtShadeC(((r.tempF - 32) * 5) / 9, r.rh) * 10) / 10,
      hiMaxTodayF: Math.max(...his), hiMaxTomorrowF: Math.max(...his),
      stormChanceTomorrow: 70, thunderTomorrow: true, soilMoisture: 0.31, hours: [], at: this.sim,
    };
  }

  /** Put the real data into one site: heat now, tomorrow's heat and storm part of the index, wildlife. */
  applyLive(siteId: string) {
    const sim = this.sites.get(siteId);
    if (!sim) return;
    // Video mode shows the recorded July day at the crew sites
    const w = this.demo.scene && CREW_SITES.has(siteId) ? this.recordedWeather() : this.live.weather[siteId];
    if (w) {
      const hi = w.hiNowF;
      const level: HazardLevel = hi >= 103 ? "extreme" : hi >= 91 ? "high" : hi >= 80 ? "moderate" : "none";
      sim.hazards.heat = {
        kind: "heat", level,
        // Heat index only means something from 80 °F up; below that, just the air temperature and humidity.
        headline: hi >= 80 ? `Heat index ${hi} °F · ${Math.round(w.tempF)} °F, ${w.rh}% humidity` : `${Math.round(w.tempF)} °F, ${w.rh}% humidity`,
        detail: hi >= 103 ? `Dangerous heat. Shade breaks every hour, water every 15 minutes. WBGT about ${w.wbgtC} °C in the shade.`
          : hi >= 91 ? `Hot. Take shade breaks and drink water often. WBGT about ${w.wbgtC} °C in the shade.`
            : hi >= 80 ? `Warm. Normal heat precautions. WBGT about ${w.wbgtC} °C in the shade.`
              : `Comfortable right now. Today's peak heat index: ${w.hiMaxTodayF} °F.`,
        source: this.demo.scene && CREW_SITES.has(siteId) ? "Replay: recorded July day" : "Open-Meteo (live) · WBGT shade estimate", updated_at: w.at, stop_work: false, band_pattern: null,
        heat: { wbgt_c: w.wbgtC, heat_index_f: Math.max(hi, Math.round(w.tempF)) },
      };
      // The scripted replay keeps its recorded hot afternoon at the crew sites; live mode uses the real air.
      if (!((this.demo.scenario.running || this.demo.scene) && CREW_SITES.has(siteId))) sim.wbgt = w.wbgtC;

      // Tomorrow's index: heat and storms from the real forecast; flood and fire parts stay as they were.
      const old = sim.hazardIndex;
      const heatIdx = clamp((w.hiMaxTomorrowF - 88) / 4, 0, 7);
      const stormIdx = clamp((w.stormChanceTomorrow / 100) * 3 + (w.thunderTomorrow ? 2.5 : 0), 0, 7);
      const flood = old.hazards.flood?.index ?? 0.2, fire = old.hazards.fire?.index ?? 0.1, trop = old.hazards.tropical?.index ?? 0.1;
      const index = round(clamp(0.6 + heatIdx * 0.85 + stormIdx * 0.5 + flood * 0.6 + fire * 0.6 + trop * 0.3, 0.3, 9.8), 1);
      const heatText = `Heat index up to ${w.hiMaxTomorrowF}°F tomorrow`;
      const stormText = w.thunderTomorrow ? `Thunderstorms possible tomorrow (${w.stormChanceTomorrow}% rain chance)` : `${w.stormChanceTomorrow}% chance of rain tomorrow`;
      const reasons: HazardIndex["top_reasons"] = [
        { hazard: "heat", feature: "hi_max_tomorrow", value: w.hiMaxTomorrowF, points: round(heatIdx * 0.85, 1), text: heatText },
        ...(w.stormChanceTomorrow >= 30 || w.thunderTomorrow
          ? [{ hazard: "storm" as const, feature: "rain_chance_tomorrow", value: w.stormChanceTomorrow, points: round(stormIdx * 0.5, 1), text: stormText }] : []),
        ...old.top_reasons.filter((r) => r.hazard === "flood" || r.hazard === "fire"),
      ];
      sim.index = index;
      sim.drivers = reasons.map((r) => r.text);
      sim.hazardIndex = {
        ...old, index, band: index < 2 ? "low" : index < 4 ? "moderate" : index < 6 ? "high" : "extreme",
        climatology_index: round(index * 0.6, 1),
        p_advisory: round(clamp(index / 5, 0, 1), 2), p_warning: round(clamp((index - 3) / 6, 0, 1), 2), p_damage: round(clamp((index - 6) / 10, 0, 1), 2),
        hazards: { ...old.hazards, heat: indexPart(heatIdx), storm: indexPart(stormIdx) },
        top_reasons: reasons.sort((a, b) => b.points - a.points).slice(0, 3),
        data_mode: "live",
      };
    }
    // National Weather Service alerts for this site: flood, fire weather, heat, storms (real, live)
    if (this.live.alerts) {
      const t = Math.round(Date.now() / 1000);
      const site = SITE_BY_ID[siteId];
      const here = alertsForSite(this.live.alerts, site);
      const until = (a: NwsAlert) => a.ends ? ` until ${new Date(a.ends).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" })}` : "";
      const pick = (re: RegExp) => here.filter((a) => re.test(a.event)).sort((a, b) => Number(/Warning/.test(b.event)) - Number(/Warning/.test(a.event)))[0];
      const flood = pick(/Flood/), fire = pick(/Red Flag|Fire/), heat = pick(/Heat/), storm = pick(/Thunderstorm|Tornado/);
      const src = "NWS alerts (live)";
      sim.hazards.flood = flood
        ? { kind: "flood", level: /Warning/.test(flood.event) ? "high" : "moderate", headline: `${flood.event}${until(flood)}`, detail: "Avoid low roads and flooded areas. Never drive through water.", source: src, updated_at: t, stop_work: false, band_pattern: /Warning/.test(flood.event) ? "FLOOD_FIRE" : null }
        : noneHazard("flood", t, "No flood alerts", src);
      sim.hazards.fire = fire
        ? { kind: "fire", level: /Warning/.test(fire.event) ? "high" : "moderate", headline: `${fire.event}${until(fire)}`, detail: "Fires can spread fast today. No hot work outdoors.", source: src, updated_at: t, stop_work: false, band_pattern: /Warning/.test(fire.event) ? "FLOOD_FIRE" : null }
        : noneHazard("fire", t, "No fire weather alerts", src);
      if (heat) sim.hazards.heat = { ...sim.hazards.heat, level: /Warning|Extreme/.test(heat.event) ? "extreme" : "high", detail: `NWS ${heat.event}${until(heat)}. ${sim.hazards.heat.detail}` };
      const strikes = sim.hazards.lightning.lightning?.strikes.length ?? 0;
      if (storm && !sim.hazards.lightning.stop_work && strikes === 0) {
        sim.hazards.lightning = { ...sim.hazards.lightning, level: "high", headline: `${storm.event}${until(storm)}`, detail: "Get inside a building or truck when you hear thunder.", source: src, updated_at: t };
      } else if (!storm && strikes === 0 && !sim.hazards.lightning.stop_work) {
        sim.hazards.lightning = { ...sim.hazards.lightning, level: "none", headline: "No storm warnings", source: src, updated_at: t };
      }
      // Tomorrow's index: flood and fire parts from the real alerts
      const floodIdx = flood ? (/Warning/.test(flood.event) ? 2.5 : 1.2) : 0.2;
      const fireIdx = fire ? (/Warning/.test(fire.event) ? 3 : 1.5) : 0.1;
      const old = sim.hazardIndex;
      const others = old.top_reasons.filter((r) => r.hazard !== "flood" && r.hazard !== "fire");
      const extra: HazardIndex["top_reasons"] = [
        ...(flood ? [{ hazard: "flood" as const, feature: "nws_flood", value: 1, points: round(floodIdx * 0.6, 1), text: `${flood.event} (NWS)` }] : []),
        ...(fire ? [{ hazard: "fire" as const, feature: "nws_fire", value: 1, points: round(fireIdx * 0.6, 1), text: `${fire.event} (NWS)` }] : []),
      ];
      const heatPart = old.hazards.heat?.index ?? 0, stormPart = old.hazards.storm?.index ?? 0, trop = old.hazards.tropical?.index ?? 0.1;
      const index = round(clamp(0.6 + heatPart * 0.85 + stormPart * 0.5 + floodIdx * 0.6 + fireIdx * 0.6 + trop * 0.3, 0.3, 9.8), 1);
      sim.index = index;
      sim.hazardIndex = {
        ...old, index, band: index < 2 ? "low" : index < 4 ? "moderate" : index < 6 ? "high" : "extreme",
        hazards: { ...old.hazards, flood: indexPart(floodIdx), fire: indexPart(fireIdx) },
        top_reasons: [...others, ...extra].sort((a, b) => b.points - a.points).slice(0, 3),
      };
      sim.drivers = sim.hazardIndex.top_reasons.map((r) => r.text);
    }
    // Topsoil moisture (Open-Meteo, live)
    if (w && w.soilMoisture !== null) {
      const sm = w.soilMoisture, pct = Math.round(sm * 100);
      sim.hazards.soil = {
        kind: "soil", level: sm >= 0.4 ? "moderate" : sm >= 0.33 ? "low" : "none",
        headline: `Topsoil ${pct}% water by volume`,
        detail: sm >= 0.4 ? "Soaked ground. Check footing for bucket trucks and outriggers." : sm >= 0.33 ? "Damp ground. Watch soft spots near ditches." : "Ground is firm enough for heavy equipment.",
        source: "Open-Meteo soil moisture (live)", updated_at: w.at, stop_work: false, band_pattern: null,
      };
    }
    if (siteId in this.live.sightings) {
      const t = Math.round(Date.now() / 1000);
      const s = this.live.sightings[siteId];
      const day = s ? new Date(s.observed_on + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "";
      sim.hazards.wildlife = s
        ? {
          kind: "wildlife", level: !s.approximate && s.miles <= 1 ? "moderate" : "low",
          headline: s.approximate ? `${s.common} reported nearby` : `${s.common} seen ${s.miles} mi away`,
          detail: s.approximate
            ? `iNaturalist sighting on ${day}. iNaturalist hides the exact spot for this one, so the map pin is approximate.`
            : `iNaturalist sighting on ${day}, ${s.miles} mi from the plant. The map shows where it was seen.`,
          source: "iNaturalist (live, last 30 days)", updated_at: t, stop_work: false, band_pattern: null,
          sighting: s,
        }
        : noneHazard("wildlife", t, "No dangerous wildlife reported within 6 mi", "iNaturalist (live, last 30 days)");
    }
  }

  /** Stop for good and hang up on every listener (their browsers reconnect to the new engine). */
  shutdown() {
    this.stop();
    if (this.liveTimer) clearInterval(this.liveTimer);
    this.liveTimer = null;
    for (const s of this.subs) { try { s.close?.(); } catch { /* already gone */ } }
    this.subs.clear();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Fresh state at sim time `t`, with 30 min of warm-up history so charts aren't empty. */
  reset(t: number) {
    this.alerts = [];
    this.mandown = null;
    this.emergency = null;
    this.log = [];
    this.band = [];
    this.rng = makeRng(42);
    this.sim = t - 30 * 60;
    this.sites.clear();
    for (const s of SITES) this.sites.set(s.id, baselineSite(s.id, t));
    this.workers.clear();
    for (const def of allWorkers()) {
      const core = 37.05 + def.effort * 0.35;
      this.workers.set(def.worker_id, {
        def, core, intensity: def.effort, scriptedIntensity: null, hr: def.restHr, skin: 34.2,
        collapsed: false, stillSince: null, minuteHr: [], hrByMinute: [],
        kalman: { ct: CFG.KALMAN.ct0, v: 0 }, status: "green", vitals: [], estimates: [],
        lastBand: "BUZZ:OFF", heatAlertId: null, rawEstimate: core,
      });
    }
    this.planCache.clear();
    this.adjustments = [];
    this.planChanges = [];
    this.videoTimers = [];
    for (const s of SITES) this.applyLive(s.id);     // keep real weather/wildlife across resets
    // Warm up quietly (no events sent).
    const quiet = this.subs;
    this.subs = new Set();
    this.advanceSim(30 * 60);
    this.subs = quiet;
    this.lastWall = this.wallNow();
  }

  seedReports(t: number) {
    const d = SITE_BY_ID["fl-deerhaven"];
    const k = SITE_BY_ID["fl-kelly"];
    this.reports.push(
      { id: "r-seed-1", site_id: d.id, worker_id: "worker-3", kind: "snake", note: "Cottonmouth by the north fence, near the transformer pad.", lat: d.lat + 0.002, lon: d.lon - 0.003, photo: null, at: t - 2.5 * 3600 },
      { id: "r-seed-2", site_id: k.id, worker_id: "worker-6", kind: "standing_water", note: "Standing water across the east access road after last night's rain.", lat: k.lat - 0.001, lon: k.lon + 0.002, photo: null, at: t - 4 * 3600 },
    );
  }

  // ---- subscriptions -----------------------------------------------------

  /** Listen for events. Sends a snapshot right away. Returns an unsubscribe function. */
  subscribe(filter: ViewFilter, send: (ev: LiveEvent) => void, close?: () => void): () => void {
    const sub: Subscriber = { filter, send, close };
    this.subs.add(sub);
    send(this.snapshot(filter));
    return () => { this.subs.delete(sub); };
  }

  emit(ev: LiveEvent) {
    for (const s of this.subs) {
      if (!canSee(s.filter, ev)) continue;
      try { s.send(ev); } catch { this.subs.delete(s); }
    }
  }

  resendSnapshots() {
    for (const s of this.subs) {
      try { s.send(this.snapshot(s.filter)); } catch { this.subs.delete(s); }
    }
  }

  addLog(text: string, source: string) {
    const entry = { at: this.sim, text, source };
    this.log.push(entry);
    if (this.log.length > 200) this.log.shift();
    this.emit({ type: "log", entry });
  }

  // ---- the clock ---------------------------------------------------------

  /** Called every ~0.5 s of wall time. Moves the sim clock by wall time x speed. */
  tick() {
    const wall = this.wallNow();
    const dt = clamp(wall - this.lastWall, 0, 5);
    this.lastWall = wall;
    this.advanceSim(dt * this.demo.speed);
    this.emit({ type: "clock", sim_time: this.sim, speed: this.demo.speed });
  }

  /** Step the simulation forward in 5-second pieces. */
  advanceSim(seconds: number) {
    let left = seconds;
    while (left > 0) {
      const step = Math.min(VITALS_EVERY_S, left);
      const before = this.sim;
      this.sim += step;
      left -= step;
      if (Math.floor(before / VITALS_EVERY_S) !== Math.floor(this.sim / VITALS_EVERY_S)) this.vitalsStep();
      if (Math.floor(before / 60) !== Math.floor(this.sim / 60)) this.minuteStep();
      this.runScenario();
      this.videoStep();
      this.manDownStep();
      this.emergencyStep();
      this.lightningStep();
    }
  }

  thresholds() {
    return this.demo.demo_threshold
      ? { warning: CFG.DEMO_HEAT_WARNING_C, danger: CFG.DEMO_HEAT_DANGER_C }
      : { warning: CFG.HEAT_WARNING_C, danger: CFG.HEAT_DANGER_C };
  }

  // ---- workers -----------------------------------------------------------

  /** Planned workload for a worker at time t, from the schedule. */
  plannedTask(w: WorkerSim, t: number): TaskKind {
    if (this.siteStopWork(w.def.site_id, t)) return "stop_work";
    const adj = this.adjustments.find((a) => a.worker_id === w.def.worker_id && t >= a.from && t < a.to);
    if (adj) return adj.task;
    if (this.usePlanner()) {
      const slot = this.slotFor(w.def.worker_id, t);
      return slot ? slot.task : "off";
    }
    const d = new Date(t * 1000);
    const halfHour = d.getHours() * 2 + (d.getMinutes() >= 30 ? 1 : 0);
    if (halfHour < 12 || halfHour >= 40) return "off";                // shift 6:00 to 20:00
    if (halfHour === 24) return "break";                               // lunch 12:00
    const afternoon = halfHour >= 25 && halfHour < 32;                 // 12:30 to 16:00: break every 1.5 h
    if (afternoon && (halfHour + w.def.breakOffset) % 3 === 0) return "break";
    if (!afternoon && (halfHour + w.def.breakOffset) % 4 === 0) return "break";
    return ["heavy", "moderate", "heavy", "light"][(halfHour + w.def.breakOffset) % 4] as TaskKind;
  }

  taskIntensity(w: WorkerSim, task: TaskKind) {
    const f = { heavy: 1, moderate: 0.8, light: 0.6, break: 0.12, stop_work: 0.15, off: 0.1 }[task];
    return w.def.effort * f + (task === "break" || task === "stop_work" || task === "off" ? 0 : 0.05);
  }

  siteWbgt(siteId: string) {
    return this.sites.get(siteId)?.wbgt ?? SITE_WBGT_DEFAULT;
  }

  vitalsStep() {
    for (const w of this.workers.values()) {
      const wbgt = this.siteWbgt(w.def.site_id);
      const target = w.scriptedIntensity ?? this.taskIntensity(w, this.plannedTask(w, this.sim));
      // Workload changes over about a minute, not instantly.
      w.intensity += (target - w.intensity) * 0.25;
      if (w.collapsed) w.intensity = 0.05;
      const coreGoal = coreTarget(w.collapsed ? 0.9 : w.intensity, wbgt);
      w.core += (coreGoal - w.core) * (CORE_RATE * VITALS_EVERY_S / 60);
      w.hr = fakeHr(w, this.rng);
      if (w.collapsed) w.hr = Math.max(w.hr, 112 + (this.rng() - 0.5) * 6);
      w.skin += ((33.6 + 1.2 * w.intensity + 0.35 * (w.core - 37) + (wbgt - 28) * 0.12) - w.skin) * 0.08;
      w.minuteHr.push(w.hr);
      const activity = w.collapsed ? 0 : round(clamp(w.intensity * 9 + (this.rng() - 0.5) * 2, 0, 12), 1);
      const v: Vitals = {
        worker_id: w.def.worker_id, timestamp: this.sim, hr: Math.round(w.hr),
        skin_temp: round(w.skin + (this.rng() - 0.5) * 0.06, 2), activity, tilt: w.collapsed ? 1 : 0,
      };
      w.vitals.push(v);
      while (w.vitals.length && w.vitals[0].timestamp < this.sim - HISTORY_VITALS_S) w.vitals.shift();
      this.emit({ type: "vitals", vitals: v });
    }
  }

  /** Every sim minute: Kalman, model estimate, forecast, heat status, band. */
  minuteStep() {
    const thr = this.thresholds();
    for (const w of this.workers.values()) {
      const avgHr = w.minuteHr.length ? w.minuteHr.reduce((a, b) => a + b, 0) / w.minuteHr.length : w.hr;
      w.minuteHr = [];
      w.hrByMinute.push(avgHr);
      if (w.hrByMinute.length > 120) w.hrByMinute.shift();
      kalmanStep(w.kalman, avgHr);

      // "Model" estimate = sim core + a little error. Calibrated range ±0.15 °C.
      w.rawEstimate = w.core + (this.rng() - 0.5) * 0.06;
      this.pushEstimate(w, thr);
    }
    this.routeAllBands();
    for (const w of this.workers.values()) this.emit({ type: "worker_status", worker: this.workerStatus(w) });
    this.refreshSiteFlags();
  }

  /** Build this minute's estimate from the raw model value, minus the worker's personal offset. */
  pushEstimate(w: WorkerSim, thr = this.thresholds()) {
    const offset = this.offsetOf(w.def.worker_id);
    const mean = w.rawEstimate - offset;
    const forecast = this.forecast(w, mean);
    const est: Estimate = {
      worker_id: w.def.worker_id,
      timestamp: this.sim,
      core: { mean: round(mean), p10: round(mean - 0.15), p90: round(mean + 0.15) },
      kalman: round(w.kalman.ct),
      forecast,
      why: this.why(w, forecast),
      thresholds: { ...thr, demo: this.demo.demo_threshold },
      model: "mock forecaster (fake physiology)",
      offset_applied_c: round(offset),
    };
    w.estimates.push(est);
    while (w.estimates.length > HISTORY_ESTIMATES_MIN) w.estimates.shift();
    this.emit({ type: "estimate", estimate: est });
    this.updateHeatStatus(w, est);
    return est;
  }

  // ---- personal offset from thermometer spot checks ----------------------
  // Same math as backend/models/personal_offset.py (Bayesian shrinkage, capped).

  offsets = new Map<string, { misses: number[]; last: OffsetUpdate["last_check"]; history: OffsetUpdate["history"] }>();

  offsetOf(workerId: string) {
    const o = this.offsets.get(workerId);
    if (!o || !o.misses.length) return 0;
    const raw = o.misses.reduce((a, b) => a + b, 0) / (o.misses.length + OFFSET.PRIOR_CHECKS);
    return clamp(raw, -OFFSET.MAX_OFFSET_C, OFFSET.MAX_OFFSET_C);
  }

  offsetUpdate(workerId: string): OffsetUpdate {
    const o = this.offsets.get(workerId) ?? { misses: [], last: null, history: [] };
    const n = o.misses.length;
    return {
      worker_id: workerId, timestamp: this.sim, offset_c: round(this.offsetOf(workerId), 3),
      confidence: round(n / (n + OFFSET.PRIOR_CHECKS), 3), checks: n, last_check: o.last, history: o.history,
    };
  }

  /** A thermometer reading (ear/oral) for this worker. Learns their offset and re-estimates right away. */
  spotCheck(workerId: string, thermometer: number): SpotCheckResult {
    const w = this.workers.get(workerId);
    if (!w) return { ok: false, accepted: false, message: "Unknown worker", offset: null };
    if (!Number.isFinite(thermometer) || thermometer < 34 || thermometer > 42) {
      return { ok: false, accepted: false, message: "That doesn't look like a body temperature (34-42 °C).", offset: this.offsetUpdate(workerId) };
    }
    const o = this.offsets.get(workerId) ?? { misses: [], last: null, history: [] };
    this.offsets.set(workerId, o);
    const miss = w.rawEstimate - thermometer;
    const accepted = Math.abs(miss) <= OFFSET.MAX_CHECK_MISS_C;
    o.last = { thermometer_c: thermometer, model_estimate_c: round(w.rawEstimate), miss_c: round(miss), accepted, at: this.sim };
    if (accepted) {
      o.misses.push(miss);
      if (!o.history.length) o.history.push({ timestamp: this.sim - 1, offset_c: 0, confidence: 0, checks: 0 });
      const u0 = this.offsetUpdate(workerId);
      o.history.push({ timestamp: this.sim, offset_c: u0.offset_c, confidence: u0.confidence, checks: u0.checks });
    }
    const u = this.offsetUpdate(workerId);
    this.emit({ type: "offset_update", offset: u });
    const body = -u.offset_c;
    const first = w.def.name.split(" ")[0];
    const how = Math.abs(body) < 0.005 ? "the same as" : `${Math.abs(body).toFixed(2)} °C ${body > 0 ? "hotter" : "cooler"} than`;
    const message = accepted
      ? `Learned: ${first} runs ${how} the average person (${u.checks} check${u.checks === 1 ? "" : "s"}, ${Math.round(u.confidence * 100)}% confident).`
      : `Ignored: ${thermometer.toFixed(1)} °C is ${Math.abs(miss).toFixed(1)} °C away from the model. Probably a bad reading, try again.`;
    this.addLog(`Spot check ${w.def.name}: ${thermometer.toFixed(1)} °C. ${message}`, "thermometer");
    if (accepted) {
      this.pushEstimate(w);   // the chart moves right away
      this.routeAllBands();
      this.emitWorker(w);
    }
    return { ok: true, accepted, message, offset: u };
  }

  /** Mock only: what an ear thermometer would read for this worker right now. */
  sampleThermometer(workerId: string) {
    const w = this.workers.get(workerId);
    if (!w) return null;
    return round(w.core + w.def.trueOffset + (this.rng() - 0.5) * 0.16, 1);
  }

  /** Run the fake physiology forward 30 minutes using the planned workload. */
  forecast(w: WorkerSim, start: number) {
    const wbgt = this.siteWbgt(w.def.site_id);
    let core = start;
    const out = [];
    for (let k = 1; k <= FORECAST_MIN; k++) {
      const planned = w.scriptedIntensity ?? this.taskIntensity(w, this.plannedTask(w, this.sim + k * 60));
      const intensity = w.collapsed ? 0.9 : planned;
      core += (coreTarget(intensity, wbgt) - core) * CORE_RATE;
      const spread = 0.03 + 0.008 * k;
      out.push({ minutes_ahead: k, p10: round(core - spread * 0.8), p50: round(core), p90: round(core + spread) });
    }
    return out;
  }

  why(w: WorkerSim, f: Estimate["forecast"]) {
    const h = w.hrByMinute;
    const now = h[h.length - 1] ?? w.hr;
    const before = h[Math.max(0, h.length - 11)] ?? now;
    const dHr = Math.round(now - before);
    const wbgt = this.siteWbgt(w.def.site_id);
    const rise = f[19].p50 - f[0].p50;
    if (w.collapsed) return "No movement detected. Heart rate still elevated.";
    if (rise > 0.15) {
      return dHr >= 8
        ? `Rising because heart rate jumped ${dHr} bpm in 10 min and WBGT is ${wbgt} °C.`
        : `Rising because of steady hard work at WBGT ${wbgt} °C.`;
    }
    if (rise < -0.1) return `Falling: lighter work or a break, heart rate ${dHr <= -5 ? `down ${-dHr} bpm` : "settling"}.`;
    return `Holding steady: workload and heat are balanced (WBGT ${wbgt} °C).`;
  }

  /** The spec's heat rules (Alerts table), with the anti-flicker margin. */
  updateHeatStatus(w: WorkerSim, est: Estimate) {
    const { warning, danger } = est.thresholds;
    const m = CFG.ALERT_CLEAR_MARGIN_C;
    const p90at = (min: number) => est.forecast[min - 1].p90;
    const now = est.core.mean;
    const redOn = now >= danger || p90at(10) >= danger;
    const redHold = w.status === "red" && (now >= danger - m || p90at(10) >= danger - m);
    const yellowOn = p90at(20) >= warning;
    const yellowHold = w.status !== "green" && p90at(20) >= warning - m;
    const next: Status = redOn || redHold ? "red" : yellowOn || yellowHold ? "yellow" : "green";
    if (next === w.status) return;

    const prev = w.status;
    w.status = next;
    if (w.heatAlertId) this.clearAlert(w.heatAlertId);
    w.heatAlertId = null;
    if (next === "green") {
      this.addLog(`${w.def.name} back to green`, "forecaster");
      return;
    }
    const kind: AlertKind = next === "red" ? "HEAT_RED" : "HEAT_YELLOW";
    const msg = next === "red"
      ? `${w.def.name}: heat danger. Stop work and cool down now.`
      : `${w.def.name}: heat warning. Forecast crosses the warning line within 20 min.`;
    w.heatAlertId = this.openAlert(kind, w.def.worker_id, w.def.site_id, msg, "forecaster").id;
    this.addLog(`${w.def.name} ${prev} → ${next}${this.demo.demo_threshold ? " (demo threshold)" : ""}`, "forecaster");
    this.adjustForHeat(w, next);
  }

  minutesToDanger(w: WorkerSim): number | null {
    const est = w.estimates[w.estimates.length - 1];
    if (!est) return null;
    if (est.core.mean >= est.thresholds.danger) return 0;
    const hit = est.forecast.find((p) => p.p90 >= est.thresholds.danger);
    if (!hit) return null;
    // Count down between minute updates, so the cards tick smoothly.
    const sinceEst = (this.sim - est.timestamp) / 60;
    return Math.max(0, Math.round(hit.minutes_ahead - sinceEst));
  }

  workerStatus(w: WorkerSim): WorkerStatus {
    const id = w.def.worker_id;
    const active = this.activeAlertsFor(id);
    const top = this.topAlert(active);
    const heat = active.find((a) => a.kind === "HEAT_RED" || a.kind === "HEAT_YELLOW");
    const mtd = this.minutesToDanger(w);
    const task = this.plannedTask(w, this.sim);
    const onBreak = task === "break" || task === "stop_work";
    let breakAt: number | null = null;
    for (let k = 1; k <= 24 * 6; k++) {         // look ahead in 5-minute steps, up to 12 h
      const t = Math.ceil(this.sim / 300) * 300 + (k - 1) * 300;
      const tk = this.plannedTask(w, t);
      const isBreak = tk === "break" || tk === "stop_work";
      if (onBreak ? !isBreak : isBreak) { breakAt = t; break; }
    }
    const offShift = task === "off";
    if (offShift) breakAt = null;                 // no "next break in 14 h" overnight
    let reason = offShift ? "Off shift. No danger predicted in the next 30 min." : "Normal. No danger predicted in the next 30 min.";
    if (this.mandown?.worker_id === id && this.mandown.reason === "sos") reason = "SOS pressed. Emergency escalation in progress.";
    else if (w.collapsed && this.mandown) reason = "Man-down check in progress.";
    else if (w.status === "red") reason = mtd === 0 ? "In the danger zone now. Cool down." : "Forecast to reach danger within 10 min.";
    else if (w.status === "yellow") reason = mtd !== null ? `Forecast to reach danger in ~${mtd} min.` : "Forecast near the warning line.";
    return {
      worker_id: id, name: w.def.name, role: w.def.role, site_id: w.def.site_id, team: w.def.team ?? DEMO_TEAM, status: w.status,
      minutes_to_danger: w.status === "green" ? null : mtd, status_reason: reason,
      hazard: top?.kind ?? null,
      alert_acknowledged: heat?.kind === "HEAT_RED" ? heat.acknowledged_at !== null : null,
      on_break: onBreak, break_at: breakAt, band_connected: true, updated_at: this.sim, off_shift: offShift,
      location: this.locations.get(id),
    };
  }

  // ---- alerts and the band ----------------------------------------------

  openAlert(kind: AlertKind, worker_id: string | null, site_id: string | null, message: string, source: string): Alert {
    const a: Alert = { id: `a-${this.nextId++}`, kind, worker_id, site_id, started_at: this.sim, message, source, acknowledged_at: null, cleared_at: null };
    this.alerts.push(a);
    if (this.alerts.length > 300) this.alerts.shift();
    this.emit({ type: "alert", alert: a });
    return a;
  }

  clearAlert(id: string) {
    const a = this.alerts.find((x) => x.id === id);
    if (!a || a.cleared_at) return;
    a.cleared_at = this.sim;
    this.emit({ type: "alert", alert: a });
  }

  updateAlert(a: Alert) {
    this.emit({ type: "alert", alert: a });
  }

  activeAlertsFor(workerId: string): Alert[] {
    const w = this.workers.get(workerId);
    if (!w) return [];
    return this.alerts.filter((a) => !a.cleared_at && (a.worker_id === workerId || (a.worker_id === null && a.site_id === w.def.site_id)));
  }

  /** Priority from PLAN.md: MANDOWN > HEAT_RED > LIGHTNING > HEAT_YELLOW > FLOOD_FIRE (SOS sits with MANDOWN). */
  topAlert(list: Alert[]): Alert | null {
    const rank: Record<AlertKind, number> = { SOS: 6, MANDOWN: 5, HEAT_RED: 4, LIGHTNING: 3, HEAT_YELLOW: 2, FLOOD_FIRE: 1 };
    return list.reduce<Alert | null>((best, a) => (!best || rank[a.kind] > rank[best.kind] ? a : best), null);
  }

  /** Send a band command only when it changes (no flicker). */
  routeAllBands() {
    for (const w of this.workers.values()) {
      // SOS is not a band command: the band blinks purple on its own when the button is held.
      const top = this.topAlert(this.activeAlertsFor(w.def.worker_id).filter((a) => a.kind !== "SOS"));
      let cmd = `BUZZ:${top ? top.kind : "OFF"}`;
      if (this.mandown && this.mandown.worker_id === w.def.worker_id && this.mandown.stage === "checking") cmd = "BUZZ:MANDOWN";
      if (cmd === w.lastBand) continue;
      w.lastBand = cmd;
      const b: BandCommand = { worker_id: w.def.worker_id, command: cmd, at: this.sim };
      this.band.push(b);
      if (this.band.length > 50) this.band.shift();
      this.emit({ type: "band_command", band: b });
    }
  }

  /** The worker pressed the band button (or "I'm OK" on the phone). */
  buttonPress(workerId: string) {
    const w = this.workers.get(workerId);
    if (!w) return { ok: false, error: "unknown worker" };
    if (this.mandown && this.mandown.worker_id === workerId && this.mandown.stage === "checking") {
      this.mandown.stage = "cancelled";
      w.collapsed = false;
      w.stillSince = null;
      this.addLog(`${w.def.name} pressed the button. Man-down check cancelled.`, "band button");
      this.emit({ type: "mandown", mandown: this.mandown });
      this.mandown = null;
      this.emit({ type: "mandown", mandown: null });
      this.routeAllBands();
      this.emitWorker(w);
      return { ok: true, action: "mandown_cancelled" };
    }
    const red = this.activeAlertsFor(workerId).find((a) => a.kind === "HEAT_RED" && !a.acknowledged_at);
    if (red) {
      red.acknowledged_at = this.sim;
      this.updateAlert(red);
      this.addLog(`${w.def.name} acknowledged the heat danger alert`, "band button");
      this.emitWorker(w);
      return { ok: true, action: "alert_acknowledged" };
    }
    return { ok: true, action: "none" };
  }

  emitWorker(w: WorkerSim) {
    this.emit({ type: "worker_status", worker: this.workerStatus(w) });
  }

  // ---- man-down and emergency -------------------------------------------

  /** Make a worker go still (the scripted collapse or the demo button). */
  collapse(workerId: string, reason: ManDown["reason"]) {
    const w = this.workers.get(workerId);
    if (!w || w.collapsed) return;
    w.collapsed = true;
    w.stillSince = this.sim;
    if (this.demo.speed !== 1) {
      this.demo.speed = 1;
      this.emitDemo();
      this.addLog("Replay slowed to 1x so the man-down runs in real time", "demo");
    }
    this.addLog(`${w.def.name}: band tipped over, no movement`, reason === "demo" ? "demo button" : "tilt switch");
    this.pendingReason = reason;
  }

  pendingReason: ManDown["reason"] = "fall";

  /** Where to send help: the phone's shared location if it's fresh (30 min), else the site. */
  whereIs(w: WorkerSim) {
    const loc = this.locations.get(w.def.worker_id);
    if (loc && this.sim - loc.at < 30 * 60) return { lat: loc.lat, lon: loc.lon };
    const site = SITE_BY_ID[w.def.site_id];
    return { lat: site.lat + 0.0012, lon: site.lon - 0.0008 };
  }

  /** A worker's phone shares (or stops sharing) its location. */
  setLocation(workerId: string, body: Record<string, unknown>) {
    const w = this.workers.get(workerId);
    if (!w) return { status: 404, json: { ok: false, error: "unknown worker" } };
    if (body.stop) {
      this.locations.delete(workerId);
    } else {
      const lat = Number(body.lat), lon = Number(body.lon), acc = Number(body.accuracy_m);
      if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
        return { status: 400, json: { ok: false, error: "lat and lon must be numbers" } };
      }
      this.locations.set(workerId, { lat, lon, accuracy_m: Number.isFinite(acc) ? Math.round(acc) : null, at: this.sim });
    }
    this.emit({ type: "worker_status", worker: this.workerStatus(w) });
    return { status: 200, json: { ok: true } };
  }

  startManDownCheck(w: WorkerSim, reason: ManDown["reason"]) {
    const site = SITE_BY_ID[w.def.site_id];
    const last = w.estimates[w.estimates.length - 1];
    this.mandown = {
      id: `md-${this.nextId++}`, worker_id: w.def.worker_id, worker_name: w.def.name,
      site_id: site.id, site_name: site.name, ...this.whereIs(w),
      fell_at: w.stillSince ?? this.sim, check_started_at: this.sim,
      confirm_at: this.sim + DEMO_TIMINGS.MANDOWN_ESCALATE_S, confirmed_at: null, stage: "checking", reason,
      last_vitals: { core_temp: last ? last.core.mean : null, hr: Math.round(w.hr), at: this.sim },
    };
    this.addLog(`Man-down check: ${w.def.name}'s band is buzzing. Waiting for a button press or movement.`, "man-down rule");
    this.emit({ type: "mandown", mandown: this.mandown });
    this.routeAllBands();
    this.emitWorker(w);
  }

  manDownStep() {
    // 1. Still long enough -> start the check.
    if (!this.mandown) {
      for (const w of this.workers.values()) {
        if (w.collapsed && w.stillSince !== null && this.sim - w.stillSince >= DEMO_TIMINGS.MANDOWN_STILL_S) {
          this.startManDownCheck(w, this.pendingReason);
          break;
        }
      }
      // Also trigger on a red heat alert with no button press for 60 s (spec).
      if (!this.mandown) {
        for (const w of this.workers.values()) {
          const red = this.activeAlertsFor(w.def.worker_id).find((a) => a.kind === "HEAT_RED" && !a.acknowledged_at && a.worker_id);
          if (red && this.sim - red.started_at >= CFG.HEAT_RED_ACK_TIMEOUT_S) {
            this.startManDownCheck(w, "no_response_to_red");
            red.acknowledged_at = this.sim; // stop retriggering; the man-down flow takes over
            this.updateAlert(red);
            break;
          }
        }
      }
      return;
    }
    const md = this.mandown;
    // 2. Keep last vitals fresh while checking.
    const w = this.workers.get(md.worker_id);
    if (w && md.stage === "checking") {
      md.last_vitals = { core_temp: w.estimates[w.estimates.length - 1]?.core.mean ?? null, hr: Math.round(w.hr), at: this.sim };
    }
    // 3. Not cancelled in time -> confirmed: alert the crew lead and crewmates, start the emergency call.
    if (md.stage === "checking" && this.sim >= md.confirm_at) {
      md.stage = "confirmed";
      md.confirmed_at = this.sim;
      this.openAlert("MANDOWN", md.worker_id, md.site_id, `${md.worker_name} is down at ${md.site_name}. No response to the band.`, "man-down rule");
      this.addLog(`Man-down CONFIRMED for ${md.worker_name}. Crew lead and nearby crewmates alerted.`, "man-down rule");
      this.emit({ type: "mandown", mandown: md });
      this.startEmergency(md);
      this.routeAllBands();
      if (w) this.emitWorker(w);
      this.refreshSiteFlags();
    }
  }

  /**
   * SOS: the band sent BTN:SOS (button held 3 s). No stillness wait, no "checking":
   * it goes straight to confirmed and starts the emergency chain (same as session D).
   * Ignored if this worker already has an active incident, so nobody gets called twice.
   */
  sos(workerId: string, source: SosPress["source"]) {
    const w = this.workers.get(workerId);
    if (!w) return { ok: false, error: "unknown worker" };
    const site = SITE_BY_ID[w.def.site_id];
    const busy = !!this.mandown;
    const press: SosPress = {
      worker_id: workerId, worker_name: w.def.name, site_id: site.id, site_name: site.name,
      ...this.whereIs(w), pressed_at: this.sim, source, escalated: !busy,
    };
    this.emit({ type: "sos", sos: press });
    if (busy) {
      this.addLog(`SOS from ${w.def.name} ignored: an incident is already active`, "band button");
      return { ok: true, action: "ignored_active_incident" };
    }
    if (this.demo.speed !== 1) {
      this.demo.speed = 1;
      this.emitDemo();
      this.addLog("Replay slowed to 1x so the SOS runs in real time", "demo");
    }
    const last = w.estimates[w.estimates.length - 1];
    this.mandown = {
      id: `sos-${this.nextId++}`, worker_id: workerId, worker_name: w.def.name,
      site_id: site.id, site_name: site.name, lat: press.lat, lon: press.lon,
      fell_at: this.sim, check_started_at: this.sim, confirm_at: this.sim, confirmed_at: this.sim,
      stage: "confirmed", reason: "sos",
      last_vitals: { core_temp: last ? last.core.mean : null, hr: Math.round(w.hr), at: this.sim },
    };
    this.addLog(`SOS from ${w.def.name}: band button held 3 s. Escalating right away.`, source === "band" ? "band button" : source === "phone" ? "phone" : "demo button");
    this.openAlert("SOS", workerId, site.id, `SOS from ${w.def.name} at ${site.name}.`, source === "band" ? "band button (BTN:SOS)" : `${source} SOS`);
    this.emit({ type: "mandown", mandown: this.mandown });
    this.startEmergency(this.mandown);
    this.routeAllBands();
    this.emitWorker(w);
    this.refreshSiteFlags();
    return { ok: true, action: "escalated" };
  }

  startEmergency(md: ManDown) {
    const crewmates = [...this.workers.values()]
      .filter((x) => x.def.site_id === md.site_id && x.def.worker_id !== md.worker_id)
      .map((x) => x.def.name.split(" ")[0]);
    this.emergency = {
      // Same id format as session D: inc-<worker_id>-<confirmed_at>
      id: `inc-${md.worker_id}-${Math.round(this.sim)}`, mandown_id: md.id, worker_id: md.worker_id, stage: "notifying",
      started_at: this.sim, cancel_deadline: null, demo: true, target_label: "Demo: would call 911",
      steps: [{ at: this.sim, stage: "notifying", text: `${md.reason === "sos" ? `SOS from ${md.worker_name}. ` : ""}Notifying crew lead ${CREW_LEAD} and the site safety officer (dashboard + phone). Crewmates alerted: ${crewmates.join(", ") || "none"}.` }],
      transcript: [], outcome: null,
    };
    this.addLog(`Emergency escalation started (fixed rule: ${md.reason === "sos" ? "SOS" : "confirmed man-down"} → call)`, "escalation");
    this.emitCall([
      { stage: "notify", status: "calling", message: `${md.reason === "sos" ? `SOS from ${md.worker_name}` : `${md.worker_name} is down`}. Notifying crew.` },
      { stage: "notify", status: "calling", target: "crew_lead", message: "Calling" },
      { stage: "notify", status: "calling", target: "safety_officer", message: "Calling" },
    ]);
  }

  /**
   * Send the emergency call out to the UI.
   * "call" format: the whole EmergencyCall in one event.
   * "status" format: one session-D-style event per step in `steps`.
   */
  emitCall(steps: StatusStep[]) {
    const em = this.emergency;
    if (!em) return;
    if (this.emergencyFormat === "call") {
      this.emit({ type: "emergency", emergency: em });
      return;
    }
    const md = this.mandown;
    for (const step of steps) {
      const target = step.target ?? null;
      this.emit({
        type: "emergency_status",
        incident_id: em.id,
        worker_id: em.worker_id,
        worker_name: md?.worker_id === em.worker_id ? md.worker_name : null,
        timestamp: this.sim,
        stage: step.stage,
        status: step.status,
        target,
        number_masked: target ? MASKED[target] : null,
        mode: "fake",
        demo_label: target === "emergency" ? em.target_label : null,
        cancel_deadline: em.cancel_deadline,
        message: step.message ?? "",
      });
    }
  }

  emergencyStep() {
    const em = this.emergency;
    if (!em || isFinalStage(em.stage)) return;
    const md = this.mandown;
    const t = this.sim - em.started_at;
    const T = DEMO_TIMINGS;
    const push = (stage: EmergencyStage, text: string) => {
      em.stage = stage;
      em.steps.push({ at: this.sim, stage, text });
      this.addLog(text, "escalation");
    };
    let changed = false;
    const steps: StatusStep[] = [];   // what changed, in session D's words (for the "status" format)
    if (em.stage === "notifying" && t >= T.NOTIFY_S) {
      em.cancel_deadline = this.sim + T.CANCEL_WINDOW_S;
      push("cancel_window", `Cancel window open for ${T.CANCEL_WINDOW_S} s. Crew lead can press "Cancel, false alarm".`);
      for (const target of ["crew_lead", "safety_officer"] as const) {
        steps.push({ stage: "notify", status: "connected", target, message: "Connected" });
        steps.push({ stage: "notify", status: "completed", target, message: "Message delivered" });
      }
      steps.push({ stage: "cancel_window", status: "waiting", message: `Emergency call in ${T.CANCEL_WINDOW_S} s unless cancelled.` });
      changed = true;
    }
    if (em.stage === "cancel_window" && em.cancel_deadline !== null && this.sim >= em.cancel_deadline) {
      push("calling", "Not cancelled. Voice agent calling the test phone. Demo: would call 911.");
      steps.push({ stage: "emergency_call", status: "calling", target: "emergency", message: "Calling the test number" });
      changed = true;
    }
    const callStart = (T.NOTIFY_S + T.CANCEL_WINDOW_S);
    if (em.stage === "calling" && t >= callStart + T.RING_S && this.failNextCall) {
      // Demo switch: the call doesn't go through. The screen must tell the crew lead to call 911.
      this.failNextCall = false;
      push("failed", CALL_FAILED_TEXT);
      em.outcome = CALL_FAILED_TEXT;
      steps.push({ stage: "emergency_call", status: "failed", target: "emergency", message: "No answer" });
      steps.push({ stage: "done", status: "failed", message: CALL_FAILED_TEXT });
      changed = true;
    }
    if (em.stage === "calling" && t >= callStart + T.RING_S) {
      push("connected", "Connected. Reading the emergency report.");
      steps.push({ stage: "emergency_call", status: "connected", target: "emergency", message: "Connected. Reading the emergency report." });
      const loc = md ? `${md.lat.toFixed(4)} N, ${Math.abs(md.lon).toFixed(4)} W` : "";
      const v = md?.last_vitals;
      const stillFor = md ? Math.round(this.sim - md.fell_at) : 0;
      em.transcript.push({
        at: this.sim, speaker: "agent",
        text: `This is an automated emergency report from Redline. Worker ${md?.worker_name} at ${md?.site_name}, GPS ${loc}, ${md?.reason === "sos" ? `pressed SOS on their band ${stillFor} seconds ago` : `collapsed, no movement for ${stillFor} seconds`}. Last estimated core temperature ${v?.core_temp?.toFixed(1) ?? "unknown"} degrees Celsius, heart rate ${v?.hr ?? "unknown"}. It started at ${md ? clockText(md.fell_at) : "unknown"}.`,
      });
      changed = true;
    }
    const conv = t - (callStart + T.RING_S);
    if (em.stage === "connected") {
      const lines: [number, "agent" | "dispatcher", string][] = [
        [7, "dispatcher", "Is the worker breathing?"],
        [9, "agent", `Unknown. Last movement was at ${md ? clockText(md.fell_at) : "unknown"}. Heart rate is still reading about ${md?.last_vitals.hr ?? "unknown"}.`],
        [14, "dispatcher", "Units are on the way. Is anyone with the worker?"],
        [17, "agent", `Crew lead ${CREW_LEAD} has been alerted and crewmates on site are heading there now.`],
      ];
      for (const [at, speaker, text] of lines) {
        if (conv >= at && !em.transcript.some((x) => x.text === text)) {
          em.transcript.push({ at: this.sim, speaker, text });
          changed = true;
        }
      }
      if (conv >= 21) {
        push("done", "Report delivered. Call logged with time, number, transcript and outcome.");
        em.outcome = "Report delivered to test dispatcher (demo). Real deployment routes through a licensed emergency-dispatch provider.";
        steps.push({ stage: "emergency_call", status: "completed", target: "emergency", message: "Call ended" });
        steps.push({ stage: "done", status: "completed", message: "Emergency report delivered." });
        changed = true;
      }
    }
    if (changed) this.emitCall(steps);
  }

  cancelEmergency(id: string) {
    const em = this.emergency;
    if (!em || em.id !== id) return { ok: false, error: "no such call" };
    if (em.stage !== "notifying" && em.stage !== "cancel_window") return { ok: false, error: "too late to cancel" };
    em.stage = "cancelled";
    em.outcome = `Cancelled by crew lead ${CREW_LEAD}: false alarm.`;
    em.steps.push({ at: this.sim, stage: "cancelled", text: em.outcome });
    this.addLog(em.outcome, "escalation");
    this.emitCall([{ stage: "done", status: "cancelled", message: em.outcome }]);
    this.resolveManDown(true);
    return { ok: true };
  }

  /** Session D's style: cancel whatever call is running for this worker. */
  cancelEmergencyForWorker(workerId: string) {
    const em = this.emergency;
    if (!em || em.worker_id !== workerId) return { ok: false, error: "no active call for this worker" };
    return this.cancelEmergency(em.id);
  }

  /** Crew lead closes the man-down screen (worker reached, or false alarm). */
  resolveManDown(falseAlarm = false) {
    const md = this.mandown;
    if (!md) return { ok: false, error: "no man-down" };
    md.stage = falseAlarm ? "cancelled" : "resolved";
    const w = this.workers.get(md.worker_id);
    if (w) { w.collapsed = false; w.stillSince = null; w.scriptedIntensity = this.demo.scenario.running ? 0.1 : null; }
    for (const a of this.alerts) if ((a.kind === "MANDOWN" || a.kind === "SOS") && !a.cleared_at && a.worker_id === md.worker_id) this.clearAlert(a.id);
    this.emit({ type: "mandown", mandown: md });
    this.mandown = null;
    this.emit({ type: "mandown", mandown: null });
    this.addLog(`${md.reason === "sos" ? "SOS" : "Man-down"} for ${md.worker_name} closed${falseAlarm ? " (false alarm)" : ""}`, "crew lead");
    this.routeAllBands();
    if (w) this.emitWorker(w);
    this.refreshSiteFlags();
    return { ok: true };
  }

  // ---- site hazards ------------------------------------------------------

  siteStopWork(siteId: string, t: number) {
    const l = this.sites.get(siteId)?.hazards.lightning.lightning;
    return !!(l && l.all_clear_at && t < l.all_clear_at && t >= (this.stopStart.get(siteId) ?? -Infinity));
  }

  /** A lightning strike `miles` from a site. Starts or extends the 30-min stop-work. */
  addStrike(siteId: string, miles: number, bearing: number, source = "GOES-19 GLM") {
    const site = SITE_BY_ID[siteId];
    const s = this.sites.get(siteId);
    if (!site || !s) return;
    const h = s.hazards.lightning;
    const l = h.lightning!;
    const p = offset(site.lat, site.lon, miles, bearing);
    l.strikes.push({ lat: p.lat, lon: p.lon, at: this.sim, miles });
    if (l.strikes.length > 20) l.strikes.shift();
    const within = miles <= CFG.LIGHTNING_RADIUS_MI;
    if (within) {
      if (!(l.all_clear_at && this.sim < l.all_clear_at)) this.stopStart.set(siteId, this.sim);   // a new stop-work starts now
      l.last_strike_at = this.sim;
      l.all_clear_at = this.sim + CFG.LIGHTNING_ALL_CLEAR_MIN * 60;
    }
    const recent = l.strikes.filter((x) => this.sim - x.at <= CFG.LIGHTNING_ALL_CLEAR_MIN * 60);
    l.nearest_miles = recent.length ? round(Math.min(...recent.map((x) => x.miles)), 1) : null;
    const active = !!(l.all_clear_at && this.sim < l.all_clear_at);
    Object.assign(h, {
      level: active ? (l.nearest_miles !== null && l.nearest_miles <= 5 ? "extreme" : "high") : "low",
      headline: `Lightning ${l.nearest_miles} mi away`,
      detail: active ? "Stop work and shelter. All clear 30 min after the last strike within 10 mi." : "Strike outside 10 mi. Watch the storm.",
      source, updated_at: this.sim, stop_work: active, band_pattern: active ? "LIGHTNING" : null,
    });
    if (active && this.usePlanner() && CREW_SITES.has(siteId)) {
      const was = this.alerts.some((a) => a.kind === "LIGHTNING" && a.site_id === siteId && !a.cleared_at);
      this.planChanges.push({ at: this.sim, text: was
        ? `New strike ${miles.toFixed(1)} mi away: the all clear moves to ${clockText(l.all_clear_at!)}.`
        : `Lightning ${miles.toFixed(1)} mi from ${site.name}: everyone stops work until ${clockText(l.all_clear_at!)} (30 min after the last strike within 10 mi).` });
    }
    if (active) {
      const open = this.alerts.find((a) => a.kind === "LIGHTNING" && a.site_id === siteId && !a.cleared_at);
      const msg = `Lightning ${miles.toFixed(1)} mi from ${site.name}. Stop work, shelter now.`;
      if (open) { open.message = msg; this.updateAlert(open); }
      else this.openAlert("LIGHTNING", null, siteId, msg, source);
      this.addLog(`Lightning strike ${miles.toFixed(1)} mi from ${site.name}. All-clear clock reset to 30:00.`, source);
    }
    this.emitSite(siteId);
    this.routeAllBands();
    for (const w of this.workers.values()) if (w.def.site_id === siteId) this.emitWorker(w);
  }

  lightningStep() {
    for (const [siteId, s] of this.sites) {
      const l = s.hazards.lightning.lightning;
      if (!l?.all_clear_at || this.sim < l.all_clear_at) continue;
      // 30 minutes with no strike within 10 miles: all clear.
      l.all_clear_at = null;
      l.nearest_miles = null;
      l.strikes = [];
      Object.assign(s.hazards.lightning, { level: "none", headline: "All clear: no strikes within 10 mi for 30 min", detail: "", stop_work: false, band_pattern: null, updated_at: this.sim });
      for (const a of this.alerts) if (a.kind === "LIGHTNING" && a.site_id === siteId && !a.cleared_at) this.clearAlert(a.id);
      this.addLog(`All clear at ${SITE_BY_ID[siteId].name}: 30 min with no lightning within 10 mi`, "GOES-19 GLM");
      this.emitSite(siteId);
      this.routeAllBands();
    }
  }

  floodFireAlert(siteId: string) {
    const s = this.sites.get(siteId);
    const site = SITE_BY_ID[siteId];
    if (!s || !site) return;
    Object.assign(s.hazards.flood, {
      level: "high", headline: "Flash Flood Warning (TEST)", detail: "Test alert from the demo controls. Move equipment off low ground.",
      source: "NWS alerts (test)", updated_at: this.sim, stop_work: false, band_pattern: "FLOOD_FIRE",
    });
    this.openAlert("FLOOD_FIRE", null, siteId, `Flash Flood Warning at ${site.name} (test alert)`, "NWS alerts (test)");
    this.addLog(`Test NWS alert: Flash Flood Warning at ${site.name}`, "demo button");
    this.emitSite(siteId);
    this.routeAllBands();
    for (const w of this.workers.values()) if (w.def.site_id === siteId) this.emitWorker(w);
  }

  clearHazards() {
    const t = this.sim;
    for (const s of SITES) {
      const fresh = baselineSite(s.id, t);
      const cur = this.sites.get(s.id)!;
      cur.hazards = fresh.hazards;
    }
    for (const a of this.alerts) if ((a.kind === "LIGHTNING" || a.kind === "FLOOD_FIRE") && !a.cleared_at) this.clearAlert(a.id);
    this.addLog("Demo hazards cleared", "demo button");
    for (const s of SITES) this.emitSite(s.id);
    this.routeAllBands();
    for (const w of this.workers.values()) this.emitWorker(w);
  }

  siteStatus(siteId: string): SiteStatus {
    const s = this.sites.get(siteId)!;
    const here = [...this.workers.values()].filter((w) => w.def.site_id === siteId);
    let flag: SiteStatus["worker_flag"] = "none";
    if (this.mandown && this.mandown.site_id === siteId) flag = this.mandown.reason === "sos" ? "sos" : "mandown";
    else if (here.some((w) => w.status !== "green")) flag = "heat";
    return {
      site_id: siteId, hazards: HAZARD_ORDER.map((k) => s.hazards[k]), index_tomorrow: s.index,
      index_drivers: s.drivers, hazard_index: s.hazardIndex, worker_flag: flag, workers_on_site: here.length, updated_at: this.sim,
    };
  }

  emitSite(siteId: string) {
    this.emit({ type: "site_status", site: this.siteStatus(siteId) });
  }

  lastFlags = new Map<string, string>();
  refreshSiteFlags() {
    for (const id of new Set(WORKERS.map((w) => w.site_id))) {
      const st = this.siteStatus(id);
      if (this.lastFlags.get(id) !== st.worker_flag) {
        this.lastFlags.set(id, st.worker_flag);
        this.emit({ type: "site_status", site: st });
      }
    }
  }

  // ---- the scripted demo -------------------------------------------------

  startScenario() {
    const timerWasOn = this.timer !== null;
    this.stop();
    this.demo = {
      ...this.demo, source: "replay", speed: 10,
      scenario: { running: true, step: "Starting", started_at: null },
    };
    this.reset(todayAt(13, 0, this.baseDate));
    this.scenarioStart = this.sim;
    this.scenarioDone.clear();
    this.demo.scenario.started_at = this.sim;
    const w = this.workers.get(DEMO_WORKER)!;
    w.scriptedIntensity = 0.3;
    this.addLog("Scripted demo started: replaying a recorded session at 10x", "demo");
    this.resendSnapshots();
    if (timerWasOn) this.start();
  }

  stopScenario() {
    this.demo.scenario = { running: false, step: null, started_at: null };
    this.scenarioStart = null;
    const w = this.workers.get(DEMO_WORKER);
    if (w) w.scriptedIntensity = null;
    this.addLog("Scripted demo stopped", "demo");
    this.emitDemo();
    this.backToRealTime();
  }

  runScenario() {
    if (!this.demo.scenario.running || this.scenarioStart === null) return;
    const minutes = (this.sim - this.scenarioStart) / 60;
    SCENARIO.forEach((step, i) => {
      if (this.scenarioDone.has(i) || minutes < step.at_min) return;
      this.scenarioDone.add(i);
      this.demo.scenario.step = step.label;
      step.run(this);
      this.emitDemo();
    });
    // Scripted reactions (button press after a red alert, crew lead cancelling the SOS).
    scenarioReactions(this);
    // End once the emergency call is finished (or after the last step if nothing is pending).
    const done = !!this.emergency && isFinalStage(this.emergency.stage);
    if (minutes >= SCENARIO_END_MIN && (done || !this.mandown) && this.scenarioDone.size === SCENARIO.length) {
      this.demo.scenario = { running: false, step: "Finished", started_at: this.demo.scenario.started_at };
      this.scenarioStart = null;
      this.addLog("Scripted demo finished", "demo");
      this.emitDemo();
    }
  }

  emitDemo() {
    this.demo.thresholds = this.thresholds();
    this.emit({ type: "demo", demo: { ...this.demo, scenario: { ...this.demo.scenario } } });
  }

  // ---- read models for REST ---------------------------------------------

  snapshot(filter: ViewFilter): Snapshot {
    const history: Snapshot["history"] = {};
    for (const w of this.workers.values()) {
      const probe: LiveEvent = { type: "vitals", vitals: { worker_id: w.def.worker_id, timestamp: 0, hr: null, skin_temp: null, activity: null, tilt: null } };
      if (canSee(filter, probe)) history[w.def.worker_id] = { vitals: [...w.vitals], estimates: [...w.estimates] };
    }
    this.demo.thresholds = this.thresholds();
    return {
      type: "snapshot", view: filter.view, sim_time: this.sim,
      workers: [...this.workers.values()].map((w) => this.workerStatus(w)),
      sites: SITES.map((s) => this.siteStatus(s.id)),
      alerts: this.alerts.slice(-60),
      mandown: this.mandown, emergency: this.emergency,
      demo: { ...this.demo, scenario: { ...this.demo.scenario } },
      reports: this.reports, log: this.log.slice(-80), history,
      band: filter.view === "crew" ? [] : this.band.slice(-20),
      offsets: Object.fromEntries([...this.workers.keys()]
        .filter((id) => id in history)
        .map((id) => [id, this.offsetUpdate(id)])),
    };
  }

  // ---- the day planner (rules in planner.ts) --------------------------------

  /** The planner runs the day on the hosted site (real clock) and in video mode; tests keep the old fixed pattern. */
  usePlanner() {
    return this.realTime || !!this.demo.scene;
  }

  /** Midnight of the sim day, local time. */
  dayStartOf(offset: number) {
    return todayAt(0, 0, this.sim * 1000) + offset * 86400;
  }

  /** The hourly forecast the planner uses for a day: video = recorded July day; else Open-Meteo; else that same sample day. */
  hoursFor(offset: 0 | 1): { hours: HourWx[]; source: string; sample: boolean } {
    const w = this.live.weather["fl-deerhaven"];
    if (!this.demo.scene && w?.hours?.length) {
      return { hours: w.hours.filter((h) => h.day === offset).map((h) => ({ hour: h.hour, hiF: h.hiF, rain: h.rain, thunder: h.thunder })), source: "Open-Meteo forecast for Deerhaven", sample: false };
    }
    return {
      hours: RECORDED_JULY_DAY.map((h) => ({ hour: h.hour, hiF: Math.round(heatIndexF(h.tempF, h.rh)), rain: h.rain, thunder: h.thunder })),
      source: this.demo.scene ? "replay of a recorded July day at Deerhaven" : "sample July day (no forecast yet)", sample: true,
    };
  }

  /** Who had a heat warning in the last 24 h (they get lighter work in the heat). */
  hadHeatWarning(id: string) {
    return this.alerts.some((a) => a.worker_id === id && (a.kind === "HEAT_YELLOW" || a.kind === "HEAT_RED") && this.sim - a.started_at < 86400);
  }

  /** The plan for today (0) or tomorrow (1), cached until the forecast or the crew changes. */
  planFor(offset: 0 | 1): DayPlan {
    const { hours, source } = this.hoursFor(offset);
    const people = [...this.workers.values()].map((w) => ({
      id: w.def.worker_id, name: w.def.name, breakOffset: w.def.breakOffset, lighter: offset === 1 && this.hadHeatWarning(w.def.worker_id),
    }));
    const key = `${this.dayStartOf(offset)}|${offset}|${this.demo.scene ?? ""}|${source}|${hours.map((h) => `${h.hiF}/${h.rain}`).join(",")}|${people.filter((p) => p.lighter).map((p) => p.id).join(",")}`;
    let plan = this.planCache.get(key);
    if (!plan) {
      plan = planDay(this.dayStartOf(offset), hours, people, source);
      if (this.planCache.size > 20) this.planCache.clear();
      this.planCache.set(key, plan);
    }
    return plan;
  }

  slotFor(id: string, t: number) {
    const offset = t >= this.dayStartOf(1) ? 1 : 0;
    return this.planFor(offset).slots[id]?.find((x) => t >= x.start && t < x.start + 1800);
  }

  /** A heat warning or danger changes that person's plan right away: a cooling break, then light work. */
  adjustForHeat(w: WorkerSim, next: Status) {
    if (!this.usePlanner() || next === "green") return;
    const id = w.def.worker_id;
    const now = this.sim;
    const breakEnd = now + (next === "red" ? 45 : 30) * 60;
    const lightEnd = breakEnd + (next === "red" ? 120 : 90) * 60;
    // Earlier changes for this person end now (kept in the past part of the plan), the new ones start now
    this.adjustments = this.adjustments
      .map((a) => (a.worker_id === id && a.to > now ? { ...a, to: now } : a))
      .filter((a) => a.from < a.to);
    this.adjustments.push(
      { worker_id: id, from: now, to: breakEnd, task: "break", label: next === "red" ? "Cool-down break (heat danger)" : "Break now (heat warning)" },
      { worker_id: id, from: breakEnd, to: lightEnd, task: "light", label: "Light work only (after heat alert)" },
    );
    const first = w.def.name.split(" ")[0];
    this.planChanges.push({ at: now, text: next === "red"
      ? `${first}: heat danger. Cool-down break until ${clockText(breakEnd)}, then light work only until ${clockText(lightEnd)}.`
      : `${first}: heat warning. Break now until ${clockText(breakEnd)}, then light work until ${clockText(lightEnd)}.` });
    for (const x of this.workers.values()) if (x === w) this.emitWorker(x);
    // Video: the overheated worker stops pushing once the band says stop, and taps "Got it" a moment later.
    // Slow the replay right down so the red moment stays on camera for a few seconds.
    if (this.demo.scene && id === this.videoHeatTarget && next === "red") {
      w.scriptedIntensity = null;
      if (this.demo.speed > 5) { this.demo.speed = 5; this.emitDemo(); }
      this.videoTimers.push({ at: now + 20, run: () => this.buttonPress(id) });
    }
  }

  schedule(day: "today" | "tomorrow" = "today"): Schedule {
    if (!this.usePlanner()) return this.legacySchedule();
    const offset = day === "tomorrow" ? 1 : 0;
    const plan = this.planFor(offset);
    const rows = [...this.workers.values()].map((w) => ({
      worker_id: w.def.worker_id, name: w.def.name,
      slots: (plan.slots[w.def.worker_id] ?? []).map((slot) => {
        if (offset === 1) return slot;
        // The slot happening right now shows what's true right now; other slots use their midpoint
        const mid = this.sim >= slot.start && this.sim < slot.start + 1800 ? this.sim : slot.start + 900;
        if (this.siteStopWork(w.def.site_id, mid)) return { start: slot.start, task: "stop_work" as TaskKind, label: LABEL.stop_work, changed: true };
        const adj = this.adjustments.find((a) => a.worker_id === w.def.worker_id && mid >= a.from && mid < a.to);
        return adj ? { start: slot.start, task: adj.task, label: adj.label, changed: true } : slot;
      }),
    }));
    return {
      date: new Date(this.dayStartOf(offset) * 1000 + 12 * 3600000).toISOString().slice(0, 10), slot_minutes: 30,
      day_start: this.dayStartOf(offset) + 6 * 3600, rows, notes: plan.notes, checked_by_rules: plan.checked,
      day, source: this.hoursFor(offset as 0 | 1).source, changes: offset === 0 ? [...this.planChanges] : [],
    };
  }

  briefing(): Briefing {
    if (!this.usePlanner()) return this.legacyBriefing();
    const plan = this.planFor(1);
    const { source } = this.hoursFor(1);
    const fmt = (h: number) => `${((Math.floor(h) + 11) % 12) + 1}${h % 1 ? ":30" : ""} ${h < 12 ? "AM" : "PM"}`;
    const ranked = SITES.map((s) => ({ s, sim: this.sites.get(s.id)! })).sort((a, b) => b.sim.index - a.sim.index);
    const crew = ranked.filter((r) => CREW_SITES.has(r.s.id));
    const pick = [...crew, ...ranked.filter((r) => !CREW_SITES.has(r.s.id)).slice(0, 5)];
    const risky = ranked.filter((r) => r.sim.index >= 5).length;
    const windows: Briefing["sites"][number]["windows"] = [];
    if (plan.heatWindow) windows.push({ kind: "heat", start_hour: plan.heatWindow[0], end_hour: plan.heatWindow[1], text: `Heat index 91 °F or more from ${fmt(plan.heatWindow[0])} to ${fmt(plan.heatWindow[1])}` });
    if (plan.stormWindow) windows.push({ kind: "lightning", start_hour: plan.stormWindow[0], end_hour: plan.stormWindow[1], text: `Storms likely ${fmt(plan.stormWindow[0])} to ${fmt(plan.stormWindow[1])}` });
    const tomorrow = new Date(this.dayStartOf(1) * 1000 + 12 * 3600000);
    return {
      generated_at: todayAt(18, 0, this.sim * 1000),
      for_date: tomorrow.toISOString().slice(0, 10),
      summary: `Tomorrow at Deerhaven: heat index up to ${plan.peakHiF} °F around ${fmt(plan.peakHour)}`
        + (plan.stormWindow ? `, storms likely ${fmt(plan.stormWindow[0])} to ${fmt(plan.stormWindow[1])}` : ", no storms in the forecast")
        + (this.demo.scene ? "." : `. ${risky} of ${SITES.length} Florida sites score 5 or more on tomorrow's risk index.`),
      sites: pick.map(({ s, sim }) => ({
        site_id: s.id, index: sim.index,
        top_hazards: HAZARD_ORDER.map((k) => sim.hazards[k]).filter((h) => h.level !== "none").slice(0, 3).map((h) => ({ kind: h.kind, level: h.level, text: h.headline })),
        windows: CREW_SITES.has(s.id) ? windows : [],
        past_incidents: [],
      })),
      schedule_suggestions: plan.notes,
      source,
    };
  }

  // ---- video mode: one button per scene on the manager dashboard ----------------

  /** The crew a scene acts on: its workers, the crew lead (owner) last. */
  videoCrew() {
    const list = [...this.workers.values()].filter((w) => (w.def.team ?? DEMO_TEAM) === this.videoTeam);
    return list.filter((w) => w.def.role !== "Crew lead").concat(list.filter((w) => w.def.role === "Crew lead"));
  }

  videoStep() {
    if (!this.videoTimers.length) return;
    const due = this.videoTimers.filter((x) => this.sim >= x.at);
    this.videoTimers = this.videoTimers.filter((x) => this.sim < x.at);
    for (const x of due) x.run();
  }

  /** Jump the demo to a scene (on a recorded July day). "live" goes back to normal. */
  scene(name: string, team: string) {
    const scenes = ["briefing", "morning", "heat", "lightning", "sos", "live"];
    if (!scenes.includes(name)) return { status: 400, json: { ok: false, error: `scene must be one of ${scenes.join(", ")}` } };
    this.demo.scenario = { running: false, step: null, started_at: null };   // a scene replaces the scripted demo
    this.scenarioStart = null;
    if (name === "live") {
      this.demo = { ...this.demo, scene: null };
      this.videoHeatTarget = null;
      if (this.realTime) this.backToRealTime();
      else { this.demo = { ...this.demo, source: "live", speed: 1 }; this.reset(todayAt(13, 0, this.baseDate)); this.resendSnapshots(); this.emitDemo(); }
      return { status: 200, json: { ok: true, scene: null } };
    }
    this.videoTeam = team === FRIENDS_TEAM ? FRIENDS_TEAM : DEMO_TEAM;
    const at = (h: number, m = 0) => todayAt(h, m, this.sim * 1000);
    const quietTo = (t: number) => {          // move the clock forward without flooding listeners
      if (t <= this.sim) return;
      const subs = this.subs; this.subs = new Set(); this.advanceSim(t - this.sim); this.subs = subs;
    };
    const prev = this.demo.scene ?? null;
    this.demo = { ...this.demo, scene: name, source: "replay", speed: 1 };

    if (name === "briefing") { this.reset(at(18, 0)); }                   // 6 PM the day before
    else if (name === "morning") { this.reset(at(7, 30)); }
    else if (name === "heat") {
      this.reset(at(12, 40));
      const target = this.videoCrew()[0];      // pick after the reset (it rebuilds everyone)
      if (target) {
        this.videoHeatTarget = target.def.worker_id;
        target.scriptedIntensity = 1;          // pushing hard through the hottest part of the day
        // An urgent repair is the one exception to the plan: heavy work in the heat, until the band says stop
        this.adjustments.push({ worker_id: target.def.worker_id, from: this.sim, to: this.sim + 3600, task: "heavy", label: "Heavy: urgent line repair" });
        this.planChanges.push({ at: this.sim, text: `${target.def.name.split(" ")[0]}: urgent line repair in the heat. Redline is watching the forecast.` });
        this.addLog(`Replay: ${target.def.name} on an urgent repair in the afternoon heat`, "video");
      }
      this.demo.speed = 60;                   // fast enough to go yellow then red in a few seconds on camera
    } else if (name === "lightning") {
      // Coming from the heat scene: keep its plan changes and move on. Otherwise start fresh at 3 PM.
      if (prev === "heat") quietTo(at(15, 5)); else this.reset(at(15, 5));
      this.addStrike("fl-deerhaven", 6.2, 300, "GOES-19 GLM (replay)");
      this.videoTimers.push({ at: this.sim + 90, run: () => this.addStrike("fl-deerhaven", 4.8, 280, "GOES-19 GLM (replay)") });
      this.demo.speed = 10;
    } else if (name === "sos") {
      const crew = this.videoCrew();
      const who = crew[1] ?? crew[0];
      if (this.mandown) this.resolveManDown(true);   // clear an old one first, or the new SOS is ignored
      if (who) this.sos(who.def.worker_id, "band");
    }
    this.demo.scene = name;
    this.resendSnapshots();
    this.emitDemo();
    return { status: 200, json: { ok: true, scene: name, team: this.videoTeam } };
  }

  legacySchedule(): Schedule {
    const day = todayAt(7, 0, this.sim * 1000);
    const rows = [...this.workers.values()].map((w) => ({
      worker_id: w.def.worker_id, name: w.def.name,
      slots: Array.from({ length: 20 }, (_, i) => {
        const start = day + i * 1800;
        const task = this.plannedTask(w, start + 60);
        const label = { heavy: "Heavy: line work", moderate: "Moderate: equipment", light: "Light: inspection", break: "Break (shade + water)", stop_work: "Stop work: lightning", off: "Off" }[task];
        return { start, task, label };
      }),
    }));
    return {
      date: new Date(day * 1000).toISOString().slice(0, 10), slot_minutes: 30, day_start: day, rows,
      notes: [
        "Heavy work moved before 11 AM where possible.",
        "Afternoon breaks every 90 min while WBGT ≥ 30 °C.",
        "Lightning stop-work windows are added automatically.",
      ],
      checked_by_rules: true,
    };
  }

  legacyBriefing(): Briefing {
    const crewSites = new Set(WORKERS.map((w) => w.site_id));
    const ranked = SITES.map((s) => ({ s, sim: this.sites.get(s.id)! }))
      .sort((a, b) => b.sim.index - a.sim.index);
    const pick = [...ranked.slice(0, 8), ...ranked.filter((r) => crewSites.has(r.s.id) && !ranked.slice(0, 8).includes(r))];
    const tomorrow = new Date(this.sim * 1000 + 86400000);
    const sitesOut = pick.map(({ s, sim }) => {
      const hz = HAZARD_ORDER.map((k) => sim.hazards[k]).filter((h) => h.level !== "none" && h.kind !== "lightning");
      const windows: Briefing["sites"][number]["windows"] = [];
      if (sim.wbgt >= 29.5) windows.push({ kind: "heat", start_hour: 11, end_hour: 17, text: `WBGT ≥ 30 °C from 11 AM to 5 PM` });
      if (sim.drivers.some((d) => d.startsWith("Thunderstorms"))) windows.push({ kind: "lightning", start_hour: 15, end_hour: 19, text: "Thunderstorms likely 3 PM to 7 PM" });
      const rng = makeRng(hashString(s.id + "inc"));
      const past: Briefing["sites"][number]["past_incidents"] = [];
      if (rng() > 0.45) past.push({ date: "2024-07-18", text: "Heat exhaustion, lineworker hospitalized (power line construction, 6 mi away)", source: "OSHA Severe Injury Reports (mock)" });
      if (rng() > 0.55) past.push({ date: "2025-06-02", text: "Lightning struck a transmission tower 3 mi away", source: "NOAA Storm Events (mock)" });
      return {
        site_id: s.id, index: sim.index,
        top_hazards: hz.slice(0, 3).map((h) => ({ kind: h.kind, level: h.level, text: h.headline })),
        windows, past_incidents: past,
      };
    });
    const high = ranked.filter((r) => r.sim.index >= 7).length;
    return {
      generated_at: todayAt(18, 0, this.sim * 1000),
      for_date: tomorrow.toISOString().slice(0, 10),
      summary: `${high} of ${SITES.length} sites score 7 or higher tomorrow. Deerhaven (your crew) scores 7.4: a hot afternoon with storms likely after 3 PM. Start heavy work at 6:30 AM and plan the afternoon around shade breaks and a possible lightning stop.`,
      sites: sitesOut,
      schedule_suggestions: [
        "Start at 6:30 AM. Finish the heavy line work at Deerhaven before 11 AM.",
        "Jake Turner: light tasks after 1 PM (ran yellow two afternoons this week).",
        "Shade breaks every 90 minutes from noon to 4 PM. Water and electrolytes on the truck.",
        "If lightning comes within 10 miles after 3 PM, stop work. The band will buzz; wait for the 30-minute all clear.",
      ],
    };
  }

  addReport(body: Partial<PhotoReport>) {
    const site = SITE_BY_ID[body.site_id ?? ""];
    if (!site) return { ok: false, error: "unknown site" };
    const kinds: ReportKind[] = ["snake", "alligator", "fire_ants", "standing_water", "downed_line", "other"];
    const r: PhotoReport = {
      id: `r-${this.nextId++}`, site_id: site.id, worker_id: body.worker_id ?? null,
      kind: kinds.includes(body.kind as ReportKind) ? (body.kind as ReportKind) : "other",
      note: String(body.note ?? "").slice(0, 500),
      lat: typeof body.lat === "number" ? body.lat : site.lat, lon: typeof body.lon === "number" ? body.lon : site.lon,
      photo: typeof body.photo === "string" && body.photo.length < 1_500_000 ? body.photo : null,
      at: this.sim,
    };
    this.reports.push(r);
    this.emit({ type: "report", report: r });
    this.addLog(`Crew photo report at ${site.name}: ${r.kind.replace("_", " ")}`, "crew report");
    return { ok: true, report: r };
  }

  // ---- REST router -------------------------------------------------------

  /** Handle one REST call. `path` is everything after /api/. */
  handle(method: string, path: string[], query: URLSearchParams, body: Record<string, unknown>): { status: number; json: unknown } {
    const ok = (json: unknown) => ({ status: 200, json });
    const bad = (error: string, status = 400) => ({ status, json: { ok: false, error } });
    const [a, b, c] = path;
    const filter: ViewFilter = { view: (query.get("view") as ViewFilter["view"]) ?? "crew", worker_id: query.get("worker_id") ?? undefined };

    if (method === "GET") {
      if (a === "state") return ok(this.snapshot(filter));
      if (a === "sites" && !b) return ok(SITES);
      if (a === "sites" && b) return SITE_BY_ID[b] ? ok({ site: SITE_BY_ID[b], status: this.siteStatus(b) }) : bad("unknown site", 404);
      if (a === "workers" && !b) return ok([...this.workers.values()].map((w) => this.workerStatus(w)));
      if (a === "workers" && b && c === "history") {
        const w = this.workers.get(b);
        if (!w) return bad("unknown worker", 404);
        if (filter.view === "crew") return bad("crew view cannot read raw biometrics", 403);
        const mins = Number(query.get("minutes") ?? 60);
        return ok({ vitals: w.vitals.filter((v) => v.timestamp >= this.sim - mins * 60), estimates: w.estimates.filter((e) => e.timestamp >= this.sim - mins * 60) });
      }
      if (a === "schedule") return ok(this.schedule(query.get("day") === "tomorrow" ? "tomorrow" : "today"));
      if (a === "briefing") return ok(this.briefing());
      if (a === "reports") return ok(this.reports);
      return bad("not found", 404);
    }

    if (method === "POST") {
      if (a === "alerts" && c === "ack") {
        const al = this.alerts.find((x) => x.id === b);
        if (!al) return bad("unknown alert", 404);
        al.acknowledged_at ??= this.sim;
        this.updateAlert(al);
        return ok({ ok: true });
      }
      if (a === "workers" && c === "button") return ok(this.buttonPress(b));
      if (a === "workers" && c === "sos") return ok(this.sos(b, "phone"));
      if (a === "workers" && c === "location") return this.setLocation(b, body);
      if (a === "demo" && b === "scene") return this.scene(String(body.scene ?? ""), String(body.team ?? DEMO_TEAM));
      if (a === "spot-check" && !b) {
        const r = this.spotCheck(String(body.worker_id ?? ""), Number(body.thermometer_c));
        return { status: r.ok ? 200 : 400, json: r };
      }
      if (a === "spot-check" && b === "reset") {
        this.offsets.delete(String(body.worker_id ?? ""));
        const id = String(body.worker_id ?? "");
        if (this.workers.has(id)) this.emit({ type: "offset_update", offset: this.offsetUpdate(id) });
        return ok({ ok: true });
      }
      if (a === "demo" && b === "sample-thermometer") {
        const t = this.sampleThermometer(String(body.worker_id ?? ""));
        return t === null ? bad("unknown worker", 404) : ok({ thermometer_c: t });
      }
      if (a === "mandown" && c === "resolve") return ok(this.resolveManDown(false));
      // Two ways to cancel: by worker (session D's suggested route, what the UI uses) or by call id.
      if (a === "emergency" && b === "cancel" && !c) return ok(this.cancelEmergencyForWorker(String(body.worker_id ?? "")));
      if (a === "emergency" && c === "cancel") return ok(this.cancelEmergency(b));
      if (a === "reports") return ok(this.addReport(body as Partial<PhotoReport>));
      if (a === "demo") {
        if (b === "test-alert") {
          const site = String(body.site_id ?? PRIMARY_SITE);
          if (body.kind === "FLOOD_FIRE") this.floodFireAlert(site);
          else this.addStrike(site, 6 + this.rng() * 3, 40 + this.rng() * 60, "GOES-19 GLM (test)");
          return ok({ ok: true });
        }
        if (b === "clear-hazards") { this.clearHazards(); return ok({ ok: true }); }
        if (b === "replay") {
          if (typeof body.on === "boolean") this.demo.source = body.on ? "replay" : "live";
          if (typeof body.speed === "number" && body.speed > 0 && body.speed <= 60) this.demo.speed = body.speed;
          if (this.demo.source === "live") this.demo.speed = 1;
          this.addLog(`Data source: ${this.demo.source}${this.demo.source === "replay" ? ` at ${this.demo.speed}x` : ""}`, "demo button");
          this.emitDemo();
          if (this.demo.source === "live") this.backToRealTime();
          return ok(this.demo);
        }
        if (b === "threshold") {
          this.demo.demo_threshold = !!body.on;
          this.addLog(this.demo.demo_threshold ? `Demo threshold ON: warning ${CFG.DEMO_HEAT_WARNING_C} °C, danger ${CFG.DEMO_HEAT_DANGER_C} °C` : "Demo threshold OFF: real thresholds 38.0 / 38.5 °C", "demo button");
          this.emitDemo();
          return ok(this.demo);
        }
        if (b === "scenario") {
          if (body.action === "start") this.startScenario();
          else this.stopScenario();
          return ok(this.demo);
        }
        if (b === "mandown") {
          this.collapse(String(body.worker_id ?? DEMO_WORKER), "demo");
          return ok({ ok: true });
        }
        if (b === "sos") return ok(this.sos(String(body.worker_id ?? DEMO_WORKER), "demo"));
        // Test the emergency path: {"format": "call" | "status", "fail_next": true}
        if (b === "emergency") {
          if (body.format === "call" || body.format === "status") this.emergencyFormat = body.format;
          if (typeof body.fail_next === "boolean") this.failNextCall = body.fail_next;
          return ok({ format: this.emergencyFormat, fail_next: this.failNextCall });
        }
      }
      return bad("not found", 404);
    }
    return bad("method not allowed", 405);
  }
}
