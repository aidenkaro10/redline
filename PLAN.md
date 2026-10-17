# Software build plan

Everything except hardware and firmware. Spec: [PROJECT.md](PROJECT.md).

Build top to bottom. Each step ends with tests passing and a commit. Don't start a milestone until the one above it works (stretch waits for 1 to 8).

## How the pieces connect

```text
SOURCES                 PIPELINE (one per worker)                OUTPUTS
fake data  ─┐                                                ┌─> band commands (console / serial / WiFi)
replay     ─┤                                                │
serial     ─┼─> Reading ─> merge latest ─> 1-min averages ─> Kalman ─> forecaster ─> alert router ─┼─> dashboard WebSocket
WiFi       ─┤   (data contract)                                    ▲                │
Pulsoid HR ─┘                                     NWS + WBGT ──────┘                └─> SQLite
```

Design choices that make everything testable without hardware:

- **One format in.** Every source turns its data into the same `Reading` (the data contract). The pipeline never knows if data is real or fake.
- **Pipeline runs on reading timestamps, not the wall clock.** That's how replay runs at 10x with zero special code downstream. Live sources stamp readings with laptop time on arrival; replay stamps them with a sped-up clock.
- **Swappable outputs.** Band commands go to a console printer, the real serial port, or WiFi. Same code either way.
- **Fake band simulator.** A small script that speaks the exact line protocol (`TEMP:`, `TILT:`, `BTN:` out, `BUZZ:` in), so serial and WiFi code get tested before the Feather is ready.
- **Fake crewmates.** 2 or 3 synthetic workers run next to the real one, so the crew dashboard looks like a real crew.

## Milestone 1: fake data end to end (hours 0 to 3)

1. `backend/config.py`: every threshold, Kalman parameter and timing as a named constant. Demo threshold 37.5 °C.
2. `backend/app/schemas.py`: the data contract and the line protocol (parse band lines, format `BUZZ:` commands).
3. Fake data generator: heart rate every 5 s (like the Apple Watch), rest then hard work. Averaged to 1-minute values.
4. `backend/models/kalman.py`: ECTemp, exact equations and parameters.
5. Heat alert on the Kalman estimate: yellow at 38.0, red at 38.5, demo threshold toggle. `BUZZ:` printed to console.
6. `backend/replay/replay.py`: play fake or recorded data as if live, with a speed factor.
7. `backend/app/main.py`: FastAPI + WebSocket + one bare chart page (core temp + heart rate).

**Done when:** one command starts everything on fake data at 10x, the chart climbs, and the console prints `BUZZ:HEAT_YELLOW` then `BUZZ:HEAT_RED`.

## Milestone 2: real inputs (hours 3 to 10)

1. `ingest/pulsoid_client.py`: live heart rate from Pulsoid's WebSocket. Reconnects on its own if the phone drops.
2. Fake band simulator (see above).
3. `ingest/serial_reader.py`: reads band lines at 115200 baud, sends `BUZZ:` commands back. Survives unplug and replug.
4. `ingest/wifi_listener.py`: same lines over WiFi (UDP or WebSocket, whichever the hardware team picks).
5. Recorder: saves any live session to `data/recordings/` so replay can play it back.
6. `storage/db.py`: SQLite with readings, workers, alerts, plans.
7. Button `BTN:1` acknowledges a red alert.

**Done when:** a teammate wears the watch and band, the chart shows live heart rate and core temp, the button acknowledges an alert, and the session replays from file.

## Milestone 3: hazard alerts (hours 10 to 16)

1. `hazards/nws.py`: active alerts at the job site and the hourly forecast. Sends a User-Agent, refreshes every 5 min, keeps the last result if the WiFi dies.
2. Map NWS events to band patterns: thunderstorm warnings → `LIGHTNING`, flood and red flag (fire weather) → `FLOOD_FIRE`. Heat advisories show on the dashboard.
3. `hazards/wbgt.py`: WBGT from the hourly forecast with the Liljegren method, using an existing open-source implementation. Tested against published example values.
4. `alerts/router.py`: the one place that decides what the band shows when several things are active.
   - Priority: `MANDOWN` > `HEAT_RED` > `LIGHTNING` > `HEAT_YELLOW` > `FLOOD_FIRE`.
   - Sends a command only when the state changes.
   - A small buffer below each threshold so alerts don't flicker on and off.
5. Demo button: inject a fake thunderstorm alert.

## Milestone 4: man-down (hours 10 to 16)

1. `alerts/mandown.py`, tilt version: band tips over, then zero activity for 30 to 60 s (configurable) → `BUZZ:MANDOWN`.
2. Button press or any movement cancels it.
3. Not cancelled within 60 s → crew alert with name, location, time since fall, last vitals.
4. Also triggers on a red heat alert that isn't acknowledged within a minute.
5. Accelerometer version once we get the Grove part: sharp spike, then near-zero movement.
6. Tests with scripted fake falls, fake sits and fake normal movement.

## Milestone 5: real frontend

Next.js + Tailwind + Recharts + Leaflet, fed by the same WebSocket.

1. Crew lead dashboard: a card per worker (green, yellow, red), hazard panel, job site map, today's schedule.
2. Worker view (phone-sized): giant status color, current hazard, break countdown.
3. Man-down screen: full-screen on every open page.
4. Demo controls, one click each: test NWS alert, replay on/off, replay speed, demo threshold.
5. Demo view for judges: live heart rate, skin temp, core temp with the forecast band shaded.

**Fallback:** if Next.js is fighting us by hour 12, switch to Streamlit.

## Milestone 6: synthetic training data (`ml/simulate.py`)

1. **One-hour test first:** check that pythermalcomfort's Predicted Heat Strain model can step minute by minute while work and weather change. If not, use its two-node model or a simple model of our own.
2. Simulated people with an ID each: random body size, fitness, acclimatization, clothing.
3. Shifts: task schedules (rest, light, moderate, heavy, breaks) under real Gainesville summer weather.
4. Heart rate model: resting HR + workload + core temp rise + noise. On purpose this is a different curve from the Kalman's, so the baseline isn't handed the answer.
5. Output: 1-minute rows with every model input, true core temp and sweat loss.
6. Sanity plots: core temps look like published field data.

## Milestone 7: forecaster and evaluation

1. Dataset builder: 60-minute history windows, targets at +10, +20, +30 min. Train/val/test split **by person**.
2. `backend/models/forecaster.py`: seq2seq GRU, 2 layers, 64 hidden. Decoder gets future WBGT and planned workload. Outputs 10th, 50th and 90th percentile at each horizon, kept in order (the 90th can never fall below the 50th).
3. `ml/train.py`: pinball loss, CPU, saves a checkpoint, logs each run.
4. `ml/evaluate.py`, always next to the Kalman baseline:
   - Estimation: RMSE, bias, Bland-Altman limits.
   - Alerts: share of dangerous episodes caught, average warning lead time, false alarms per hour.
   - Plots of predicted vs real core temp on held-out people.
   - Forecast baseline = "Kalman now, assumed flat". A warning if we beat published research by a lot.
5. Real data: find the public datasets (Mendeley firefighter, Abertay), check license and format, fine-tune and test on them.
6. Live: forecaster runs each minute. Heat alerts switch to the spec rule (20-min 90th percentile ≥ 38.0 → yellow; current or 10-min ≥ 38.5 → red).
7. Dashboard shades the forecast band.

## Milestone 8: skin temperature input

1. Add skin temp as an input. Train with it randomly missing so the model still works if the diode fails.
2. Handle the offset (a diode under a strap reads differently from true skin).
3. Report with vs without skin temp, next to Kalman.

## Milestone 9: freeze and demo prep (hours 30 to 36)

1. Record real exercise sessions for replay.
2. Run the 3-minute demo end to end, including the switch to replay mid-demo.
3. Export results figures for the slides from `evaluate.py`.
4. Final README run steps.

## Stretch (only after 1 to 8 all work)

1. **Sweat:** whole-body sweat loss estimate from the heat strain model plus a general drink-water message (no sensor needed). The measured-vs-predicted part waits for a DHT22.
2. **Personal twins:** worker embedding (8 to 16 numbers), fine-tuned after each shift on next-10-min heart rate. Acclimatization state. Simulated two weeks showing error dropping. All guardrails from the spec.
3. **Planning agent:** OR-Tools CP-SAT schedules tasks and breaks in 30-min slots. Claude API with tool use for chat and a morning briefing. The LLM never makes safety calls.
4. **Residual correction network:** second small GRU with the three-part loss from the spec.

## Decisions needed (with my pick)

1. **Bare chart in milestone 1:** plain HTML + Chart.js served by FastAPI. Pick: yes.
2. **Heart rate source:** follow the protocol table (Pulsoid only, not the band), even though the architecture section shows `HR:82` from the band. Pick: table.
3. **Kalman start + early alerts:** uncertainty starts at 0 (Buller 2013); before the forecaster, yellow 38.0 and red 38.5 on the current estimate. Pick: yes.
4. **Privacy conflict:** the spec says crew leads see a core temp chart, and also that they never see raw biometrics. Pick: crew lead sees status color + "minutes until predicted danger" only. Charts live in the worker's own view and a labeled demo view.
5. **WiFi transport:** UDP or WebSocket, ask the hardware team. Pick: UDP (simplest on the ESP32).
6. **Weather history for synthetic data:** Open-Meteo's free historical API (no key). Pick: yes.
7. **Run tracking:** Weights & Biases needs an account. Pick: local CSV logs, W&B optional.
8. **Worker location for man-down:** the band has no GPS. Pick: fixed positions on the job site map for the demo.

## Risks to check early

- **Hackathon rules.** Many hackathons don't allow code written before the event, or require you to disclose it. Check SwampHacks rules before we build a lot ahead of time.
- **Python 3.14** is new. torch or pythermalcomfort might not install on it yet. We may need a 3.12 environment for the ML part.
- **Pulsoid free plan** may not include API access (open question in the spec).
- **Public datasets** might not be downloadable or might not have minute-level heart rate.
- **WBGT needs sun strength.** NWS only gives cloud cover, so we estimate it.
- **ECTemp is patented.** Fine for a hackathon.
