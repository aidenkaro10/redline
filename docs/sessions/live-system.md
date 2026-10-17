# Session A: live system, progress notes

Branch `claude/live-system-f3b5f1`. Interface: [docs/interfaces/live-system.md](../interfaces/live-system.md).

## Built (all tested, `pytest` green)

| Piece | File |
| --- | --- |
| Heat alerts: Kalman rule + spec forecast rule, hysteresis, demo thresholds, button ack, red timeout | `backend/alerts/heat.py` |
| Band router: one pattern, priority, only on change | `backend/alerts/router.py` |
| Band outputs: console, serial, UDP, fan-out | `backend/alerts/band_output.py` |
| Man-down (tilt rule + unacknowledged red trigger) | `backend/alerts/mandown.py` |
| Per-worker pipeline on reading timestamps | `backend/app/pipeline.py` |
| Hub (sources → pipelines → band / dashboard / SQLite / listeners) | `backend/app/hub.py` |
| FastAPI server, raw `/ws`, chart page | `backend/app/main.py`, `backend/app/static/index.html` |
| Frontend contract adapter (`/ws/live` + E's REST paths) | `backend/app/frontend_api.py` |
| C1 bridge (optional import) | `backend/app/site_bridge.py` |
| Replay: sim shifts, synthetic, JSONL; virtual clock; pre-roll; scripted fall; auto-ack | `backend/replay/` |
| Inputs: UDP listener, serial reader, Pulsoid client, fake band (UDP + pty) | `backend/ingest/` |
| SQLite + JSONL recorder | `backend/storage/` |

## Demo replay

`python -m backend.app.main --source replay --speed 10`: sim shift 9647 (Jake) plus 3 calm crewmates, starting at minute 116 (first 116 minutes pre-rolled instantly). At 10x: yellow after ~1 min, red after ~3.7 min (Jake presses the button 20 s later), Maria falls at 4 min → `BUZZ:MANDOWN` 4.5 s later → `mandown_confirmed` 6 s after that. True core temp (from the simulator) is drawn dashed on the chart. `--speed 30` for a faster run.

## Integration round (Oct 17, afternoon)

- Forecaster: `ens5.pt` (5-model ensemble) loads first, then `v1.pt`, then Kalman-only. `backend/app/model_runner.predict_at` runs it for the newest minute only (0.11 s vs 0.41 s late in a shift); a test checks it equals `predict_signals` for both checkpoints. With ens5 the demo story is unchanged: Jake yellow at +9 replay min, red at +38, crewmates calm, pre-roll ~6 s.
- Personal offset twin wired (`POST /spot-check`, SQLite, `offset_update`).
- Why-line adapter (`backend/app/why.py`), C1 bridge with fake/live/off, D bridge: all import their modules only when merged.
- Checked in a throwaway merge of this branch + C1 + D + H (not committed, removed): 234 tests pass, and the replay demo runs end to end with 79 sites, C1 fake hazards, H's why-lines, the D chain through "Demo: would call 911", test lightning reaching the crew's bands by router priority, and a spot check. Merge needs both sides kept in `backend/config.py` and `.env.example` (append-only conflicts).

## Findings for the ML session

1. **The forecaster needs its "known future" inputs.** Without the next 30 minutes of planned workload and WBGT, the 20-min 90th percentile runs about 0.35 °C higher, and yellow fires on nearly every simulated shift (even workers whose true core temp never passes 37.8). The pipeline now passes 30 rows of known future to `predict_signals` (rows after the current minute, only WBGT/workload filled). Live, that needs the planner (workload) and C1 (forecast WBGT). Worth scoring the "no future inputs" case in the harness, since that's the live default until B is wired.
2. **Early-shift false alarms.** In the first ~5-20 minutes of a shift, the 20-min p90 crosses 38.0 for most sim shifts, even with full inputs. The replay skips this with pre-roll. Probably worth a warm-up fix in the model or calibration.

## Not done / open

- Accelerometer man-down rule (Grove part not here yet) and the ML man-down classifier.
- Firmware contract (Oct 17) implemented: `BTN:SOS` → `ButtonPress(kind="sos")` → `sos` event; `#` lines ignored; 20 s keep-alive. Open for the hardware team: a repeated identical command must not restart beeping that a short press silenced.
- `Estimate.why` is a simple heart-rate sentence until Phase 5 explainability exists.
- Not tested against the real Next.js app (running a second `next dev` in E's folder would collide with theirs); tested against the contract shapes instead.
- Not tested against the real band or Pulsoid (no hardware/token here). Fake band over a pty serial port and UDP both work end to end.
