# Redline: technical write-up

Redline keeps outdoor crews from overheating. A wristband streams sensor data to a laptop, which estimates each worker's core body temperature every minute, forecasts it 30 minutes ahead with calibrated uncertainty, schedules the workday around heat and weather, and escalates to a phone call if a worker goes down.

**Design principle:** learned models predict and suggest. Deterministic code makes every safety decision (alert thresholds, the plan rule checker, the lightning rule, emergency number blocking). No threshold is ever learned.

Standard technique names are in parentheses. Full source list: [techniques.md](techniques.md). Every result is on people, days or years the model never trained on.

---

## 1. System architecture

```
Band (ESP32-S3) --Bluetooth LE (Nordic UART) / USB--> FastAPI backend --WebSocket--> Next.js dashboards
BLE heart rate or labeled demo HR input --------^      |  SQLite
Weather / lightning / flood feeds ------------------^  |-- Kalman -> GRU ensemble -> alerts -> band commands
                                                       |-- CP-SAT planner + Claude agent + rule checker
                                                       |-- emergency escalation (ElevenLabs + Twilio, allowlisted)
```

- **Link:** Bluetooth Low Energy, Nordic UART Service, advertised as "Redline Band": no WiFi, internet or pairing. The laptop side (bleak) runs in a helper process, rebuilds lines split or merged across notifications, and reconnects with backoff. USB serial at 115200 carries the same lines as a backup. First wireless test: 498/500 accelerometer lines and 50/50 PPG lines in 10 s, 185-byte MTU.
- **Line protocol:** plain text lines (`TEMP:34.62`, `LIGHT:950`, `ACC:x,y,z` at 50 Hz, `PPG:...`, `BTN:1`, `BTN:SOS`), commands back (`BUZZ:HEAT_RED`). Every reading is timestamped on the laptop at arrival. Unknown or malformed lines are dropped, never crash the parser (fuzz-tested with 10,000 random lines).
- **Replay mode** streams a scripted simulated shift at up to 10× speed, so every module runs and is tested with no hardware.
- **Privacy by view:** the crew-lead stream carries status only (green/yellow/red, minutes to danger), never heart rate, core temp or personal offsets. Enforced and tested on every WebSocket message and REST endpoint.

---

## 2. Band sensing

**Hardware (as built):** Adafruit ESP32-S3 Feather (4 MB flash / 2 MB PSRAM); Adafruit Prop-Maker FeatherWing for its LIS3DH accelerometer (I2C 0x18, ±8 g, 100 Hz, read at 50 Hz); photoresistor + 10 kΩ divider on pin 10 (sunlight, or pulse through a fingertip); 1N4148 diode + 10 kΩ on A5 for skin temperature (about -2 mV/°C, 64-sample average, two-point calibration); passive buzzer (pin 5, `tone()`); button (pin 6); built-in NeoPixel. Pins for analog reads are on ADC1 so they work with the radio on. Powered by a portable phone charger.

**Firmware** (`firmware/band/band.ino`, about 630 lines plus `config.h`, Arduino C++): no `delay()` in the loop, every job on its own timer (motion and pulse at exactly 20 ms via `micros()`); auto-detects ADXL345 / LIS3DH / MMA7660 and BME280 / BMP280 by I2C address and chip ID; non-blocking beep queue and per-pattern light rendering; debounced button (short press = `BTN:1`, 3 s hold = `BTN:SOS`); USB writes never block, so it runs with no computer attached. Compiled and flashed from the Mac with arduino-cli (`tools/flash_band.sh`). The band only samples and reports; all processing is on the laptop.

**Heart rate from light (photoplethysmography).** PPG sampled at 50 Hz on an ADC1 pin (works with WiFi on), sent in 10-sample batches. Accelerometer at 50 Hz on the same clock.
1. Detrend + band-pass 0.7-3.5 Hz (42-210 bpm).
2. Motion removal with an adaptive noise canceller (NLMS filter) using the 3 accelerometer axes + motion energy as the reference signal.
3. Rate from the spectral peak, cross-checked by autocorrelation, over a 10 s window. Peaks at harmonics of the arm-swing frequency are excluded (cadence-lock rejection).
4. A 0-1 quality score (peak clarity, beat-to-beat consistency, motion level, residual signal). Below 0.55, no value is emitted.

Synthetic benchmark (30 sessions × 5 min per activity, unseen seeds), mean absolute error: rest 0.6 bpm (100% coverage), walking 1.5 bpm (68%), arm work 6.3 bpm (49%). A naive peak counter scored 22-47 bpm.

**Real fingertip test** (photoresistor, phone flashlight through the finger, counted reference 53 bpm): a 60 s recording's strongest rhythm was 52-57 bpm (18% of in-band power at 50-56 bpm). Two later 30 s recordings showed a steady 90 bpm carrying 41% of the power: flashlight PWM flicker aliased by 50 Hz sampling. Fix in firmware: box-average every ADC read between samples (anti-aliasing); the 90 bpm share fell to 5%, and the remaining estimates scattered 45-61 bpm with quality below the 0.55 cutoff, so none were shown. Conclusion: a real but weak signal; needs a steady red LED and a light-tight finger clip.

**Demo heart rate:** with no reliable pulse sensor, the demo uses a labeled input (`POST /api/demo/heart_rate`, rest 65-75 or active 105-115 bpm, bounded random walk, 45 s ramps, source `demo_input`, other HR sources ignored while on). It's tagged "Heart rate: demo input" on screen; everything downstream runs for real.

**Fall detection:** impact over threshold + orientation change + stillness, confirmed by a barometric height drop (about 0.12 hPa per meter) when available (threshold detection with sensor confirmation). Activity comes from the same accelerometer stream (it replaced the tilt switch).

**Offline backup:** any device using the standard Bluetooth Heart Rate Profile (0x180D / 0x2A37), e.g. an Apple Watch through HeartCast, read with `bleak` in a separate process so a macOS permission failure can never take down the server.

---

## 3. Core temperature estimation

**Extended Kalman filter, ECTemp model (Buller et al. 2013, US Army).** Core temperature CT is the hidden state; 1-minute mean heart rate is the observation.

```
Predict:  CT- = CT_prev            v- = v_prev + γ²
Observe:  h(CT) = b2·CT² + b1·CT + b0        c = 2·b2·CT- + b1   (linearized slope)
Update:   K = v-·c / (c²·v- + σ²)
          CT = CT- + K·(HR - h(CT-))         v = (1 - K·c)·v-
```

Parameters as published: γ = 0.022 °C/min, σ = 18.88 bpm, b2 = -4.5714, b1 = 384.4286, b0 = -7887.1, CT0 = 37.1 °C, v0 = 0. At steady state the gain closes about 5% of the gap per minute (half-life about 13 minutes), so single outliers barely move it and only sustained changes do.

- **Sensor fusion:** with several HR sources in one minute, sequential updates with per-source noise (watch σ = 18.88, band PPG σ = 2 × 18.88). Missing HR runs predict-only, and the UI shows "heart rate missing" instead of a confident green.
- **Test-set error:** RMSE 0.398 °C on 10 unseen people.

---

## 4. Forecaster

**Task:** core temperature at every minute t+1 ... t+30, as the 10th, 50th and 90th percentiles.

### Data
- **Simulation (physics-based, ISO 7933 Predicted Heat Strain via pythermalcomfort):** 9,000 shifts with randomized body size, fitness, acclimatization, clothing, workload, breaks and Gainesville-style weather, stepped per minute. Heart rate model calibrated to the real data. A second version adds PPG artifacts (cadence lock, effort noise, dropouts) (data augmentation).
- **Real:** 65 people from a published heat-stress study (Mendeley), each walking at 5.31 km/h, 4% grade, in 40 °C and 22 °C rooms with an ingested core-temperature pill and a chest-strap HR monitor. Missing inputs (WBGT, workload) filled from the published protocol (WBGT 32.0 / 16.4 °C, 5.35 met).
- **Split by person (grouped split).** 10 people locked as the test set and scored once. The rest used for training, early stopping and calibration.

### Model
- **Sequence-to-sequence GRU encoder-decoder.** The encoder reads the past at 1-minute resolution and builds a hidden state; the decoder unrolls 30 steps with known future inputs (forecast WBGT, planned workload) and a horizon index.
- **Inputs (15 per minute):** HR, HR missing flag, 5-min HR change, skin temp + flag, activity + flag, WBGT + flag, workload + flag, Kalman CT, Kalman SD, Kalman slope, time into shift.
- **Residual target (hybrid physics-ML):** the network predicts CT minus the Kalman estimate, so it learns corrections to a physics baseline.
- **Non-crossing quantiles:** median plus softplus-positive gaps, so p10 ≤ p50 ≤ p90 by construction.
- **Two heads:** the forecast head (30 × 3 quantiles), and a "now" head with a mean and log-variance.

### Loss
```
L = L_pinball + w_nll·L_nll + w_safety·L_safety + w_smooth·L_smooth

L_pinball = mean over q ∈ {0.1, 0.5, 0.9} of max(q·e, (q-1)·e),   e = y - ŷ_q      (quantile regression)
L_nll     = ½·(log σ² + (y - μ)² / σ²)                                             (Gaussian NLL, now head)
L_safety  = w(y)·max(0, y - μ)²,   w = 3 when true CT > 38.0 °C, else 1             (asymmetric, ours)
L_smooth  = max(0, |Δŷ| - 0.05 °C/min)²  on the median path                         (physiology prior, ours)
```
Training with random sensor masking (input dropout), pretraining on simulation then fine-tuning on real people at 0.3× learning rate, early stopping on held-out people.

### Model selection and ensemble
- **Random hyperparameter search**, 30 trials: hidden size, depth, dropout, learning rate, loss weights, simulator version.
- **Cross-fitted deep ensemble:** the top 5 configurations (22k-323k parameters each; hidden 48-128, 1-3 layers). The 55 non-test people are split into 5 folds of 11. Member i is pretrained on simulation and fine-tuned on the 44 people outside fold i (5 of them held out for early stopping). The forecast is the mean of the 5 members.

### Calibration
- **Conformal prediction (one-sided conformalized quantile regression, CV+).** Each member scores only its own fold, giving out-of-fold misses on all 55 people. For each horizon k, the p90 edge is shifted by the ⌈(n+1)·0.9⌉/n empirical quantile of the misses (and the p10 edge symmetrically). This gives finite-sample coverage without distributional assumptions.
- Out-of-fold p90 coverage: 0.85 → 0.90 after calibration at every horizon. The "now" uncertainty is scaled ×1.08.

### Results (10 unseen test people, 20 sessions, scored once)

| | Kalman raw | Kalman + trend | **Ensemble** |
| --- | --- | --- | --- |
| Now RMSE (95% CI by person) | 0.398 (0.33-0.44) | 0.398 | **0.347** (0.21-0.40) |
| RMSE +10 / +20 / +30 min (°C) | 0.442 / 0.481 / 0.500 | 0.384 / 0.380 / 0.400 | **0.363 / 0.370 / 0.371** |
| Quantile loss +20 | 0.158 | 0.099 | **0.088** |
| p90 coverage +20 (target 0.90) | 0.52 | 0.98 | 0.95 |
| Yellow caught / lead / false alarms per h | 12% / 29 min / 0.03 | 88% / 13.0 / 0.23 | 88% / **16.9** / **0.20** |
| Red caught / lead / false alarms per h | 70% / 18 / 0.26 | **100%** / 36 / 0.30 | 90% / 32 / 0.46 |

23% lower +20 min error than the published method, with valid uncertainty. Against our own trend baseline the accuracy gain is small (3-7%) but warnings arrive 30% earlier (16.9 vs 13.0 min) with fewer false alarms (0.20 vs 0.23/h). The Kalman-flat baseline with a 90% range warned nearly as well (88%, 16.1 min, 0.20/h), so most of the warning benefit comes from forecasting with a calibrated range; the network adds accuracy and slightly earlier warnings. On red alerts the trend baseline caught 10/10 vs our 9/10 (one episode, n = 10). Validation-set error (0.230) was optimistic due to model selection; 0.370 is the number we report.

Confidence intervals: cluster bootstrap by person. Agreement: Bland-Altman limits -0.71 to +0.65 °C (vs -0.77 to +0.79 for Kalman). Feature attributions for the "why" line on the dashboard: integrated gradients.

---

## 5. Personalization

**Diagnosis (error decomposition):** most forecast error is a stable per-person offset (between-session correlation +0.57), and resting heart rate carries no information about it (correlation -0.02).

| Approach | Result |
| --- | --- |
| Per-person 16-dim embedding learned from next-10-min HR (self-supervised) | Worse: 0.30 → 0.42 °C |
| Meta-learned 4-dim context (CAVIA, Zintgraf et al. 2019) | No gain |
| **Offset from thermometer spot checks (Bayesian shrinkage)** | **0.308 → 0.270 °C at +20 min** |

```
offset = Σ misses / (n_checks + 2)       clipped to ±0.6 °C;  any miss > 1.5 °C ignored
```
The "+2" is a prior worth two average-person checks (empirical Bayes shrinkage). Applied once to the estimate and every forecast quantile, per worker, persisted in SQLite. Evaluated on 19 people using 3 core-temperature pill readings from one session as stand-in checks for the other session; a real ear/oral thermometer adds noise.

---

## 6. Alerts and safety logic

Deterministic, constants in `backend/config.py`:
- **Yellow:** p90 of the 20-min forecast ≥ 38.0 °C. **Red:** current estimate or 10-min forecast ≥ 38.5 °C. Clear only 0.1 °C below the line (hysteresis). Lines follow ACGIH heat-stress guidance (38.0 °C unacclimatized, 38.5 °C acclimatized).
- **Forecast-based triggering:** alerts use where a worker is heading, which already encodes level, rate and acceleration. A raw rate-of-change trigger is the Kalman + trend baseline, which warned 3.9 min later.
- **Priority router:** man-down > heat red > lightning > heat yellow > flood/fire. Commands sent on change, re-sent every 20 s.
- **Man-down / SOS → escalation** (section 10).

---

## 7. Weather and site hazards

Live, free feeds for 79 real Florida power plants: NWS alerts (zone-matched), Open-Meteo hourly weather, NOAA GOES-19 Geostationary Lightning Mapper (20 s files), NOAA NWPS river gauges, NASA FIRMS fire detections, iNaturalist wildlife sightings. Each feed caches its last good result and degrades to "stale" offline.

What the rest of the system consumes:
1. **Stop-work state (rule-based):** ≥ 2 good-quality flashes within 10 miles stops outdoor work; all-clear after 30 min with no flash; a stale lightning feed never gives the all-clear. NWS lightning/flood/tornado warnings also stop work.
2. **Per-slot weather risk:** storm and rain probability, and **WBGT** computed with the Liljegren method (Argonne), a vectorized NumPy port validated against published reference values. These feed the forecaster and the planner.

**Tomorrow's 0-10 risk index (supervised classification).** Labels: 2018-2024, 202,003 site-days of NWS warnings, storm reports, NOAA Storm Events damage and OSHA heat injuries, mapped to a severity ladder. Features: only information available by 6 PM the day before (verified by a leakage test that scrambles post-cutoff data). Models: climatology, logistic regression, gradient-boosted trees (HistGradientBoostingClassifier), and a probability blend of the last two. Trained 2018-2022, tested 2023-2024, plus a spatial hold-out of whole regions. A model ships for a hazard only if its PR-AUC gain over climatology has a 95% interval above zero.

| Held-out 2023-2024, PR-AUC | Climatology | **Blend** |
| --- | --- | --- |
| Any warning | 0.29 | **0.71** |
| Heat advisory | 0.18 | **0.70** |
| Flood warning (lift mostly from NWS warnings already in effect) | 0.24 | **0.80** |
| Storm warning | 0.16 | **0.35** |
| Fire, heat injury | no clear gain | climatology ships |

---

## 8. Workday planner

**Formulation (combinatorial optimization, constraint programming, Google OR-Tools CP-SAT).** Boolean decision variables x[worker, task, slot] over 30-minute slots. A typical crew day has on the order of 10^107 assignments. CP-SAT solves it with constraint propagation, branch and bound, and conflict-driven clause learning, typically in seconds.

- **Hard constraints:** one activity per worker per slot; task staffing, skills and time windows; mandatory shade breaks; no outdoor work during lightning stop-work or listed NWS hazards; and every worker's predicted p90 core temperature < 38.0 °C at every minute.
- **Objective (weighted linear objective with soft constraints):** maximize priority-weighted work, minus penalties for predicted heat margin, outdoor work in high storm-probability slots (doubled for elevated or hard-to-stop tasks), and unfair heat exposure.
- **Predict-then-optimize:** per-worker heat costs come from the PHS model under each slot's forecast weather, shifted by the personal offset plus an uncertainty margin. The chosen plan is re-simulated end to end, because heat accumulates across slots.

**LLM agent (Claude, tool use).** Tools: forecast, optimizer, simulator. Pipeline: generate candidate plans under several objective variants → rule-check → critic → fuse → re-check (Archon-style generate-verify-critique-fuse), plus a plain-English rationale and the 6 PM briefing.

**Verifier (generator-verifier pattern):** a deterministic rule checker re-validates every plan from every source before it is shown or applied.

**Closed-loop replanning (model predictive control style):** each minute, if a worker's 20-min p90 reaches an early line (37.7 °C), candidate interventions (break now, swap with a cooler worker, defer heavy work) are simulated forward. The best is rule-checked, then applied automatically if it costs no work, or sent to the crew lead if it does.

| 12 held-out simulated scenarios | Safety violations | Work done | Heat alerts/day |
| --- | --- | --- | --- |
| Fixed schedule (current practice) | 107 | 86% | 1.94 |
| Claude alone, no checker | in 92% of plans | 86% | 1.67 |
| Optimizer + checker | 0 | 68% | 0.19 |
| **Full agent** | **0** | 67% | **0.17** |

Closed-loop replanning (simulated with the PHS model and an idealized forecast stand-in, not on people): worker-minutes above 38.0 °C -22%, above 38.5 °C to zero, no work lost. Storm-probability cost: outdoor work in ≥ 40% storm slots 29 → 9 units at equal throughput.

Tested and rejected on held-out data: tuned objective weights (random search + successive halving: -6% tasks), and 20/10 work-rest micro-cycles in hot slots from a 2024 study (worker-minutes over 38.0 -3% but over 38.5 34 vs 8, fewer tasks). Both remain options; defaults unchanged.

---

## 9. Self-improving agent

The agent improves its context, strategy and settings over simulated days. It never touches weights of a safety model, thresholds or the rule checker. Score: priority-weighted work - 0.25 per worker alerted - 0.5 per °C the hottest worker exceeds 37.5, with zero rule-checker violations required.

1. **Self-play curriculum (Absolute Zero, Zhao et al. 2025):** Claude proposes bounded scenario specs across 5 stress families (heat wave, unacclimatized hires, afternoon storms, short staffing, deadlines). Code validates them. Each is solved 6 times; kept only if 0 < solve rate < 1. 27 of 93 kept.
2. **Agentic Context Engineering (ACE, Zhang et al. 2025):** generator → reflector → curator → gate over 14 days. A playbook update is kept only if held-out gate days improve. 3 of 18 updates kept; every version saved and restorable.
3. **Reflective prompt evolution + open-ended archive (GEPA, Agrawal et al. 2025; Darwin Gödel Machine, Zhang et al. 2025):** Claude mutates the agent's strategy note and ranking weights from failure traces. Minibatch screening, a Pareto archive over 5 metrics, novelty-weighted parent selection, final selection on validation days only. Validation score 0.378 → 0.452.
4. **Nightly loop:** learn per-worker profiles from yesterday's simulated shift (against a hindcast on the actual weather), generate 30 Monte Carlo variations of tomorrow, practice and gate, and report a risk estimate ("k of 30 variations exceed the yellow line"). 14 nights, 13 held-out days × 6 outcomes: risk Brier score 0.140 → 0.105 (better calibrated), but workers alerted per day 0.24 → 0.27 and work done -11%; 1 lesson kept, 0 of 9 strategy edits accepted. $0.45.

Solver nondeterminism (wall-clock limits) initially produced false "improvements". All loops now run with a fixed work limit and one thread.

| 12 held-out scenarios | Violations | Work done | Heat alerts/day |
| --- | --- | --- | --- |
| Agent, day 1 | 0 | 67% | 0.17 |
| After ACE playbook (day 14) | 0 | 67% | **0.11** |
| After GEPA + playbook | 0 | 61% | **0.03** |

Total Claude spend for all learning loops (including the nightly loop): $2.49.

---

## 10. Offline language model

**Purpose:** keep the agent working with no connectivity, at zero cost, with worker data staying on the laptop.

- **Base:** Qwen3-1.7B, 4-bit (about 1 GB), run with Apple MLX.
- **Knowledge distillation:** the Claude agent solved 263 training scenarios (leakage guard: a content hash refuses any held-out day). Cost $21.71 ($23.57 including a Claude re-run on test days for comparison).
- **STaR filtering:** keep only plans that pass the rule checker, aren't the all-rest fallback, do ≥ 30% of the work, and rank at least as high as the optimizer-only plan. 224 days kept, 3 tasks per day (plan, ideas, explanation).
- **QLoRA fine-tuning:** rank-16 adapters on all 28 layers (about 1% of weights trainable), answer-only loss, 2 epochs, LR 1e-4 cosine, 98 min on an M5 GPU. 70 MB adapter.

| 12 held-out scenarios | Valid | Passes every rule alone | Inside full pipeline: violations / plan score / cost |
| --- | --- | --- | --- |
| Claude Opus | 100% | 8% | 0 / 0.435 / $0.155 |
| Qwen3-1.7B base | 0% | 0% | 0 / 0.425 / $0 |
| **Qwen3-1.7B + LoRA** | 43% | 17% | 0 / **0.431** / **$0** |

Round 2 (validation-selected, scored once on the same 12 held-out days):

| Standalone, 5 samples/day | Valid | Pass every rule | Good pass (pass + work ≥ optimizer - 0.05) | Work when passing |
| --- | --- | --- | --- | --- |
| Claude Opus alone | 100% | 8% | 8.3% | 0.82 |
| Original adapter, free | 43% | 17% | 0% | 0.35 |
| Original adapter, constrained decoding + rules | 100% | 43% | 6.7% | 0.48 |
| After 1 round of expert iteration, constrained | 100% | 57% | 5.0% | 0.50 |
| + verifier-feedback repair (3 tries) | 100% | 68% | 8.3% | 0.50 |

- **Constrained decoding** made every output valid. **Expert iteration** (sample 8 per training day, keep the best plan that passes and does real work, add Claude's examples, retrain one epoch) raised validation good-pass 7.1% → 11.6%. The **repair loop** feeds the rule checker's violations back; it raises passing mostly by resting people.
- **Did not work:** an edit-the-optimizer's-plan format (76% empty edits; beat the optimizer once in 112), and naive self-training before constrained decoding (only 3.6% usable samples).
- **Full pipeline:** agent + round-1 adapter 0 violations, plan score 0.429 vs Claude 0.435, 49 s vs 67 s per plan, $0.
- **Explanations, code fact-check** (every number, time and name must be backed by the plan's facts): Claude 0/108 unsupported claims, local model 0/86 on validation days; 0/33 on held-out days.

Standalone plans still do less work than the optimizer (0.50 vs 0.69), so no model plans unverified.

---

## 11. Emergency escalation

Man-down (fall + no response) or SOS → crew lead and safety officer notified (an empty number is skipped) → 30 s cancel window → an ElevenLabs conversational voice agent calls through Twilio. The agent receives the incident as dynamic variables (worker, site, GPS, event, time, last vitals) and is instructed to answer only from them, saying "Sorry, I don't know the answer." otherwise. One call per incident, keyed by incident ID. Allowlist-only dialing; 911, 112, 999, 988, 933 and short codes are blocked in every format; live calls are off unless `EMERGENCY_LIVE_CALLS=1`. In the demo it calls a teammate.

---

## 12. Engineering and testing

**Stack:** Python, FastAPI + WebSockets, SQLite, PyTorch, scikit-learn, NumPy/SciPy, pythermalcomfort, OR-Tools, Anthropic API, MLX, bleak, pyserial; Next.js, Tailwind, Recharts, Leaflet; Arduino C++ on the band.

**Tests:** 421 backend, 93 ML, 162 end-to-end, 53 frontend (729), all passing. End-to-end tests run the real app against 36 acceptance criteria: parser fuzzing, exact thresholds and hysteresis, alert priorities, man-down timing, privacy on every message, blocked numbers in 16 formats, one call per incident, lightning edge cases (9.9 vs 10.1 mi), offline behavior, restart recovery, 6 workers × 8 h load, and a check that no code path writes a safety constant.

---

## 13. Negative results

| Tried | Outcome | Decision |
| --- | --- | --- |
| HR-based personal embeddings | +40% error | Thermometer offset |
| Meta-learning (CAVIA) | No gain | Dropped |
| Tuned planner weights | Didn't hold on held-out days | Defaults kept |
| 20/10 hot-hour micro-cycles | More time above 38.5 °C on held-out days | Default kept, option available |
| Small LM planning alone | Passes by under-scheduling | Used only inside the verified pipeline |
| Red-alert recall | 9/10 vs 10/10 for trend baseline | Reported as is |

## 14. Key decisions

1. **Code makes every safety call; models only predict and suggest.**
2. **Locked test set, scored once.** We report 0.370 °C, not the 0.230 validation figure.
3. **Offline first:** the band on Bluetooth LE (no WiFi), Bluetooth HR, a local LLM, cached feeds.
4. **Thermometer checks over HR twins**, after the diagnosis showed HR carries no offset information.
5. **AI coordinator, not AI manager:** automatic changes only when they can't cost work; humans approve the rest.
6. **No automated 911 calls** in the demo; allowlisted teammate phone.
7. **Research claims tested on held-out data before adoption** (20/10 cycles).
