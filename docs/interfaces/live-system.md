# Live system interface (session A)

Owner: session A. Code: `backend/app`, `backend/alerts`, `backend/replay`, `backend/ingest`, `backend/storage`.

One Python process: FastAPI + background tasks. Start it:

```bash
python -m backend.app.main --source replay --speed 10      # demo, no hardware
python -m backend.app.main --source live                   # band (USB/WiFi) + Pulsoid
```

Default port 8000 (`SERVER_PORT`). Open `http://localhost:8000` for the bare chart page.

Useful flags: `--speed 30` (faster replay), `--demo` (demo thresholds 37.3 / 37.5), `--hazards fake|live|off` (session C1's feeds; fake by default in replay), `--emergency-phone dry_run|fake|live` (session D; never dials 911), `--checkpoint <file>`, `--no-forecaster`, `--no-fall`, `--no-ack`, `--record`.

### What's plugged in (each one falls back cleanly when missing)

| Piece | Where it comes from | When it's missing |
| --- | --- | --- |
| Forecaster | `ml/checkpoints/ens5.pt` (5-model ensemble), else `v1.pt` | Kalman-only alerts (Kalman rule) |
| Personal offset twin | `backend/models/personal_offset.py` + `POST /spot-check` | offset 0 (average person) |
| Why-lines | session H, `ml/explain/` (branch `claude/ml-explain-071c08`) | `estimate.why` is null; `/ws/live` uses a plain heart-rate sentence |
| Site hazards | session C1, `backend/hazards/` + `backend/sites/` | one demo site `site-gnv`, demo-button hazards only |
| Emergency escalation | session D, `backend/emergency/` | `mandown_confirmed` / `sos` still go to the dashboard, nobody is called |

Heart-rate twins stay off (they didn't help on real people, see `docs/results.md`).

There are **two live streams**:

| Stream | Who uses it | Shapes |
| --- | --- | --- |
| `WS /ws/live?view=crew\|worker\|demo[&worker_id=]` | The Next.js dashboard (session E) | The frontend contract (`frontend/src/lib/contract.ts`, `docs/interfaces/frontend-contract.md` on E's branch). Privacy filter applied by view. |
| `WS /ws` | The bare chart page, debugging, any Python consumer | The hub's raw events (below). No filter. |

All times are Unix seconds. During replay they are **replay time** (the virtual clock), never wall time.

---

## 1. Hub events (raw `/ws`, `hub.add_listener`)

Every event is a dict with `type`, `timestamp` and (when it's about a worker) `worker_id`.
The first `/ws` message is a `snapshot` (`GET /api/raw/state` returns the same).

| `type` | When | Main fields |
| --- | --- | --- |
| `reading` | Every input reading | `hr, skin_temp, tilt, activity, accel` (null = not in this line), `source`: `watch` (Pulsoid), `band` (band lines, incl. its pulse sensor's `HR:`), `replay`, `demo` |
| `button` | Band button (raw input) | `kind`: `press` (`BTN:1`, "I'm OK") or `sos` (`BTN:SOS`, held 3 s) |
| `estimate` | Once per worker per minute | `minute, hr` (devices blended), `hr_watch, hr_band, hr_sources` (devices that fed the Kalman this minute), `skin_temp, activity, wbgt, kalman, kalman_sd, core_temp, core_temp_sd` (personal offset applied), `core_temp_raw, offset_c` (before it), `forecast{minutes[1..30], p10[], p50[], p90[]}` (offset applied) or null, `model, heat_level, why` (latest why-line or null), `true_core_temp` (replay of sim only) |
| `why` | A why-line is ready (0.1-0.3 s after its minute, background thread) | `minute, why, direction` rising/steady/falling, `contributions` {heart_rate, body_heat, skin_temp, activity, wbgt, workload, time} in °C. Raw `/ws` only; on `/ws/live` it fills in `Estimate.why` |
| `offset_update` | `POST /spot-check` | `offset_c, confidence, n_checks, accepted, thermometer_c, model_c, miss_c, reason`. Passed through on `/ws/live` as-is, **only to `view=demo` and `view=worker` for that worker** (body data, never crew) |
| `heat_alert` | Heat level changes | `level` clear/yellow/red, `previous, value, threshold, rule` kalman/forecast, `demo` |
| `heat_ack` | Button pressed during red | `after_s` |
| `red_unacknowledged` | Red with no button for `HEAT_RED_ACK_TIMEOUT_S` | `red_since` |
| `band_command` | The band's pattern changes (only on change) | `pattern, line` e.g. `"BUZZ:HEAT_RED"` |
| `mandown_check` | Tipped + still `MANDOWN_STILL_S`, or red unacknowledged, or demo | `reason` fall / heat_red_unacknowledged / demo_fall, `started_at` |
| `mandown_cancelled` | Button or movement during the check | `by` button/movement, `reason, check_started` |
| `mandown_confirmed` | Not cancelled within `MANDOWN_ESCALATE_S` | see §3 |
| `mandown_worker_responded` | Button pressed after confirmation (does not cancel) | `by` |
| `mandown_resolved` | Crew lead closed it | `by` |
| `sos` | Band `BTN:SOS` (held 3 s) or phone/demo SOS. Starts session D's escalation **right away** (no stillness wait). Never cancels a man-down and is never sent to the band | same who/where/vitals fields as §3 + `pressed_at, source` band/phone/demo |
| `hazard` | Site hazard on/off | `site_id, kind, on, pattern, source, details` |
| `emergency_status` | Session D's escalation steps (passed through) | D's `EmergencyStatus` |
| `worker` / `worker_removed` | Worker created / removed | `name, site_id, source` |
| `demo` | Demo thresholds toggled | `demo, warning_c, danger_c` |
| `replay` | Replay started/stopped/speed | `on, speed, description, offset_s` |
| `plan` | Session B posted a plan | `source, plan` |

Band priority (one pattern at a time): `MANDOWN > HEAT_RED > LIGHTNING > HEAT_YELLOW > FLOOD_FIRE`, else `OFF`.
SOS is never sent to the band (the band shows it by itself).

### Heart rate from two devices

Heart rate can come from the watch (Pulsoid, tagged `watch`) and/or the band's pulse sensor (`HR:` lines, tagged `band`; the band only sends it when confident). Each device gets its own 1-minute average. The Kalman runs `ECTempKalman.step_fused([(hr_watch, KALMAN_SIGMA_WATCH), (hr_band, KALMAN_SIGMA_BAND_PPG)])` every minute, skipping a device with no reading that minute (watch only = exactly ECTemp). The forecaster was trained on one HR input, so it gets the two minute averages blended with the same 1/sigma² weights. Note: its internal Kalman baseline is then a plain single-sigma Kalman on that blend, so `estimate.kalman` (fused) and the forecaster's own baseline can differ slightly when both devices are present.

### Personal offset twin (thermometer spot checks)

`POST /spot-check` (alias `/api/spot-check`) `{worker_id, thermometer_c, timestamp?}`. The miss is the model's NOW estimate **before** any offset (the estimate nearest `timestamp`, default now on that worker's clock) minus the thermometer reading, fed to `PersonalOffset.add_check` (it rejects readings more than 1.5 °C off). `offset_c` is subtracted from the NOW estimate and every forecast percentile from then on, and the heat alert is re-checked right away. Capped at ±0.6 °C by the ML session's code; thresholds never change. Offsets are saved per worker in SQLite (`offsets`, every check in `spot_checks`) and reloaded when the worker reappears. Returns the `offset_update` event.

### Heat alert rules (all numbers in `backend/config.py`)

- Kalman only (no checkpoint): yellow when estimate ≥ `HEAT_WARNING_C` (38.0), red when ≥ `HEAT_DANGER_C` (38.5).
- With forecaster v1: yellow when the 20-min 90th pct ≥ 38.0; red when the current estimate or the 10-min 90th pct ≥ 38.5.
- Hysteresis: an alert drops only `ALERT_CLEAR_MARGIN_C` (0.1) below its threshold.
- Demo thresholds 37.3 / 37.5 switchable live (re-checked immediately).
- The band repeats the red buzz itself every `HEAT_RED_REPEAT_S` until a short press; the laptop sends `BUZZ:HEAT_RED` on the change.
- Keep-alive: every `BAND_RESEND_S` (20) real seconds the laptop re-sends each real band its current status (serial/UDP only, not the console, not replay workers), so the band never shows "no connection" (cyan after 60 s). **Firmware must treat a repeat of the command it's already showing as no change**, so a re-sent `BUZZ:HEAT_RED` or `BUZZ:MANDOWN` doesn't restart beeping the worker already silenced with a short press.

---

## 2. REST

Every route is `async` (runs on the event loop, so the hub is single-threaded). CORS allows `FRONTEND_ORIGINS` (default `http://localhost:3000`).

### Frontend contract (session E)

| Method | Path | Body / query | Returns |
| --- | --- | --- | --- |
| GET | `/api/state` | `view, worker_id` | contract Snapshot |
| GET | `/api/workers` | | `WorkerStatus[]` |
| GET | `/api/workers/{id}/history` | `view, minutes=60` | `{vitals[], estimates[]}`; **403** for `view=crew` |
| GET | `/api/sites` | | `Site[]` (C1's 79 plants when merged, else one demo site `site-gnv`) |
| GET | `/api/schedule` | | latest plan from B with `rows` (Schedule shape), else 404 |
| GET / POST | `/api/briefing` | Briefing (B posts it) | 404 until B posts one |
| GET / POST | `/api/reports` | `{site_id?, worker_id?, kind, note, photo?, lat?, lon?}` | `{ok, report}`; photo = JPEG data URL, 413 if over ~1.5 MB |
| POST | `/api/workers/{id}/button` | | same as band `BTN:1` |
| POST | `/api/workers/{id}/sos` | | same as band `BTN:SOS`; `{ok, action: escalated \| ignored_active_incident}` |
| POST | `/api/alerts/{id}/ack` | | HEAT_RED = band button press; others marked seen |
| POST | `/api/mandown/{id}/resolve` | | closes a man-down or SOS incident (incident id or worker id) |
| POST | `/api/emergency/{id}/cancel` | | crew lead "cancel, false alarm" (D's manager) |
| POST | `/api/demo/threshold` | `{on}` | DemoState |
| POST | `/api/demo/replay` | `{on?, speed?}` | DemoState |
| POST | `/api/demo/scenario` | `{action: start\|stop}` | DemoState (start = restart the demo shift) |
| POST | `/api/demo/test-alert` | `{kind: LIGHTNING\|FLOOD_FIRE, site_id?}` | |
| POST | `/api/demo/clear-hazards` | | |
| POST | `/api/demo/mandown` | `{worker_id}` | fakes a fall (replay workers really tip over in the data) |
| POST | `/api/demo/sos` | `{worker_id}` | fakes an SOS |

### Live-system controls and plug-ins

| Method | Path | Body | What |
| --- | --- | --- | --- |
| GET | `/api/raw/state` | | hub snapshot (what `/ws` sends first) |
| GET | `/api/raw/workers/{id}/estimates` | `limit` | raw minute estimates |
| POST | `/api/replay` | `{on, speed?, source?: sim\|synthetic\|<file.jsonl>}` | replay on/off |
| POST | `/api/replay/speed` | `{speed}` | 409 if replay is off |
| POST | `/api/demo/hazard` | `{kind, on, site_id?, source?, details?}` | kinds: lightning, flood, fire, red_flag, fire_weather |
| POST | `/api/demo/fall`, `/api/demo/button` | `{worker_id}` | |
| POST | `/api/emergency/cancel` | `{worker_id}` | |
| POST | `/api/hazards` | same as `/api/demo/hazard` | for C1 if it runs as a separate process |
| POST | `/api/sites` | `{site_id, name, lat, lon, ...extra}` | add a site |
| POST | `/api/sites/{id}/status` | contract SiteStatus | C1's full hazard picture for the dashboard |
| POST | `/api/sites/{id}/weather` | `{wbgt, future_wbgt?[30]}` | forecaster input for every worker at the site |
| POST | `/spot-check` (or `/api/spot-check`) | `{worker_id, thermometer_c, timestamp?}` | personal offset twin, returns `offset_update` |
| POST | `/api/workers` | `{worker_id, name?, site_id?}` | create / move a worker (resets their pipeline) |
| POST | `/api/workers/{id}/context` | `{wbgt?, workload?, future_wbgt?[30], future_workload?[30]}` | planned workload (B) |
| POST | `/api/plans` | any JSON | stored + broadcast as `plan`; a Schedule-shaped one feeds `/api/schedule` |

---

## 3. `mandown_confirmed` and `sos` payload (session D)

Matches D's `MandownConfirmed` / `SosPressed` models (`backend/emergency/events.py`); extra keys are ignored by them.

```json
{
  "type": "mandown_confirmed", "worker_id": "demo-2", "worker_name": "Maria",
  "site_id": "eia-663", "site_name": "Deerhaven Generating Station", "lat": 29.76, "lon": -82.39,
  "trigger": "fall",                       // "fall" or "heat_red_unacked"
  "reason": "fall",                        // A's own name: fall | heat_red_unacknowledged | demo_fall
  "started_at": 1792237100.0,              // fall started (still since) or red alert check started
  "check_started": 1792237145.0,           // BUZZ:MANDOWN went out
  "confirmed_at": 1792237205.0, "timestamp": 1792237205.0,
  "seconds_since_start": 105.0,
  "last_movement_at": 1792237099.5,
  "core_temp_c": 38.61, "hr_bpm": 151.0, "skin_temp_c": 35.2,
  "location": {"lat": 29.76, "lon": -82.39},
  "last_vitals": {"core_temp": 38.61, "core_temp_sd": 0.21, "hr": 151.0, "skin_temp": 35.2,
                  "heat_level": "red", "estimate_at": 1792237200.0, "hr_at": 1792237204.0},
  "crewmates": ["demo-1", "demo-3", "demo-4"]   // everyone else at the site (dashboard alert)
}
```

`sos` has the same who/where/vitals keys plus `pressed_at` and `source`.

Wiring (in `main.py`, active when `backend/emergency/` is merged): every `mandown_confirmed` and `sos` event goes to `EscalationManager.handle()`; its `EmergencyStatus` updates are dispatched as `emergency_status` events (stored, broadcast, folded into the contract's `EmergencyCall`). The phone backend comes from `--emergency-phone dry_run|fake|live` (default `dry_run`). The decision to call is plain code.

---

## 4. Plugging in other sessions

**C1 (site hazards).** Nothing to do if C1's code is merged (`--hazards fake` uses C1's built-in demo data, no internet; `live` uses the real feeds; `off` skips C1). Then `backend/app/site_bridge.py` imports `backend.hazards.picture` / `backend.hazards.contract`, loads the 79 sites, makes `CREW_SITE_ID` (default `eia-663`) the default site (the stand-in `site-gnv` disappears), starts C1's 60 s background refresh, and every 15 s pushes each SiteStatus to the dashboard and turns band patterns on/off (`BUZZ:LIGHTNING` → lightning, `BUZZ:FLOOD_FIRE` → flood). C1's site-level `HEAT_YELLOW` doesn't go to bands: each worker's own heat alert owns that. `POST /api/demo/test-alert` calls C1's `test_alert()` (so the real 10-mile / 30-min rules run) and also turns the pattern on right away. From a separate process, use `POST /api/hazards` and `POST /api/sites/{id}/status` instead. WBGT for the forecaster: `hub.set_site_weather(site_id, wbgt, future_wbgt)` or `POST /api/sites/{id}/weather`.

**B (planner).** Post a plan to `POST /api/plans` (Schedule shape from the contract: `date, slot_minutes, day_start, rows[], notes[], checked_by_rules`) and the 6 PM briefing to `POST /api/briefing`. Planned workload per worker (W/m², now + next 30 min) improves the forecast a lot: `POST /api/workers/{id}/context` or `hub.set_context(worker_id, workload=..., future_workload=[...])`. Without it the forecaster's 90th percentile runs about 0.35 °C high (more false yellows). Read forecasts from `GET /api/raw/workers/{id}/estimates` or `hub.pipelines[id].estimates`. The planner never changes thresholds.

**D (emergency).** See §3.

**E (frontend).** `NEXT_PUBLIC_BACKEND_URL=http://localhost:8000` in `frontend/.env.local`. Answers to the contract's open questions: (1) yes, one `/ws/live` with `view` filtering; (2) yes, replay time everywhere + `clock` twice a second; (3) yes, photo data URLs in JSON, capped at ~1.5 MB; (4) A computes `minutes_to_danger` (first forecast minute whose 90th pct ≥ the danger line; 0 when red) and `status_reason`. `Estimate.why` is session H's model why-line once `ml/explain` is merged (it lags its minute by one: the next estimate carries it), else a simple heart-rate line. New event: `offset_update` (spot-check result, flat shape in §1; demo and own-worker views only). `on_break`/`break_at` are false/null until B's schedule is wired. `transcript` in `EmergencyCall` stays empty (D's status events don't carry it).

---

## 5. Band line protocol (implemented exactly, 115200 baud)

Follows `docs/firmware-contract.md`. Band → laptop: `TEMP:34.62` (every 5 s), `TILT:1,ACT:7` (every 1 s; ACT = tilt switch changes in the last second), `ACC:0.98,0.02,0.11` (later), `BTN:1` (short press) and `BTN:SOS` (held 3 s), both parsed into `ButtonPress` with `kind` `press` / `sos`. Lines starting with `#` are firmware debug prints and are ignored. Accepted extras: `HR:<bpm>` (the band's pulse sensor, tagged `band`, fused with the watch), `ID:<worker_id>` over UDP (which worker a band is). Laptop → band: `BUZZ:HEAT_YELLOW|HEAT_RED|LIGHTNING|FLOOD_FIRE|MANDOWN|OFF`, sent on change plus the 20 s keep-alive.

Every line is timestamped on the laptop when it arrives. UDP listener on `UDP_PORT` (5005) replies BUZZ lines to the band's address from the same port.

## 6. Storage (SQLite, `data/planit.db`)

Tables: `workers`, `sites`, `readings` (with `source`), `alerts` (every alert-type event, JSON payload), `plans`, `reports`, `offsets` (personal offset per worker), `spot_checks`, `estimates` (one row per worker per minute: inputs, `hr_watch`/`hr_band`/`hr_sources`, Kalman, estimate, forecast at +10/+20/+30 p10/p50/p90, heat level, model, twin version). Live inputs can also be recorded to `data/recordings/*.jsonl` (`--record`) and replayed with `--replay-file`.
