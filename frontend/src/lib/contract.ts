/**
 * The frontend data contract, in code.
 *
 * Every shape the UI reads from the backend lives here. The human-readable
 * version is docs/interfaces/frontend-contract.md. Keep the two in sync.
 *
 * Rules of thumb:
 * - All times are Unix seconds (floats), same as backend Reading.timestamp.
 *   During replay they are "replay time", so the UI never mixes clocks.
 * - Temperatures are °C, heart rate is bpm, distances are miles.
 * - Everything the live connection sends is a LiveEvent with a `type` field.
 *
 * This file only uses plain TypeScript (no enums), so Node can run it
 * directly in tests.
 */

// ---------------------------------------------------------------------------
// Basic labels
// ---------------------------------------------------------------------------

/** Worker heat status. Crew leads see this, never the raw numbers behind it. */
export type Status = "green" | "yellow" | "red";

/**
 * Alert types. Same names as the band's BUZZ patterns (backend/config.py), plus SOS.
 * SOS is never sent to the band: the band shows its own purple blink when the button is held 3 s.
 */
export type AlertKind = "HEAT_YELLOW" | "HEAT_RED" | "LIGHTNING" | "FLOOD_FIRE" | "MANDOWN" | "SOS";

/** What the laptop can tell the band to do, e.g. "BUZZ:HEAT_RED" (docs/firmware-contract.md). */
export type BuzzPattern = "HEAT_YELLOW" | "HEAT_RED" | "LIGHTNING" | "FLOOD_FIRE" | "MANDOWN" | "OFF";

/** Site hazards from the track brief. */
export type HazardKind = "heat" | "lightning" | "flood" | "fire" | "soil" | "wildlife";

/** How bad a hazard is right now. "none" still gets a row so the panel is complete. */
export type HazardLevel = "none" | "low" | "moderate" | "high" | "extreme";

/** Who is looking. The server only sends what that view is allowed to see. */
export type ViewKind = "crew" | "worker" | "demo";

// ---------------------------------------------------------------------------
// Sites (job sites = Florida power plants from EIA-860M)
// ---------------------------------------------------------------------------

/** Static facts about a site. Comes from plants.geojson (session C1). */
export interface Site {
  id: string;                 // e.g. "eia-663" (EIA plant ID)
  name: string;
  operator: string;
  tech: "gas" | "nuclear" | "solar" | "coal" | "oil" | "biomass";
  capacity_mw: number;
  lat: number;
  lon: number;
  county: string;
  coastal: boolean;
}

export interface Strike {
  lat: number;
  lon: number;
  at: number;                 // when it was detected
  miles: number;              // distance from the site
}

/** One hazard at one site. Every hazard has a status, a source and a time (track brief). */
export interface Hazard {
  kind: HazardKind;
  level: HazardLevel;
  headline: string;           // short, e.g. "Lightning 6.1 mi away"
  detail: string;             // one sentence for the panel
  source: string;             // e.g. "GOES-19 GLM", "NWS alerts", "Open-Meteo"
  updated_at: number;
  stop_work: boolean;         // true when work should stop (lightning within 10 mi, etc.)
  band_pattern: BuzzPattern | null;  // pattern the band gets for this hazard, if any
  stale?: boolean;            // feed is down; this is the last good copy (show greyed)
  fake?: boolean;             // test/demo data (show a TEST badge)
  /** Only for kind === "lightning". */
  lightning?: {
    nearest_miles: number | null;
    last_strike_at: number | null;
    all_clear_at: number | null;   // last strike within 10 mi + 30 min
    strikes: Strike[];             // recent strikes, newest last
  };
  /** Only for kind === "heat": the numbers behind the heat level. */
  heat?: { wbgt_c: number; heat_index_f: number };
}

/** Live status of one site: hazards now, tomorrow's index, worker flags. */
export interface SiteStatus {
  site_id: string;
  hazards: Hazard[];              // one per HazardKind, always 6
  index_tomorrow: number | null;  // 0-10 hazard index for tomorrow (session C2); null = not computed yet
  index_drivers: string[];        // top reasons, e.g. ["Heat index 108 °F", "Storms 60%"]
  /** Session C2's full predict_index() result for tomorrow, when available (index_tomorrow = hazard_index.index). */
  hazard_index?: HazardIndex | null;
  worker_flag: "none" | "heat" | "mandown" | "sos";  // a worker on site has a heat alert, man-down or SOS
  workers_on_site: number;
  updated_at: number;
}

// ---------------------------------------------------------------------------
// Workers
// ---------------------------------------------------------------------------

/** What a crew lead sees about a worker. No raw biometrics in here, on purpose. */
export interface WorkerStatus {
  worker_id: string;
  name: string;
  role: string;
  site_id: string;
  status: Status;
  /** Minutes until the forecast (90th pct) crosses the danger line. 0 = already there. null = not in the next 30 min. */
  minutes_to_danger: number | null;
  status_reason: string;          // plain words, no numbers: "Forecast to reach danger in ~6 min"
  hazard: AlertKind | null;       // most important active alert for this worker
  alert_acknowledged: boolean | null;  // null = no alert needing an ack
  on_break: boolean;
  /** Break countdown target: when the break ends (on_break) or the next break starts. */
  break_at: number | null;
  band_connected: boolean;
  updated_at: number;
}

/** One raw reading. Only sent to the worker's own view and the demo view. */
export interface Vitals {
  worker_id: string;
  timestamp: number;
  hr: number | null;
  skin_temp: number | null;
  activity: number | null;
  tilt: number | null;
}

export interface ForecastPoint {
  minutes_ahead: number;          // 1..30
  p10: number;
  p50: number;
  p90: number;
}

/** Once per minute per worker: core temp now + forecast. Worker view and demo view only. */
export interface Estimate {
  worker_id: string;
  timestamp: number;
  core: { mean: number; p10: number; p90: number };  // NOW head, calibrated range
  kalman: number;                 // ECTemp baseline, shown as a thin line
  forecast: ForecastPoint[];      // 30 points, 1..30 minutes ahead
  why: string | null;             // "Rising because heart rate jumped 18 bpm in 10 min and WBGT is 31 °C." null = no explanation this minute
  /** Personal offset already subtracted from core + forecast (see OffsetUpdate). 0 or missing = average person. */
  offset_applied_c?: number;
  thresholds: { warning: number; danger: number; demo: boolean };
  model: string;                  // e.g. "forecaster v1 + conformal"
}

// ---------------------------------------------------------------------------
// Alerts, man-down, emergency call
// ---------------------------------------------------------------------------

export interface Alert {
  id: string;
  kind: AlertKind;
  worker_id: string | null;       // null for site-wide hazard alerts
  site_id: string | null;
  started_at: number;
  message: string;
  source: string;                 // "forecaster", "GOES-19 GLM", "NWS alerts", "tilt switch", "demo button"
  acknowledged_at: number | null;
  cleared_at: number | null;
}

export type ManDownStage = "checking" | "confirmed" | "cancelled" | "resolved";

/**
 * One emergency incident for one worker: a man-down, or an SOS (band button held 3 s).
 * An SOS skips the "checking" stage: it starts at "confirmed" and escalates right away.
 */
export interface ManDown {
  id: string;
  worker_id: string;
  worker_name: string;
  site_id: string;
  site_name: string;
  lat: number;
  lon: number;
  fell_at: number;                // when movement stopped
  check_started_at: number;       // when the band started the man-down buzz
  confirm_at: number;             // when it escalates if nobody cancels
  confirmed_at: number | null;
  stage: ManDownStage;
  reason: "fall" | "no_response_to_red" | "demo" | "sos";
  /** Shown on the man-down screen to everyone, including crew leads (emergency exception to the privacy rule). */
  last_vitals: { core_temp: number | null; hr: number | null; at: number };
}

/**
 * Where the emergency call is. "done", "cancelled" and "failed" are the end.
 * "failed" = the emergency call did not go through: the screen tells the crew lead to call 911 themselves.
 */
export type EmergencyStage = "notifying" | "cancel_window" | "calling" | "connected" | "done" | "cancelled" | "failed";

/** The band sent BTN:SOS (button held 3 s), or the worker held SOS on their phone. */
export interface SosPress {
  worker_id: string;
  worker_name: string;
  site_id: string;
  site_name: string;
  lat: number;
  lon: number;
  pressed_at: number;
  source: "band" | "phone" | "demo";
  /** false if it was ignored because this worker already has an active incident (no double calls). */
  escalated: boolean;
}

export interface EmergencyCall {
  id: string;
  mandown_id: string;
  worker_id: string;
  stage: EmergencyStage;
  started_at: number;
  cancel_deadline: number | null; // crew lead can cancel ("false alarm") until this time
  demo: boolean;                  // true = calls a test phone, never real 911
  target_label: string;           // "Demo: would call 911"
  steps: { at: number; stage: EmergencyStage; text: string }[];
  transcript: { at: number; speaker: "agent" | "dispatcher"; text: string }[];
  outcome: string | null;
  /** Only when built from session D's events: dry_run = nothing dialed, fake = pretend phone, live = a real phone rang. */
  mode?: EmergencyMode;
}

// ---------------------------------------------------------------------------
// Session D's raw escalation events (backend/emergency, docs/interfaces/emergency.md)
// ---------------------------------------------------------------------------

export type EmergencyMode = "dry_run" | "fake" | "live";

/**
 * One update from session D's escalation manager. Session A passes these straight
 * through the live connection. The UI folds them into one EmergencyCall
 * (lib/emergency.ts), so the man-down screen reads the same shape either way.
 */
export interface EmergencyStatus {
  incident_id: string;            // "inc-<worker_id>-<confirmed_at>"
  worker_id: string;
  worker_name?: string | null;
  timestamp: number;              // when this update happened (same clock as `clock.sim_time`)
  stage: "notify" | "cancel_window" | "emergency_call" | "done";
  /** waiting = cancel window open. */
  status: "calling" | "connected" | "completed" | "failed" | "cancelled" | "waiting";
  target: "crew_lead" | "safety_officer" | "emergency" | null;   // null = about the whole incident
  number_masked?: string | null;  // "+1 ••• ••• 1234"
  mode?: EmergencyMode;
  demo_label?: string | null;     // "Demo: would call 911" on every emergency-leg event
  cancel_deadline?: number | null;
  message?: string;
}

// ---------------------------------------------------------------------------
// Demo controls, band, reports, planner, briefing
// ---------------------------------------------------------------------------

export interface DemoState {
  source: "live" | "replay";      // replay = recorded/synthetic data played as if live
  speed: number;                  // replay speed factor, e.g. 10
  demo_threshold: boolean;        // true = lowered, clearly labeled thresholds
  thresholds: { warning: number; danger: number };  // the ones in force right now
  scenario: { running: boolean; step: string | null; started_at: number | null };
  mock: boolean;                  // true when this is the frontend's mock server
}

export interface BandCommand {
  worker_id: string;
  command: string;                // exact line sent, e.g. "BUZZ:HEAT_RED"
  at: number;
}

export type ReportKind = "snake" | "alligator" | "fire_ants" | "standing_water" | "downed_line" | "other";

export interface PhotoReport {
  id: string;
  site_id: string;
  worker_id: string | null;
  kind: ReportKind;
  note: string;
  lat: number;
  lon: number;
  photo: string | null;           // data URL (JPEG, resized in the browser) or null
  at: number;
}

export type TaskKind = "heavy" | "moderate" | "light" | "break" | "stop_work" | "off";

/** Planner schedule (session B): 30-minute slots per worker. */
export interface Schedule {
  date: string;                   // "2026-10-17"
  slot_minutes: number;           // 30
  day_start: number;              // Unix seconds of the first slot
  rows: { worker_id: string; name: string; slots: { start: number; task: TaskKind; label: string }[] }[];
  notes: string[];                // planner notes, e.g. "Heavy work moved before 11 AM"
  checked_by_rules: boolean;      // passed the safety rule checker (code, not LLM)
}

export interface Briefing {
  generated_at: number;           // 6 PM the day before
  for_date: string;
  summary: string;
  sites: {
    site_id: string;
    index: number;                // 0-10
    top_hazards: { kind: HazardKind; level: HazardLevel; text: string }[];
    windows: { kind: HazardKind; start_hour: number; end_hour: number; text: string }[];
    past_incidents: { date: string; text: string; source: string }[];
  }[];
  schedule_suggestions: string[];
}

export interface LogEntry {
  at: number;
  text: string;
  source: string;
}

// ---------------------------------------------------------------------------
// The live connection: every message is one of these
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// 0-10 hazard index (session C2, ml/hazard_index/predict.py: predict_index)
// ---------------------------------------------------------------------------

export type IndexHazard = "heat" | "storm" | "flood" | "fire" | "tropical";

export interface HazardIndex {
  site_id: string;
  date: string;                   // the day being scored ("tomorrow" at 6 PM)
  index: number;                  // 0-10, expected level on the label ladder
  band: "low" | "moderate" | "high" | "extreme";   // low <2, moderate <4, high <6, extreme 6+
  climatology_index: number;      // what a usual day this month scores, for comparison
  p_advisory: number;
  p_warning: number;
  p_damage: number;
  hazards: Partial<Record<IndexHazard, { index: number; p_advisory: number; p_warning: number; p_damage: number; climatology_index: number }>>;
  top_reasons: { hazard: IndexHazard; feature: string; value: number; points: number; text: string }[];
  cutoff: string;                 // latest data the score may use (6 PM the day before)
  data_mode: "archive" | "live" | "mock";
  model_version: string;
  notes: string[];
}

// ---------------------------------------------------------------------------
// Personal offset from thermometer spot checks (backend/models/personal_offset.py)
// ---------------------------------------------------------------------------

/**
 * "Learning your body". After each ear/oral thermometer spot check:
 *   miss = model estimate - thermometer;  offset = sum(misses) / (checks + 2), capped at ±0.6 °C
 * The live estimate then shows (model - offset). Never changes a safety threshold.
 */
export interface OffsetUpdate {
  worker_id: string;
  timestamp: number;
  offset_c: number;               // + = the model reads this worker HOTTER than they really are
  confidence: number;             // 0 = average person only, toward 1 = mostly their own history
  checks: number;                 // accepted spot checks so far
  last_check: { thermometer_c: number; model_estimate_c: number; miss_c: number; accepted: boolean; at: number } | null;
  history: { timestamp: number; offset_c: number; confidence: number; checks: number }[];  // oldest first
}

/** Reply to POST /api/spot-check {worker_id, thermometer_c}. */
export interface SpotCheckResult {
  ok: boolean;
  accepted: boolean;              // false = looked like a bad reading (more than 1.5 °C off), ignored
  message: string;
  offset: OffsetUpdate | null;
}

export interface Snapshot {
  type: "snapshot";
  view: ViewKind;
  sim_time: number;
  workers: WorkerStatus[];
  sites: SiteStatus[];
  alerts: Alert[];
  mandown: ManDown | null;
  emergency: EmergencyCall | null;
  demo: DemoState;
  reports: PhotoReport[];
  log: LogEntry[];
  /** Only for views allowed to see raw biometrics. Keyed by worker_id. */
  history: Record<string, { vitals: Vitals[]; estimates: Estimate[] }>;
  band: BandCommand[];
  /** Personal offsets, only for views allowed to see raw biometrics. Optional: older backends omit it. */
  offsets?: Record<string, OffsetUpdate>;
}

export type LiveEvent =
  | Snapshot
  | { type: "clock"; sim_time: number; speed: number }
  | { type: "worker_status"; worker: WorkerStatus }
  | { type: "vitals"; vitals: Vitals }
  | { type: "estimate"; estimate: Estimate }
  | { type: "alert"; alert: Alert }
  | { type: "mandown"; mandown: ManDown | null }
  | { type: "sos"; sos: SosPress }
  | { type: "emergency"; emergency: EmergencyCall | null }
  | ({ type: "emergency_status" } & EmergencyStatus)
  | { type: "site_status"; site: SiteStatus }
  | { type: "demo"; demo: DemoState }
  | { type: "band_command"; band: BandCommand }
  | { type: "report"; report: PhotoReport }
  | { type: "log"; entry: LogEntry }
  | { type: "offset_update"; offset: OffsetUpdate };

/** Who may see what. The server applies this; the UI never receives what it shouldn't. */
export interface ViewFilter {
  view: ViewKind;
  worker_id?: string;             // required for view === "worker"
}

/**
 * Privacy rule. Crew leads get status only. Raw vitals and core temp go to
 * the worker's own phone and the labeled demo view.
 */
export function canSee(filter: ViewFilter, ev: LiveEvent): boolean {
  if (ev.type === "vitals" || ev.type === "estimate" || ev.type === "offset_update") {
    // offset_update comes wrapped ({offset: {...}}) from our mock, flat ({worker_id, offset_c, ...}) from session A.
    const flat = ev as unknown as { worker_id?: string; offset?: { worker_id?: string } };
    const id = ev.type === "vitals" ? ev.vitals?.worker_id : ev.type === "estimate" ? ev.estimate?.worker_id
      : flat.offset?.worker_id ?? flat.worker_id;
    if (filter.view === "demo") return true;
    if (filter.view === "worker") return filter.worker_id === id;
    return false;
  }
  if (ev.type === "band_command") return filter.view !== "crew";
  return true;
}

// ---------------------------------------------------------------------------
// Display helpers shared by the UI
// ---------------------------------------------------------------------------

export const HAZARD_ORDER: HazardKind[] = ["heat", "lightning", "flood", "fire", "soil", "wildlife"];

export const HAZARD_LABEL: Record<HazardKind, string> = {
  heat: "Heat",
  lightning: "Lightning",
  flood: "Flood",
  fire: "Wildfire",
  soil: "Soil",
  wildlife: "Wildlife",
};

export const ALERT_LABEL: Record<AlertKind, string> = {
  HEAT_YELLOW: "Heat warning",
  HEAT_RED: "Heat danger",
  LIGHTNING: "Lightning",
  FLOOD_FIRE: "Flood / fire weather",
  MANDOWN: "Man down",
  SOS: "SOS",
};

export const LEVEL_RANK: Record<HazardLevel, number> = { none: 0, low: 1, moderate: 2, high: 3, extreme: 4 };
