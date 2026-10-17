# Frontend data contract (draft v2: adds SOS)

Owner: session E (frontend). Status: **draft**, built against the frontend's mock server. Session A (live system) should follow it or tell E what to change.

The exact field-by-field types live in code: [`frontend/src/lib/contract.ts`](../../frontend/src/lib/contract.ts). This doc explains them. If the two disagree, the code wins, and please fix this doc.

## How the frontend connects

| What | Real backend (session A) | Frontend mock (today) |
| --- | --- | --- |
| Live events | WebSocket `ws://<backend>/ws/live?view=<view>[&worker_id=<id>]` | Server-Sent Events `GET /api/stream?view=...` |
| REST | `http://<backend>/api/...` | same paths, served by Next.js at `/api/...` |
| Switch | set `NEXT_PUBLIC_BACKEND_URL=http://localhost:8000` in `frontend/.env.local` | leave it empty |

- Every live message is one JSON object with a `type` field (list below). The first message after connecting is always a `snapshot`.
- The backend needs CORS for the frontend's origin (`http://localhost:3000`) on `/api/*`.
- If the WebSocket drops, the UI reconnects (1 s, 2 s, 4 s ... 10 s) and expects a fresh `snapshot`.

## Units and time

- **All times are Unix seconds** (float), like `Reading.timestamp`.
- During replay, times are **replay time** (the timestamps in the replayed data), never wall clock. The UI animates countdowns with the `clock` event (`sim_time` + `speed`).
- °C, bpm, miles. Hazard index is 0-10.

## Privacy (who gets what)

The server filters events by `view`. The UI never receives what it shouldn't.

| View | Used by | Gets raw vitals + core temp? |
| --- | --- | --- |
| `crew` | Crew lead dashboard, briefing, report form | **No.** Status, minutes until danger, hazards, alerts only |
| `worker` + `worker_id` | That worker's phone | Only their own |
| `demo` | Labeled demo view for judges (wearer consents) | Yes, all workers |

Exception: the man-down / SOS incident carries `last_vitals` (core temp, HR) for everyone, because responders need them.

`GET /api/workers/{id}/history?view=crew` must return **403**.

## Live events (server → UI)

| `type` | When | Payload |
| --- | --- | --- |
| `snapshot` | On connect, and after a reset (scenario start) | Everything: `workers`, `sites` (statuses), `alerts`, `mandown`, `emergency`, `demo`, `reports`, `log`, `history` (raw data, only for allowed views), `band`, `sim_time`, `view` |
| `clock` | ~2x per second | `sim_time`, `speed` |
| `worker_status` | Each minute and on any change | `worker`: WorkerStatus |
| `vitals` | Each reading (~5 s) | `vitals`: worker_id, timestamp, hr, skin_temp, activity, tilt |
| `estimate` | Each minute per worker | `estimate`: Estimate (core now + 30-min forecast) |
| `alert` | Opened, acknowledged, updated or cleared | `alert`: Alert (same `id` = same alert, upsert) |
| `mandown` | Check starts, confirmed, cancelled, resolved (also used for SOS incidents) | `mandown`: ManDown, or `null` when closed |
| `sos` | The band sent `BTN:SOS` (button held 3 s), or the phone's SOS button | `sos`: SosPress |
| `emergency` | Every stage change and transcript line | `emergency`: EmergencyCall, or `null` |
| `site_status` | Hazard change at a site, worker flag change | `site`: SiteStatus |
| `demo` | Any demo control change, scenario step | `demo`: DemoState |
| `band_command` | Each command sent to a band (not to `crew`) | `band`: worker_id, command (`"BUZZ:HEAT_RED"`), at |
| `report` | New crew photo report | `report`: PhotoReport |
| `log` | Anything worth a line in the event log | `entry`: at, text, source |

### WorkerStatus (what a crew lead sees)

```json
{
  "worker_id": "worker-1", "name": "Maria Lopez", "role": "Lineworker", "site_id": "fl-deerhaven",
  "status": "yellow",
  "minutes_to_danger": 13,
  "status_reason": "Forecast to reach danger in ~13 min.",
  "hazard": "HEAT_YELLOW",
  "alert_acknowledged": null,
  "on_break": false, "break_at": 1792258200,
  "band_connected": true, "updated_at": 1792256640
}
```

- `status`: green / yellow / red from the spec's rules (yellow: 20-min 90th pct ≥ warning; red: estimate or 10-min 90th pct ≥ danger).
- `minutes_to_danger`: first minute the 90th percentile crosses the danger line. `0` = already there, `null` = not within 30 min.
- `status_reason`: plain words, **no numbers** (privacy).
- `hazard`: top active alert by router priority `MANDOWN > HEAT_RED > LIGHTNING > HEAT_YELLOW > FLOOD_FIRE`.
- `alert_acknowledged`: only for a red alert (band button pressed); `null` otherwise.
- `break_at`: when the break ends (if `on_break`) or the next break starts. From the planner's schedule; lightning stop-work counts as a break.

### Estimate (worker and demo views only)

```json
{
  "worker_id": "worker-1", "timestamp": 1792256640,
  "core": { "mean": 37.82, "p10": 37.67, "p90": 37.97 },
  "kalman": 37.61,
  "forecast": [ { "minutes_ahead": 1, "p10": 37.83, "p50": 37.86, "p90": 37.90 }, "... 30 points ..." ],
  "why": "Rising because heart rate jumped 18 bpm in 10 min and WBGT is 31 °C.",
  "thresholds": { "warning": 38.0, "danger": 38.5, "demo": false },
  "model": "forecaster v1 + conformal"
}
```

- `forecast` has all 30 minutes (the forecaster already outputs 1..30). The chart shades p10–p90.
- `thresholds` are the ones in force (demo or real), so the chart's lines always match the alerts.
- `why` comes from the explainability step (Phase 5). Until then, a simple rule-based sentence is fine.

### Alert

`id, kind, worker_id (null for site-wide), site_id, started_at, message, source, acknowledged_at, cleared_at`. `kind` is one of `HEAT_YELLOW, HEAT_RED, LIGHTNING, FLOOD_FIRE, MANDOWN` (same names as the BUZZ patterns) or `SOS`. **SOS is never sent to the band as a command**: the band blinks purple by itself when the button is held. Every hazard event keeps its `source` and times, so the log is the site's record.

Priority (for `WorkerStatus.hazard` and the band router): `SOS`, `MANDOWN` > `HEAT_RED` > `LIGHTNING` > `HEAT_YELLOW` > `FLOOD_FIRE`. The band router skips `SOS`.

### ManDown (one emergency incident: man-down or SOS)

`id, worker_id, worker_name, site_id, site_name, lat, lon, fell_at, check_started_at, confirm_at, confirmed_at, stage, reason, last_vitals {core_temp, hr, at}`.

- `stage`: `checking` (band buzzing, worker can cancel) → `confirmed` (crew lead + crewmates alerted) → `resolved` / `cancelled`.
- `confirm_at`: when it escalates if nobody cancels. The UI counts down to it.
- `reason`: `fall`, `no_response_to_red`, `demo`, or `sos`.
- **SOS** (`reason: "sos"`): skips `checking`. It starts at `confirmed` with `fell_at = check_started_at = confirm_at = confirmed_at = pressed_at`, and the emergency call starts right away (same chain as man-down, session D). The UI shows a full-screen purple "SOS from <worker>" instead of "Man down".
- One incident per worker at a time. An SOS while an incident is already active is ignored (no double calls; matches session D).

### SosPress (session A, from `BTN:SOS`)

`worker_id, worker_name, site_id, site_name, lat, lon, pressed_at, source ("band" | "phone" | "demo"), escalated`.

Sent once per press. `escalated: false` means it was ignored because an incident was already active. The incident itself arrives as a `mandown` event with `reason: "sos"`, and the call as `emergency` events.

### EmergencyCall (session D)

`id, mandown_id, worker_id, stage, started_at, cancel_deadline, demo, target_label, steps[], transcript[], outcome`.

- `stage`: `notifying` → `cancel_window` → `calling` → `connected` → `done`, or `cancelled`.
- `demo: true` + `target_label: "Demo: would call 911"`. The UI shows this label big. Never real 911.
- `transcript`: `{at, speaker: "agent" | "dispatcher", text}` lines, appended as the call goes.
- `steps`: the call log (time + text for each stage).

### SiteStatus (sessions C1 + C2)

```json
{
  "site_id": "fl-deerhaven",
  "hazards": [ "... one per kind, always 6, in order heat, lightning, flood, fire, soil, wildlife ..." ],
  "index_tomorrow": 7.4,
  "index_drivers": ["Heat index 111 °F tomorrow", "Thunderstorms 50% after 3 PM"],
  "worker_flag": "heat",               // none | heat | mandown | sos
  "workers_on_site": 4,
  "updated_at": 1792256640
}
```

Hazard: `kind, level (none|low|moderate|high|extreme), headline, detail, source, updated_at, stop_work, band_pattern`, plus:

- `lightning`: `nearest_miles, last_strike_at, all_clear_at, strikes[{lat, lon, at, miles}]`. `all_clear_at` = last strike within 10 mi + 30 min. The UI shows the 10-mile ring and counts down to `all_clear_at`.
- `heat`: `wbgt_c, heat_index_f`.

### DemoState

`source (live|replay), speed, demo_threshold, thresholds {warning, danger}, scenario {running, step, started_at}, mock`.

## REST

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| GET | `/api/state?view=...&worker_id=...` | | Snapshot (same as the first live message) |
| GET | `/api/sites` | | `Site[]`: id, name, operator, tech, capacity_mw, lat, lon, county, coastal (from `plants.geojson`) |
| GET | `/api/sites/{id}` | | `{site, status}` |
| GET | `/api/workers` | | `WorkerStatus[]` |
| GET | `/api/workers/{id}/history?minutes=60&view=...` | | `{vitals[], estimates[]}`; 403 for `view=crew` |
| GET | `/api/schedule` | | Schedule (session B): `date, slot_minutes, day_start, rows[{worker_id, name, slots[{start, task, label}]}], notes[], checked_by_rules` |
| GET | `/api/briefing` | | Briefing (session B): `generated_at, for_date, summary, sites[{site_id, index, top_hazards[], windows[], past_incidents[]}], schedule_suggestions[]` |
| GET | `/api/reports` | | `PhotoReport[]` |
| POST | `/api/reports` | `{site_id, worker_id?, kind, note, photo?, lat?, lon?}` | `{ok, report}`. `photo` is a JPEG data URL, already shrunk to ≤1024 px in the browser. `kind`: snake, alligator, fire_ants, standing_water, downed_line, other |
| POST | `/api/alerts/{id}/ack` | | `{ok}` |
| POST | `/api/workers/{id}/button` | | Same as the band's `BTN:1`: cancels a man-down check, else acknowledges a red alert. `{ok, action}` |
| POST | `/api/workers/{id}/sos` | | Same as the band's `BTN:SOS` (the phone view has a hold-3-s SOS button). `{ok, action: "escalated" \| "ignored_active_incident"}` |
| POST | `/api/mandown/{id}/resolve` | | Crew lead closes the man-down screen |
| POST | `/api/emergency/{id}/cancel` | | Crew lead's "Cancel, false alarm" (only in `notifying` / `cancel_window`) |

`task` in the schedule: `heavy, moderate, light, break, stop_work, off`.

### Results, twins, planner chat (types in `frontend/src/lib/contract-extra.ts`)

| Method | Path | Owner | Returns |
| --- | --- | --- | --- |
| GET | `/api/results` | ML session (A serves it) | `ModelResults`: `updated, split, models[]` (first = Kalman baseline), `rows[{group, name, unit, better: lower\|higher\|closer, target?, values[], text?[]}]`, `rmse_by_horizon[{minutes_ahead, values[]}]`, `notes[]`. Until a backend serves it, the page shows a bundled copy of `docs/results.md`. |
| GET | `/api/twins` | ML session (Phase 6) | `TwinReport`: `available, mock, note, workers[{worker_id, name, days[{day, hr_rmse_bpm, core_rmse_c, kept}]}]`. 404 or `available: false` shows "not trained yet". |
| POST | `/api/planner/chat` | B (already built) | `{message, session_id?}` → `{session_id, reply, tool_calls[{tool, input}], plan, llm, cost_usd}` |

### Demo controls (one click each)

| Method | Path | Body | Does |
| --- | --- | --- | --- |
| POST | `/api/demo/scenario` | `{action: "start" \| "stop"}` | Plays the scripted demo (below) |
| POST | `/api/demo/test-alert` | `{kind: "LIGHTNING" \| "FLOOD_FIRE", site_id?}` | Test hazard alert at the crew site (default `fl-deerhaven`) |
| POST | `/api/demo/clear-hazards` | | Clears test hazards |
| POST | `/api/demo/replay` | `{on?: bool, speed?: number}` | Replay on/off and speed (1, 5, 10, 30) |
| POST | `/api/demo/threshold` | `{on: bool}` | Demo thresholds (37.3 / 37.5 °C from `config.py`) |
| POST | `/api/demo/mandown` | `{worker_id}` | Fakes a fall for that worker |
| POST | `/api/demo/sos` | `{worker_id}` | Fakes an SOS (button held 3 s) for that worker |

## The scripted demo (what the mock plays)

At 10x replay (1 replay minute = 6 real seconds). About 2 min 55 s total. Heat alerts come from the alert rules, not the script.

| Replay min | What happens | Screens |
| --- | --- | --- |
| 0 | Maria (wearing the band) does light work. All green | |
| 3 | Hard exercise starts. Forecast climbs | Demo chart band rises |
| ~4 | Yellow (20-min 90th pct ≥ 38.0) | Card turns yellow, "danger in ~13 min", band `BUZZ:HEAT_YELLOW` |
| ~8 | Red. ~20 s later she presses the band button (ack) | `BUZZ:HEAT_RED`, "✓ Acknowledged" |
| 13–15.5 | Lightning at 8.6, 6.2, 4.8 mi | Stop-work banner, 10-mile ring, 30:00 countdown, `BUZZ:LIGHTNING` |
| 14 | Jake holds his band button 3 s: SOS. Replay drops to 1x | Full-screen purple "SOS from Jake Turner", escalation starts at once |
| 14 +8 s | 5 s into the cancel window, the crew lead cancels: false alarm (script does it if the presenter doesn't). Replay back to 10x | Shows the cancel button working |
| 17 | She collapses. Replay drops to 1x | |
| +10 s | Man-down check (band buzzes) | Full-screen alert on every page |
| +25 s | Confirmed. Crew lead + crewmates alerted | `MANDOWN` alert, map flag |
| +28 s | Cancel window (10 s) | "Cancel, false alarm" button |
| +38 s | Calling. "Demo: would call 911" | |
| +42 s | Connected, report read, follow-up Q&A | Live transcript |
| +63 s | Done, call logged | |

The man-down and call timings above are **shortened demo timings** in the mock. The real ones belong to sessions A (`MANDOWN_STILL_S`, `MANDOWN_ESCALATE_S` in `config.py`) and D (`EMERGENCY_CANCEL_WINDOW_S` 30 s, `EMERGENCY_DEMO_CANCEL_WINDOW_S` 10 s, which the mock uses).

## Band light (one LED)

Where the UI shows the band's state (demo view), it follows the firmware contract (`docs/firmware-contract.md`):

| State | Light |
| --- | --- |
| `BUZZ:OFF` | Solid green (all clear) |
| `BUZZ:HEAT_YELLOW` | Solid yellow |
| `BUZZ:HEAT_RED` | Flashing red, 2 per second |
| `BUZZ:LIGHTNING` | Rapid white flashes, 5 per second |
| `BUZZ:FLOOD_FIRE` | Solid blue |
| `BUZZ:MANDOWN` | Red breathing pulse |
| SOS (band's own feedback, not a command) | Blinking purple |
| No command for 60 s (`band_connected: false`) | Slow cyan blink |

## Who fills what

| Data | Produced by |
| --- | --- |
| WorkerStatus, Vitals, Alert, ManDown, band commands, DemoState, replay, WebSocket, REST shell | A (live system) |
| Estimate (core, forecast, why) | ML session (`backend/models`), wired by A |
| Schedule, Briefing | B (planner) |
| Site list, Hazard per site, lightning rule | C1 (site hazards) |
| `index_tomorrow`, `index_drivers` | C2 (hazard index) |
| EmergencyCall | D (emergency escalation) |
| PhotoReport storage | A |

## Known gap: session D's `emergency_status`

Session D's interface (`docs/interfaces/emergency.md` on their branch) sends one flat `emergency_status` event per step (`stage: notify | cancel_window | emergency_call | done`, `status`, `target`, `demo_label`, `cancel_deadline`, `message`). The UI currently reads the aggregated `EmergencyCall` above. Plan: the frontend folds `emergency_status` events into `EmergencyCall` itself, so session A can pass D's events straight through. Not built yet. Also: show D's `done/failed` ("Emergency call FAILED. Call 911 yourself now.") big.

## Open questions for session A

1. OK with one WebSocket at `/ws/live` with the `view` query param doing the privacy filtering?
2. OK with replay time in every timestamp plus a `clock` event, instead of wall time?
3. Photos as data URLs in JSON (no multipart, no extra dependency), capped at ~1.5 MB?
4. Who computes `minutes_to_danger` and `status_reason`: the alert module (A) or the forecaster wrapper? The UI only needs the fields.
