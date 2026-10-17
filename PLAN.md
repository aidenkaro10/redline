# Software build plan

Everything except hardware and firmware (friends own the band). Spec: [PROJECT.md](PROJECT.md).

The ML is the main event. The plumbing is one fast phase, because nothing runs without it. Each step ends with tests passing and a commit.

## The pitch this plan builds toward

"Our band doesn't just estimate your core temperature. It forecasts it 30 minutes out with a calibrated uncertainty range, explains why, learns each worker's body every shift, and an AI planner reschedules the crew before anyone gets hurt. Here's every model next to the Army's baseline on real people it's never seen."

## Locked decisions

| Area | Decision | Why |
| --- | --- | --- |
| Storage | SQLite, no Supabase | One file, no account, works if venue WiFi dies |
| Server | One Python process: FastAPI + background tasks + one queue | Fewest moving parts |
| Time | Pipeline runs on reading timestamps | Replay at any speed for free |
| Features | One shared feature function for training and live | Prevents train/live mismatch, the most common silent ML bug |
| Model | One multi-task net (now + 30-min forecast + next-10-min HR) | Replaces the separate residual net; HR output powers the twins |
| Target | Predict the change from the Kalman estimate | Starts from the Army's physics, learns what it misses |
| Uncertainty | 5-model ensemble + conformal calibration on held-out real people | Honest ranges; replaces the Transformer challenger |
| Live learning | Twins adapt on heart rate only | True core temp is never observed live |
| Twin method | Per-person embeddings, then CAVIA-style "practice adapting" training | Matches exactly what happens live |
| Results | Headline numbers on real data only, split by person | Simulator results are for development |
| Agent | LLM keeps notes on preferences, never safety | LLM never makes safety calls |

## How it fits together

```text
SOURCES                 PIPELINE (per worker, every minute)                         OUTPUTS
fake / replay  ─┐                                                                ┌─> band (BUZZ:...)
serial / WiFi  ─┼─> Reading ─> 1-min features ─> Kalman ─> multi-task net ─> calibrate ─> alerts ─┼─> dashboard (WebSocket)
Pulsoid HR     ─┘                                              ▲    (ensemble)                     └─> SQLite
                                   NWS + WBGT ─────────────────┤
                                   worker twin (16 numbers) ───┘
                     after each shift: twin learning loop (HR-based, gated)
                     planning agent: reads forecasts, optimizes, verifies, proposes
```

---

## Phase 0: foundation (fast)

1. `backend/config.py`: every threshold and parameter as a named constant. **Done.**
2. `backend/app/schemas.py`: data contract + line protocol. **Done.**
3. `backend/models/kalman.py`: ECTemp, exact spec, checked against the patent. **Done.**
4. Fake shift generator, shared 1-minute feature builder.
5. Heat alert on the Kalman estimate, band commands to console.
6. Per-worker pipeline.
7. Replay mode with speed factor.
8. FastAPI + WebSocket + bare chart page.
9. Pulsoid client, USB serial reader, UDP listener, fake band simulator, recorder, SQLite.

SQLite tables: `readings`, `workers`, `alerts`, `plans` (spec), plus `estimates` (one row per worker per minute: features, Kalman, forecast, twin version). The twins learn from `estimates`.

**Done when:** fake data at 10x climbs on the chart and prints `BUZZ:HEAT_RED`, and real heart rate from the watch flows through the same path.

---

## Phase 1: data

### 1a. Public datasets (Aiden found some, more requested)

1. One loader per dataset, all converted to the same 1-minute table: person ID, minute, HR, skin temp (if any), activity (if any), weather (if any), **measured core temp**.
2. Check each license and note it.
3. Split by person: fine-tune set, calibration set, test set. The test people are never touched until final results.

### 1b. Physiology simulator (`ml/simulate.py`)

1. **Heat strain engine:** pythermalcomfort's Predicted Heat Strain (ISO 7933) and two-node models, stepped minute by minute while work and weather change. One-hour test first to confirm state carries between steps.
2. **Virtual workforce:** thousands of people, each with an ID and randomized body size, fitness, resting HR, acclimatization, clothing, sweat rate.
3. **Shifts:** task schedules (rest, light, moderate, heavy, breaks) under real Gainesville summer weather (Open-Meteo historical API).
4. **Heart rate model:** resting HR + workload + core temp rise + cardiac drift + noise. A different curve from the Kalman's, so the baseline isn't handed the answer.
5. **Sensor realism:** Apple Watch 5-second gaps and dropouts, diode offset and drift, tilt flicker.
6. **Multi-week histories** per person with acclimatization rising and decaying. Feeds the twins.
7. Sanity plots against published field ranges and against the real datasets.

## Phase 2: evaluation harness (built before the model)

One command scores any model, always next to the baselines.

1. Automatic check that no person is in both train and test.
2. Estimation: RMSE, bias, Bland-Altman limits of agreement.
3. Forecasts: pinball loss, CRPS, coverage of the 90% range.
4. Alerts: share of dangerous episodes caught, warning lead time, false alarms per hour, full lead-time vs false-alarm curve.
5. Baselines: Kalman (ECTemp), "Kalman now, assumed flat", Kalman + linear trend.
6. Per-person breakdown.
7. Simulator results and real results reported separately.
8. Auto-flag anything better than published research. Usually a bug.
9. Exports slide-ready figures.

## Phase 3: the multi-task model

```text
last 60 min ──> [encoder GRU, 2 x 64] ──> shared memory ──┬─> NOW head: core temp now (mean + variance)
 HR, HR trend, skin temp + "missing" flag,                ├─> FORECAST: [decoder GRU, 2 x 64] ─> +1..+30 min, 10/50/90th pct
 activity, WBGT, Kalman temp + variance,                  └─> HR head: next 10 min of heart rate
 time into shift, acclimatization
                    + worker twin (16 numbers) fed into every step
                    + decoder gets future WBGT and planned workload
```

1. **Targets are the change from the current Kalman estimate.**
2. **NOW head loss** (the spec's residual-net loss): Gaussian negative log-likelihood + asymmetric safety penalty (underestimating punished harder, heavier above 38 °C) + smoothness penalty (core temp can't jump).
3. **FORECAST head loss:** pinball loss at 10th, 50th, 90th percentiles. The net predicts the median plus positive gaps, so the percentiles can never cross. Read out +10, +20, +30.
4. **HR head loss:** mean squared error. This is what lets the twins learn live.
5. **Skin temp randomly hidden** in training with a "missing" flag, so the model works without the diode.
6. **Training:** pretrain on the simulator, fine-tune on real data. AdamW, early stopping on held-out people, gradient clipping. CPU.
7. **Ensemble:** train 5 copies with different random starts. Average them; their disagreement adds to the uncertainty.
8. **Small random search** over history length, hidden size, learning rate, loss weights. Every run logged.
9. **Live:** runs every minute. Heat alerts switch to the spec rule (20-min 90th pct ≥ 38.0 → yellow; current or 10-min ≥ 38.5 → red).

## Phase 4: calibrated uncertainty

1. **Conformal calibration** on held-out real people: measure how often the truth lands above the 90th percentile, then widen the range per horizon until it's really 90%. Hand-written, no new library.
2. Same for the NOW head's range.
3. Coverage reported in the harness and shown on the results page.
4. Live, the range shown is ensemble + conformal. We never recalibrate core temp live (no truth to check against).

## Phase 5: explainability

1. For each forecast, find which inputs pushed it up (integrated gradients, hand-written in torch).
2. One line on the dashboard: "Rising because heart rate jumped 18 bpm in 10 min and WBGT is 31 °C."

## Phase 6: self-improving twins

The trick: we can't observe core temp live, but we can observe heart rate.

1. **Pretrain with fingerprints:** every virtual person gets a 16-number twin, learned alongside the network. The HR head forces the fingerprint to capture things like fitness and heat tolerance.
2. **Practice adapting (CAVIA-style):** during training, repeatedly: take a person, reset their fingerprint to average, adjust only the fingerprint on their first hour of HR, then score core temp on the rest. The network learns fingerprints that are quick and safe to adapt.
3. **New worker** starts at the population average.
4. **After each shift:** freeze the network, adjust only that worker's fingerprint on their shift's HR. Loss = HR error + β · distance from average (spec formula).
5. **Gate:** keep the update only if it predicts the last part of the shift better. Otherwise roll back. Updates capped. Every version logged.
6. **Acclimatization score:** rises with daily heat exposure, fades on days off. Model input.
7. **The proof (simulator, where truth is known):** show that adapting on HR alone lowers core temp forecast error. Then check on real data. If it fails, we know early.
8. **Guardrails, hard-coded:** safety thresholds never learned, updates capped, uncertain predictions lean cautious, ignored alerts never teach the system someone "can handle more."
9. **Demo:** two simulated weeks for one worker, with a chart of their error dropping day by day.

## Phase 7: planning agent

Self-improving agent design waits for the Stanford course material. Base version:

1. **Precompute** with the twins: how much heat each task adds for each worker in each 30-min slot.
2. **Optimizer** (OR-Tools CP-SAT): schedule tasks and breaks under each worker's heat budget, avoiding NWS hazard windows.
3. **Verify** the schedule with the full model. If anyone's 90th percentile crosses the limit, block it and solve again.
4. **Claude agent** with tools (`get_forecast`, `get_crew_status`, `run_optimizer`, `simulate_schedule`): chat, morning briefing, what-if questions.
5. **Learns over time:** logs planned vs actual strain; keeps notes on non-safety preferences.
6. **Hard line:** alerts never go through the LLM. It can't change limits. Every schedule must pass the optimizer and the full-model check.

## Phase 8: hazards and man-down

1. `hazards/nws.py`: active alerts + hourly forecast at the job site, cached, survives WiFi drops.
2. `hazards/wbgt.py`: Liljegren WBGT from the forecast (also a model input).
3. `alerts/router.py`: priority `MANDOWN` > `HEAT_RED` > `LIGHTNING` > `HEAT_YELLOW` > `FLOOD_FIRE`, sends only on change, no flicker.
4. `alerts/mandown.py`: tilt rule, accelerometer rule when the Grove part arrives, unacknowledged red alert trigger.
5. Man-down classifier on accelerometer windows (falls, sits, normal movement recorded by teammates).

## Phase 9: frontend (Next.js + Tailwind + Recharts + Leaflet)

1. Crew lead dashboard: worker cards (green, yellow, red), minutes until predicted danger, hazard map, planner's schedule.
2. Forecast chart with the shaded uncertainty range and the "why" line.
3. Learning panel: each worker's twin improving.
4. Agent panel: chat + morning briefing.
5. Worker view (phone): giant status color, current hazard, break countdown.
6. Man-down screen: full-screen on every page.
7. Demo controls, one click: test NWS alert, replay on/off, replay speed, demo threshold.
8. Results page: harness report next to Kalman.

Privacy: crew lead sees status and minutes until danger; raw charts live in the worker view and a labeled demo view.

## Phase 10: sweat (needs a DHT22 for the full version)

1. Sweat loss estimate from the heat strain model + general drink-water message.
2. Measured vs predicted sweat in the twin, once a sensor exists.

## Phase 11: freeze and demo

1. Record real exercise sessions for replay.
2. Rehearse the 3-minute demo, including switching to replay mid-demo.
3. Final figures, README run steps.

---

## Cut line (drop from the bottom if time runs out)

Sweat, man-down classifier, explainability, CAVIA training (keep plain twins), agent chat (keep the optimizer). Never cut: foundation, simulator, harness, multi-task model, calibration, twins demo, live demo path.

## Waiting on

- Aiden: dataset names/links (found + requested).
- Aiden: Stanford self-improving agents course material.
- Hardware team: UDP on port 5005? Button stops the repeat buzz on its own and still sends `BTN:1`? Diode calibration on the band?

## Risks

- **Hackathon rules** on pre-built code. Check SwampHacks rules.
- **Python 3.14** may need a 3.12 environment for torch or pythermalcomfort.
- **Pulsoid free plan** may not include API access.
- **Real data size:** small datasets limit fine-tuning and calibration.
- **WBGT needs sun strength.** NWS only gives cloud cover, so it's estimated.
- **ECTemp is patented.** Fine for a hackathon.
