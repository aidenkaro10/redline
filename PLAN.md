# Software build plan

Everything except hardware and firmware. Spec: [PROJECT.md](PROJECT.md).

The ML is the main event. The plumbing is one fast milestone, because nothing else can run without it. Each step ends with tests passing and a commit.

## The pitch this plan builds toward

"Our band doesn't just estimate your core temperature. It forecasts it 30 minutes out, with an uncertainty band that's mathematically calibrated, explains why, learns each worker's body over time, and an AI planner reschedules the crew before anyone gets hurt. And here's every model next to the Army's baseline on people it's never seen."

## How it fits together

```text
SOURCES                 PIPELINE (per worker)                                     OUTPUTS
fake / replay  ─┐                                                              ┌─> band (BUZZ:...)
serial / WiFi  ─┼─> Reading ─> 1-min features ─> Kalman ─┬─> forecaster ──┐    ├─> dashboard (WebSocket)
Pulsoid HR     ─┘                                        └─> residual net ─┴─> calibrated ─> alert router ─┼─> SQLite
                          NWS + WBGT ─────────────────────────────▲      (conformal)                    │
                          personal twin embedding ────────────────┘                                  ▼
                                                              after each shift: learning loop updates the twin
                                                              planning agent reads forecasts, proposes schedules
```

Rules that keep it all testable: one data format in, the pipeline runs on reading timestamps (so replay at 10x just works), outputs are swappable (console, serial, WiFi), and a fake band simulator speaks the exact line protocol.

---

## Phase 0: foundation (fast, a few hours)

1. `backend/config.py`: every threshold, Kalman parameter and timing as a named constant.
2. `backend/app/schemas.py`: data contract + line protocol.
3. Fake data generator, 1-minute feature builder.
4. `backend/models/kalman.py`: ECTemp, exact spec.
5. Heat alert on the Kalman estimate, `BUZZ:` printed to console.
6. Replay mode with speed factor.
7. FastAPI + WebSocket + bare chart page.
8. Pulsoid client, serial reader, WiFi listener, fake band simulator, recorder, SQLite.

**Done when:** fake data at 10x climbs on the chart and prints `BUZZ:HEAT_RED`, and real heart rate from the watch flows through the same path.

---

## Phase 1: the physiology simulator (the data engine)

Every model trains on this, so it gets real effort.

1. **Heat strain engine:** pythermalcomfort's Predicted Heat Strain (ISO 7933) and two-node models, stepped minute by minute while work and weather change. One-hour test first to confirm the library can carry state between steps.
2. **Virtual workforce:** thousands of simulated people, each with an ID and randomized body size, fitness, resting heart rate, acclimatization, clothing, and sweat rate.
3. **Realistic shifts:** task schedules (rest, light, moderate, heavy, breaks) under real Gainesville summer weather from Open-Meteo's historical API.
4. **Heart rate model:** resting HR + workload + core temp rise + fatigue drift + noise. On purpose a different curve from the Kalman's, so the baseline isn't secretly handed the answer.
5. **Sensor realism:** Apple Watch 5-second gaps and dropouts, diode offset and drift, tilt switch flicker. Models trained on clean data break on real data.
6. **Multi-day histories:** the same virtual person across weeks, with acclimatization rising and decaying. This feeds the personal twins.
7. Sanity plots against published field ranges.

## Phase 2: the evaluation harness (built before the models)

One command scores any model. Every model gets measured from day one.

1. Splits **by person** with an automatic check that no person appears in both train and test.
2. Estimation: RMSE, bias, Bland-Altman limits of agreement.
3. Forecasts: pinball loss, CRPS, and **coverage** (does the 90% band really contain the truth 90% of the time?).
4. Alerts: share of dangerous episodes caught, average warning lead time, false alarms per hour, and the full lead-time vs false-alarm curve.
5. Baselines in every report: Kalman (ECTemp), "Kalman now, assumed flat", Kalman + linear trend.
6. Per-person breakdown, so we see who the model fails on.
7. Auto-flag results better than published research. It's usually a bug.
8. Exports slide-ready figures.

## Phase 3: the forecaster

1. **Main model (spec):** seq2seq GRU, 2 layers, 64 hidden. 60-minute history in. The decoder gets known future inputs (forecast WBGT, planned workload). Out: 10th, 50th and 90th percentile at +10, +20 and +30 min, with pinball loss. Quantiles built so they can never cross.
2. **Challenger:** a small Transformer encoder-decoder on the same data. Keep whichever wins in the harness, or ensemble both.
3. **Hyperparameter search:** a random search over size, history length and learning rate. Every run logged.
4. **Sim-to-real:** pretrain on synthetic data, fine-tune on public datasets with measured core temp (Mendeley firefighter, Abertay heat acclimation), test on real people the model never saw.
5. **Skin temperature as an input,** trained with it randomly missing so the model still works if the diode fails. Reported with vs without.
6. Live: runs every minute. Heat alerts switch to the spec rule (20-min 90th percentile ≥ 38.0 → yellow, current or 10-min ≥ 38.5 → red).

## Phase 4: calibrated uncertainty

This is what makes "the 90th percentile" honest instead of a guess.

1. **Conformal prediction:** on held-out people, measure how wrong the bands are, and widen or tighten them so 90% really means 90%. Built by hand, no new library.
2. **Online adaptive conformal:** during a shift, each worker's band recalibrates live as new heart rate comes in and errors show up.
3. Coverage shown in the harness and on the dashboard.

## Phase 5: residual correction network (hybrid physics + ML)

A second small GRU that corrects the Kalman estimate right now using everything else (skin temp, activity, WBGT, the twin). Its loss has three parts, as in the spec:

1. Gaussian negative log-likelihood, so it reports honest uncertainty.
2. Asymmetric safety penalty: underestimating is punished harder, heavier above 38 °C.
3. Smoothness penalty: core temp can't jump faster than physiology allows.

Pitch: physics where physics is solid, ML where it isn't.

## Phase 6: explainability

1. For each forecast, compute which inputs drove it (integrated gradients, hand-written in torch).
2. Turn it into one line on the dashboard: "Rising because heart rate jumped 18 bpm in 10 min and WBGT is 31 °C."

## Phase 7: the self-improving system

The system gets better at each worker every shift, with guardrails.

1. **Personal twins:** each worker has a learned embedding (8 to 16 numbers) fed into the forecaster and residual net. New workers start at the population average.
2. **Self-supervised learning:** after each shift, fine-tune only that worker's embedding on predicting their next 10 minutes of heart rate, which we can observe without a core temp sensor. Loss = MSE + β·distance from the population average.
3. **Acclimatization state:** rises with daily heat exposure, decays on days off.
4. **Learning loop with a gate:** ingest shift, then update the twin, then re-score on held-out minutes. The update is kept only if error didn't get worse, otherwise rolled back. Every update is logged.
5. **Guardrails, hard-coded:** safety thresholds never learned, updates capped, uncertain predictions lean cautious, ignored alerts never teach the system someone "can handle more."
6. **Demo:** two weeks of simulated history for a worker, with a chart of their forecast error dropping day by day.

## Phase 8: the planning agent

1. **Optimizer:** OR-Tools CP-SAT schedules tasks and breaks in 30-minute slots, keeping every worker's calibrated 90th percentile under the limit and avoiding NWS hazard windows. It uses each worker's twin.
2. **Agent:** the Claude API with tools (`get_forecast`, `get_crew_status`, `run_optimizer`, `simulate_schedule`). Chat ("can Jake do roofing at 2pm?") plus a morning briefing.
3. **What-if simulation:** the agent can play any proposed schedule through the twins and show predicted strain before anyone works it.
4. **It improves with the crew:** after each shift, planned strain vs actual strain feeds back into the twins, so tomorrow's plans are better.
5. **Hard line:** the LLM never makes safety calls. Every schedule must pass the optimizer's hard constraints, and alerts never go through the LLM.

## Phase 9: hazards and man-down

1. `hazards/nws.py`: active alerts + hourly forecast at the job site, cached, works if the WiFi drops.
2. `hazards/wbgt.py`: Liljegren WBGT from the forecast (this also feeds the models as an input).
3. `alerts/router.py`: priority `MANDOWN` > `HEAT_RED` > `LIGHTNING` > `HEAT_YELLOW` > `FLOOD_FIRE`, sends only on change, no flicker at thresholds.
4. `alerts/mandown.py`: tilt rule now, accelerometer rule when the Grove part arrives, plus the unacknowledged red alert trigger.
5. **Man-down classifier:** a small model on accelerometer windows, trained on falls, sits and normal movement recorded by teammates.

## Phase 10: frontend (Next.js + Tailwind + Recharts + Leaflet)

1. **Crew lead dashboard:** worker cards (green, yellow, red), minutes until predicted danger, hazard map, schedule from the planner.
2. **Forecast fan chart:** core temp now and 30 min ahead with the calibrated band, plus the "why" line.
3. **Learning panel:** each worker's twin improving over time.
4. **Agent panel:** chat and morning briefing.
5. **Worker view (phone):** giant status color, current hazard, break countdown.
6. **Man-down screen:** full-screen on every open page.
7. **Demo controls, one click:** test NWS alert, replay on/off, replay speed, demo threshold.
8. **Results page:** the harness report, every model next to Kalman.

## Phase 11: sweat (hardware-dependent)

1. Whole-body sweat loss estimate from the heat strain model + a general drink-water message. No sensor needed.
2. Measured vs predicted sweat in the twin, and the "sweating dropped while temp keeps rising" warning, once a DHT22 exists.

## Phase 12: freeze and demo

1. Record real exercise sessions for replay.
2. Rehearse the 3-minute demo, including switching to replay mid-demo.
3. Final figures, README run steps.

---

## Cut line (if time runs out, drop from the bottom)

Sweat, then man-down classifier, then Transformer challenger, then explainability, then agent chat (keep the optimizer). Never cut: foundation, simulator, harness, GRU forecaster, conformal, twins demo, live demo path.

## Decisions needed (with my pick)

1. **Priority change:** this plan puts the residual net, twins and agent ahead of hazards and man-down, which changes the spec's order. Pick: yes, but hazards and man-down stay in because they're in the demo script.
2. **New libraries:** torch, pythermalcomfort, ortools, anthropic (all named in the spec), plus Chart.js for the first bare page. Pick: yes. Conformal, search and explainability are hand-written to avoid more.
3. **Heart rate source:** follow the protocol table (Pulsoid, not the band). Pick: table.
4. **Kalman start + early alerts:** uncertainty starts at 0 (Buller 2013), yellow 38.0 and red 38.5 on the estimate until the forecaster works. Pick: yes.
5. **Privacy conflict:** the spec says crew leads see a core temp chart and also never see raw biometrics. Pick: crew lead sees status + minutes until danger; charts live in the worker view and a labeled demo view.
6. **WiFi transport:** UDP or WebSocket, hardware team's call. Pick: UDP.
7. **Weather history:** Open-Meteo historical API (free, no key). Pick: yes.
8. **Run tracking:** W&B needs an account. Pick: local logs first, W&B optional.
9. **Worker location:** the band has no GPS. Pick: fixed positions on the job site map.

## Risks to check now

- **Hackathon rules.** This plan is a lot for 36 hours by one software person, even with me. It works if we can build the ML before the event. Many hackathons ban or require disclosure of pre-built code. Check SwampHacks rules first, because it decides how much of this is realistic.
- **Python 3.14** is new. torch or pythermalcomfort may need a 3.12 environment.
- **Pulsoid free plan** may not include API access.
- **Public datasets** may not be downloadable or may lack minute-level heart rate. That would weaken the sim-to-real story.
- **WBGT needs sun strength.** NWS only gives cloud cover, so it's estimated.
- **ECTemp is patented.** Fine for a hackathon.
