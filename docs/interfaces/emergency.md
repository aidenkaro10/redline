# Emergency escalation interface (session D, `backend/emergency/`)

Confirmed man-down **or SOS** → call crew lead + safety officer → cancel window → emergency call.
The decision to call is plain code. The prototype **never dials 911**: the emergency leg calls
`EMERGENCY_TEST_NUMBER` (a teammate) and every status for it carries `demo_label: "Demo: would call 911"`.

## Input: `mandown_confirmed` (session A sends this)

Draft, session A please confirm or change. Model: `backend/emergency/events.py::MandownConfirmed`.
Send it when the man-down check was **not** cancelled within `MANDOWN_ESCALATE_S`.

```json
{
  "type": "mandown_confirmed",
  "worker_id": "worker-1",
  "worker_name": "Alex Rivera",          // optional, said as "worker ID worker-1" if missing
  "site_id": "deerhaven",                // optional
  "site_name": "Deerhaven Generating Station",  // optional
  "lat": 29.7581, "lon": -82.3898,       // optional, from the site (session C1) or the band
  "trigger": "fall",                     // "fall" or "heat_red_unacked" ("sos" is set internally, see below)
  "started_at": 1760000000.0,            // when the fall / red alert began, laptop Unix time
  "confirmed_at": 1760000105.0,          // when the cancel time ran out
  "last_movement_at": 1760000000.0,      // optional, last movement the band saw
  "core_temp_c": 39.1,                   // optional, latest Kalman/forecaster estimate
  "hr_bpm": 142,                         // optional
  "skin_temp_c": 35.2                    // optional
}
```
Missing optional fields are said as "Unknown" on the call, never guessed.

## Input: `sos` (session A sends this when the band sends `BTN:SOS`, button held 3 s)

Model: `SosPressed`. Escalates **right away**: no stillness wait. Then the same chain: crew lead +
safety officer → same cancel window → same test-number call (never 911, allowlist, dry-run by default).
The call says "pressed SOS on their band" instead of "collapsed". Pressing the button counts as the
last movement.

```json
{
  "type": "sos",
  "worker_id": "worker-1",
  "worker_name": "Alex Rivera",          // optional
  "site_id": "deerhaven",                // optional
  "site_name": "Deerhaven Generating Station",  // optional
  "lat": 29.7581, "lon": -82.3898,       // optional
  "pressed_at": 1760000000.0,            // when the 3 s hold finished, laptop Unix time
  "core_temp_c": 38.4,                   // optional
  "hr_bpm": 128,                         // optional
  "skin_temp_c": 35.0                    // optional
}
```
`manager.handle()` takes either dict and picks the model from `"type"`. An SOS for a worker who
already has an active incident (e.g. a man-down) is ignored, so nobody gets called twice.

## How session A wires it in

```python
from backend.emergency.escalation import EscalationManager
from backend.emergency.voice_agent import make_phone

manager = EscalationManager(phone=make_phone("dry_run"),     # "fake" for demos, "live" once approved
                            emit=lambda s: broadcast(s.model_dump()))
asyncio.create_task(manager.run())         # ticks every 0.5 s, fires the emergency call on time

manager.handle(event_dict)                 # on mandown_confirmed or sos
manager.cancel(worker_id="worker-1")       # crew lead pressed "cancel, false alarm" (returns False if too late)
```
Suggested routes (A owns them): `POST /api/emergency/cancel {"worker_id"}` and the WebSocket broadcast of status events.
A second `mandown_confirmed` for a worker who already has an active incident is ignored (no double calls).

## Output: `emergency_status` (session E shows this)

Model: `EmergencyStatus`. One event every time anything changes.

| field | meaning |
| --- | --- |
| `incident_id` | `inc-<worker_id>-<confirmed_at>` |
| `worker_id`, `worker_name` | who |
| `timestamp` | Unix time of this update |
| `stage` | `notify` → `cancel_window` → `emergency_call` → `done` |
| `status` | `calling`, `connected`, `completed`, `failed`, `cancelled`, `waiting` (cancel window open) |
| `target` | `crew_lead`, `safety_officer`, `emergency`, or `null` for incident-level updates |
| `number_masked` | `+1 ••• ••• 1234` |
| `mode` | `dry_run` (nothing dialed), `fake` (pretend phone), `live` (a real phone rang) |
| `demo_label` | `"Demo: would call 911"` on every emergency-leg event, else `null` |
| `cancel_deadline` | Unix time the cancel window closes (count down from this) |
| `message` | short text for the screen |

Typical sequence: `notify/calling` (incident) → per crew target `calling` → `connected` → `completed` →
`cancel_window/waiting` → emergency `calling` → `connected` → `completed` → `done/completed`.
Cancelled: `done/cancelled`. If the emergency call fails: `done/failed` with message
"Emergency call FAILED. Call 911 yourself now." Show that one big.

Suggested UI: cancel button visible while `stage` is `notify` or `cancel_window`.

## Config (end of `backend/config.py`, keys in `.env.example`)

| name | default | what |
| --- | --- | --- |
| `EMERGENCY_CANCEL_WINDOW_S` | 30 | cancel window |
| `EMERGENCY_DEMO_CANCEL_WINDOW_S` | 10 | shorter window for live demos |
| `EMERGENCY_BLOCKED_NUMBERS` | 911, 112, 999, 000, 111, 110, 119, 988, 933 | always refused |
| `EMERGENCY_MIN_DIGITS` | 10 | shorter numbers refused |
| `EMERGENCY_LIVE_CALLS` (.env) | 0 | 1 = real calls allowed |
| `CREW_LEAD_NUMBER`, `SAFETY_OFFICER_NUMBER`, `EMERGENCY_TEST_NUMBER` (.env) | empty | the allowlist, the only numbers ever called |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID` (.env) | empty | live calls only |
| `EMERGENCY_LOG_PATH` (.env) | `data/emergency_log.jsonl` | one JSON line per step |
