# PlanIT Control: how it works

A wristband and laptop system that keeps outdoor crews from overheating. It estimates each worker's core temperature live, forecasts it 30 minutes ahead, plans the workday around the heat and the weather, and calls for help if someone goes down.

**The rule behind everything:** AI predicts and suggests. Plain code makes every safety call.

Standard names for each method are in parentheses. Sources for every technique: [techniques.md](techniques.md). Every number below is on people, days or years the model never trained on.

---

## 1. The band

**Hardware:** Adafruit ESP32-S3 Feather, light sensor + LED (pulse), 3-axis accelerometer, temperature/humidity/pressure sensor, skin temperature diode, buzzer, button, status light.

**Design choice:** the band sends raw numbers fast. The laptop does the math. Easier to improve, no reflashing.

**Pulse from light (photoplethysmography, PPG).** Each heartbeat pushes blood under the skin, which absorbs a little light. The sensor sees one dip per beat.
1. Keep only heartbeat speeds, 42-210 bpm (band-pass filter).
2. Subtract arm movement using the accelerometer, like noise-cancelling headphones (adaptive noise cancellation, NLMS filter).
3. Find the strongest repeating rhythm that isn't the arm's (spectral peak + autocorrelation).
4. Give a quality score. Bad signal = no number, never a guess.

Synthetic test: off by 0.6 bpm sitting, 1.5 walking, 6.3 during arm work. Real recordings pending.

**Falls:** sharp jolt + lying flat + no movement, confirmed by a height drop from the barometer (threshold detection + barometric confirmation).

**Offline heart rate backup:** any standard Bluetooth heart rate device, like an Apple Watch through HeartCast (Bluetooth Heart Rate Profile). No internet needed.

---

## 2. Core temperature right now

**Kalman filter (extended Kalman filter, US Army ECTemp, Buller 2013).** Heart rate rises with core temp. Every minute:
1. **Predict:** core temp is probably what it was, but a bit less certain.
2. **Compare:** the heart rate we got vs the heart rate this temp should cause.
3. **Correct:** nudge the estimate, weighted by how much it trusts the guess vs the reading.

No training needed. A few multiplications a minute. It ignores single weird readings and only moves on sustained changes.

**Two heart rate sources (sensor fusion):** band and watch combined by how noisy each is. One drops, it keeps going.

On 10 unseen test people: off by 0.398 °C on average.

---

## 3. The forecaster (the neural net)

**Job:** predict core temp for the next 30 minutes, as low / likely / high.

**Data:**
- **9,000 simulated work shifts** from the international heat-strain standard (physics simulation, ISO 7933 PHS), with varied bodies, fitness, weather, workloads and fake sensor noise (data augmentation).
- **65 real people** from a published heat study, with swallowed core temp pills.
- Learn physics on fake shifts, then real bodies on real people (pretraining + fine-tuning, sim-to-real).
- Split by person, never by time. 10 people locked away and scored once (held-out test set).

**The network (GRU, sequence-to-sequence encoder-decoder):**
- Reads the last hour minute by minute and keeps a running memory.
- Writes the next 30 minutes, using the weather forecast and planned work.
- Inputs: heart rate and its change, skin temp, movement, outside heat (WBGT), workload, the Kalman's estimate and trend, time into shift, and a "missing" flag per sensor.
- Predicts the correction to the Kalman, not the raw temp (residual learning). If unsure, it falls back to the proven formula.

**How it was trained (the loss):**
- Grade low / likely / high fairly (quantile regression, pinball loss).
- Guessing too low costs extra, more when the person really is hot (asymmetric safety loss, our design).
- Forecast lines can't jump faster than a body can (smoothness penalty, our design).
- Sensors hidden at random so it survives dropouts (input masking).
- 30 recipes tried, best 5 kept (random hyperparameter search), stopped when it stopped improving (early stopping).

**Making it trustworthy:**
- 5 networks vote and we average them (deep ensemble).
- Ranges stretched until "90% sure" really means 90%, using real past mistakes (conformal prediction).
- 55 people take turns training and checking, so nobody's wasted (cross-fitting, CV+).
- Always compared to simple baselines. Code refuses to score anyone the model trained on (leakage guard).

**Results, 10 unseen test people:**

| | Army formula (Kalman) | Kalman + trend | **Our forecaster** |
| --- | --- | --- | --- |
| Right-now error | 0.398 °C | 0.398 °C | **0.347 °C** |
| 20-min forecast error | 0.481 °C | 0.380 °C | **0.370 °C** |
| "90% sure" range held | 52% | 98% | **95%** |
| Heat warnings caught early, lead time | 12% | 88%, 13.0 min | **88%, 16.9 min** |
| Red alerts caught | 7 of 10 | 10 of 10 | 9 of 10 |

23% better than the published method. Only a bit better than a simple trend on accuracy, but earlier warnings with fewer false alarms. We report the red-alert miss.

**Continuous:** runs every minute for every worker. The network itself is frozen during a shift and only updates after passing a locked test.

---

## 4. Personalization

**Problem:** at the same heart rate, some people run hotter than others (fitness, heat adaptation, hydration). Most of the error is a steady per-person offset.

- **Tried: 16 personal numbers learned from heart rate** (learned embeddings). Made it worse, 0.30 → 0.42 °C. Heart rate can't reveal the offset (correlation -0.02).
- **Tried: meta-learning** (CAVIA). No gain.
- **Works: quick thermometer checks** (Bayesian shrinkage). The offset = misses ÷ (checks + 2), so one odd reading can't swing it. Capped at 0.6 °C, wild readings ignored, never touches alert lines.

Result: 0.308 → 0.270 °C on 19 people, using the study's own pill readings as stand-in checks. A real thermometer will be a bit noisier.

---

## 5. Alerts

Plain code, never learned:
- **Yellow:** the high end of the 20-minute forecast crosses 38.0 °C.
- **Red:** now or the 10-minute forecast crosses 38.5 °C.
- Lines from the main US heat guideline (ACGIH).
- We trigger on where the worker is heading, not where they are.
- Each hazard has its own buzz and light pattern.
- Man-down and SOS start the emergency call.

---

## 6. Weather and site hazards

Free live feeds for 79 real Florida power plants: National Weather Service warnings, Open-Meteo weather, NOAA GOES-19 satellite lightning, NOAA river gauges, NASA FIRMS fires, iNaturalist wildlife sightings.

Everything boils down to two things that feed the planner:
1. **Can we work right now?** Lightning within 10 miles, flood or tornado warning = stop (rule-based).
2. **How likely is bad weather, and how much does it affect the work?** Storm and rain chances per time slot, plus outside heat (WBGT, Liljegren method).

**Tomorrow's 0-10 risk score** (gradient-boosted trees + logistic regression, blended). Trained on 7 years and 202,003 site-days of Florida warnings, storm reports and heat injuries. Tested on 2023-2024 only: 2-4× better than the usual rate for that site and month (e.g. heat advisory PR-AUC 0.70 vs 0.18). Where it didn't clearly win (fire, heat injury), the simple rate ships.

Every feed keeps its last good result. No internet = shown as "stale", nothing crashes.

---

## 7. The planner

**Job:** most work done, nobody overheats, nobody in a storm.

- **A puzzle solver builds the schedule (combinatorial optimization, constraint programming, Google OR-Tools CP-SAT).** A crew's day has about 10^107 possible schedules, more than the atoms in the universe. The solver never lists them. It crosses out impossible options instantly (propagation), drops hopeless branches (branch and bound), and remembers dead ends (clause learning).
- **Points system (weighted linear objective).** + points for work done, minus points for predicted heat, outdoor work in likely storms, and unfair heavy-work spreads.
- **Uses our forecasts while it builds (predict-then-optimize).** Every candidate schedule gets a minute-by-minute heat prediction for every worker.
- **AI proposes, code approves (generator-verifier).** Claude suggests plans, explains them and writes the 6 PM briefing. It works by calling tools: forecast, optimizer, simulator (LLM tool use). Its pipeline makes several candidates, checks them, critiques and combines them (Archon-style). A code rule checker re-checks every plan before anyone sees it.
- **Adjusts during the day (closed-loop replanning, model predictive control style).** When someone's forecast runs hot, it adds a break or swaps their heavy task before the alert fires. Safer-only changes are automatic, anything that costs work goes to the crew lead.

**Results, 12 held-out days:**

| | Safety violations | Heat alerts per day |
| --- | --- | --- |
| How crews schedule today | 107 | 1.94 |
| Claude alone | in 92% of plans | 1.67 |
| **Our planner** | **0** | **0.17** |

Acting on forecasts: 22% less time above 38.0 °C, zero above 38.5, no work lost. Storm-chance points: outdoor work in stormy slots 29 → 9, same total work.

---

## 8. Self-improving agents

**Job:** the planner's AI gets better every day, without code changes, without touching a safety rule.

1. **Invents its own practice (self-play curriculum, Absolute Zero).** Claude makes up hard days (heat waves, new hires, storms). Only days the agent solves sometimes are kept.
2. **Writes its own playbook (Agentic Context Engineering, ACE).** Plan → reflect on what went wrong → write a lesson → keep it only if scores improve on other days.
3. **Evolves its own strategy (reflective prompt evolution, GEPA + Darwin Gödel Machine).** Claude rewrites the agent's strategy from its failures. It keeps a family tree of good versions and picks winners only on unseen days.
4. **Nightly loop (in progress).** Learn from yesterday → update each worker's rating → test tomorrow's plan on 30 random versions of tomorrow (Monte Carlo) → practice → a risk number for the 6 PM briefing.

**Results, 12 held-out days:** heat alerts 0.17 → 0.11 per day with the same work after 14 days of playbook learning; 0.03 with the evolved strategy, which gives up some work. Total Claude cost: $1.95.

---

## 9. Our own offline AI

**Job:** keep the planner's AI working with no internet, for free, with worker data staying on the laptop.

1. Start from a free open-source model, shrunk to 1 GB (Qwen3-1.7B, 4-bit quantization).
2. Claude solves 263 practice days, which become the teaching examples (knowledge distillation).
3. Keep only plans that passed the rule checker and did real work (STaR filtering).
4. Train a small add-on, 1% of the model's size (LoRA / QLoRA fine-tuning), on the Mac's chip (Apple MLX). 98 minutes.

**Result:** inside the planner it ties Claude (plan score 0.431 vs 0.435), 0 violations, $0. Alone it isn't a planner, and neither is Claude. In progress: format forcing (constrained decoding), editing the solver's plan instead of writing one, and a fix loop driven by the rule checker's feedback.

---

## 10. Emergency call

On a confirmed man-down or SOS: notify the crew lead → 30-second cancel window → an AI voice agent calls (ElevenLabs + Twilio). It reads only the facts (who, where, GPS, what happened, last vitals) and says "Unknown" to anything else. In the demo it calls a teammate's phone. 911 and short codes are blocked in code, and live calls are off unless explicitly turned on.

---

## 11. Software

Python, FastAPI + WebSockets, SQLite, PyTorch, scikit-learn, NumPy/SciPy, pythermalcomfort, Google OR-Tools, Claude API, MLX, Next.js + Tailwind + Recharts + Leaflet. A replay mode streams a scripted shift at 10× speed, so the demo works with no hardware.

**Tests:** 386 backend, 79 ML, 159 end-to-end (the whole system: band lines, alerts, privacy, emergency, hazards), 50 frontend. All passing.

**Privacy:** the crew lead sees status only (green / yellow / red, minutes to danger), never heart rate or core temp.

---

## 12. What didn't work (and what we did about it)

| Tried | What happened | Decision |
| --- | --- | --- |
| Personal twins from heart rate | Made forecasts worse | Thermometer offset instead |
| Meta-learning twins | No gain | Dropped |
| Tuning the planner's point values | Better on practice days, not on test days | Kept defaults |
| 20 min work / 10 min rest breaks (from a 2024 study) | Less time above yellow, more above red, fewer tasks | Kept current breaks, 20/10 is an option |
| Small AI planning alone | Safe only by resting everyone | Use it inside the planner, keep improving it |
| Red-alert catch rate | 9 of 10 vs 10 of 10 for a simple trend | Reported as is |

## 13. Key decisions

1. **Bluetooth heart rate, no internet.** Job sites often have no signal.
2. **Pulse on the band itself,** watch as backup.
3. **Thermometer checks, not heart rate twins,** for personalization.
4. **The AI plans, code approves.** 92% unsafe alone, 0% with the checker.
5. **AI coordinator, not AI manager.** Safe changes are automatic, the human approves anything that costs work.
6. **Calls a teammate, never 911,** in the demo.
7. **Locked test set, scored once.** We quote the honest number (0.370 °C), not the lucky one.
8. **Our own offline model,** for no-signal sites and data privacy.
9. **Test research claims instead of copying them** (20/10 breaks).
