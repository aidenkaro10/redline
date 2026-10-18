# PlanIT Control: the full write-up

*How we built a wristband that warns outdoor workers before they overheat, plans their day around the heat, and calls for help when someone goes down. SwampHacks XII, Sperry Tech PlanIT Control track.*

---

## The problem

Heat is the deadliest weather hazard in the US, and heat illness at work is badly undercounted. The person overheating usually can't tell. By the time you feel dizzy, your core temperature is already high, and judgment is one of the first things to go. Most heat deaths at work happen in a worker's first few days on the job, before their body has adapted, and new workers are also the ones most likely to push through instead of speaking up.

Florida crews at solar farms, substations and power plants work in this every summer. Today the tools are a thermometer on the wall, a water cooler, and a supervisor's gut feeling.

We wanted something that does three things:
1. **Warns the worker before they're in danger,** not after.
2. **Plans the crew's day** so dangerous heat never happens in the first place.
3. **Gets help fast** if someone collapses anyway.

One rule ran through every design choice: **AI can predict and suggest, but plain code makes every safety decision.** No alert threshold, safety limit or emergency call is ever decided by a learned model.

---

## What it does

A worker wears the band. Every minute, the laptop estimates their core body temperature from their heart rate and forecasts it 30 minutes ahead. If the forecast is heading toward danger, the band buzzes yellow 15-20 minutes early. If they get there, it flashes red. The crew lead's dashboard shows everyone's status (never their raw health data), the site's weather and lightning, and the day's schedule.

Behind that, a planner builds the crew's schedule around tomorrow's forecast, and adjusts it during the day when someone starts running hot. If a worker falls and doesn't get up, the system notifies the crew, waits 30 seconds for a cancel, then an AI voice calls and reads out who, where, and their last vitals.

**The demo, in one line:** Jake goes yellow at minute 9 and red at minute 38 of a replayed shift. Maria falls at minute 40. 45 seconds later her band buzzes the man-down pattern, 60 seconds after that it's confirmed, and a phone on the table rings with the emergency report.

---

## The numbers at a glance

| | |
| --- | --- |
| 20-minute forecast error on 10 unseen people | **0.370 °C**, vs 0.481 for the published Army method (**23% better**) |
| Heat warnings caught early | **88%**, with **16.9 minutes** of warning on average |
| "90% sure" forecast range held | **95%** of the time (the published method: 52%) |
| Safety rule violations in AI-made schedules | **0%** with our rule checker, vs **92%** for Claude alone |
| Time workers spent above 38.0 °C when the planner adjusts on the fly | **22% less**, with no work lost |
| Tomorrow's hazard prediction vs. normal rates | **2-4×** better on held-out years |
| Offline AI planner vs. Claude inside our system | **0.431 vs 0.435** plan score, at **$0** |
| Real people in the training data | **65** (plus **9,000** simulated work shifts) |
| Weather history behind the hazard model | **7 years, 202,003** site-days |
| Automated tests | **674**, all passing |
| Code | about **34,000** lines of Python, **7,600** of TypeScript, **269** commits |
| Total API spend for all ML and agent training | about **$26** |

Every result in this write-up is measured on people, days or years the model never trained on.

---

## Part 1: The hardware

### The band

The band is an **Adafruit ESP32-S3 Feather** on a half-size breadboard strapped to the forearm. We picked the Feather because it has WiFi, Bluetooth and a built-in battery charger on one small board. An Arduino Uno was the backup.

**What's on it now:**

| Part | Job | Wiring |
| --- | --- | --- |
| Adafruit ESP32-S3 Feather | Main board | |
| Built-in RGB LED (NeoPixel) | Status color | `PIN_NEOPIXEL`, power enabled first |
| Passive buzzer | Alert sounds | D5, driven with `tone()`, 100 Ω in series |
| Push button | "I'm OK" (short press), SOS (3-second hold) | D6, `INPUT_PULLUP` |
| Tilt switch | Man-down and activity (backup) | D9, `INPUT_PULLUP` |
| 1N4148 diode + 10 kΩ resistor | Skin temperature | Junction read on A5 |
| Grove light sensor + LED | Pulse | Sensor on D10, LED on D12 |
| Grove 3-axis accelerometer | Movement, falls | I2C (SDA/SCL) |
| BME280 | Air temperature, humidity, pressure | I2C (shares the accelerometer's wires) |

**Things we figured out the hard way:**
- **The buzzer is passive.** Setting the pin HIGH does nothing. Every alert sound is a `tone()` pattern.
- **We wanted a 5-LED strip** for status colors, but its pads take no jumpers and need soldering. We used the board's built-in LED instead and gave every alert a different color and blink pattern, since there's only one light.
- **A diode is a cheap thermometer.** A plain 1N4148 diode's voltage drops about 2 mV for every degree it warms up. We read it through a 10 kΩ resistor, average 64 readings, and calibrate at two temperatures (room and ice water).
- **Analog pins vs. WiFi.** On this chip, pins A0-A4 stop reading analog values whenever WiFi is on. So the temperature sensor uses A5 and the pulse sensor uses D10, both on the converter that keeps working with WiFi.
- **Upload gotchas.** "USB CDC On Boot" has to be enabled or no serial port shows up. The port number changes after every upload. When an upload fails: hold BOOT, tap RST, release BOOT, retry.
- **No parts could be ordered.** The hackathon was too short for shipping, so everything new came from the MLH hardware lab. When there were no Grove-to-jumper cables, we cut Grove-to-Grove cables in half and stripped the ends into the breadboard.

### How the band talks to the laptop

The band reports and the laptop decides. The band never decides whether someone is in danger. It just sends readings and obeys commands, which keeps the firmware simple and lets us improve everything on the laptop without reflashing.

Text lines over USB at 115,200 baud (or UDP over WiFi):

| Band → laptop | Every | Meaning |
| --- | --- | --- |
| `TEMP:34.62` | 5 s | Skin temperature, °C |
| `TILT:0,ACT:7` | 1 s | Tilt state and activity |
| `PPG:512,515,...` | 200 ms | 10 raw light readings, 20 ms apart |
| `ACC:0.98,0.02,0.11` | 20 ms | Accelerometer, in g |
| `AIRT:31.4` / `HUM:58.2` / `BARO:1012.35` | 1 s | Air temperature, humidity, pressure |
| `BTN:1` / `BTN:SOS` | on press | "I'm OK" / help |

| Laptop → band | Light | Sound |
| --- | --- | --- |
| `BUZZ:HEAT_YELLOW` | Solid yellow | One long beep |
| `BUZZ:HEAT_RED` | Flashing red | Long beep every 10 s until pressed |
| `BUZZ:LIGHTNING` | Rapid white flashes | Rapid beeps |
| `BUZZ:FLOOD_FIRE` | Solid blue | Two short beeps |
| `BUZZ:MANDOWN` | Red "breathing" pulse | Continuous beeping until pressed |
| `BUZZ:OFF` | Solid green | Silent |

The laptop stamps every reading with its own clock the moment it arrives, so all signals line up. It re-sends the current status every 20 seconds, and if the band hears nothing for 60 seconds it blinks cyan, so a dead cable is obvious during a demo.

### Heart rate without the internet

Our first plan was an Apple Watch through the Pulsoid app. Then we realized Pulsoid goes through the cloud, and many job sites have no signal. A safety device can't depend on WiFi.

So we switched to **local Bluetooth**: the standard Bluetooth Heart Rate profile, which the HeartCast app can broadcast from an iPhone and Apple Watch, and which any chest strap or Garmin watch also speaks. No internet, no cost. It runs in its own process, so if macOS refuses Bluetooth permission, only the helper dies and the server keeps going.

Then we went further and put **a pulse sensor on the band itself**, so the band needs nothing else. The watch stays as a silent backup until the band is proven.

### Pulse from light

When your heart beats, a little extra blood pushes under your skin, and blood absorbs light. Shine an LED into the wrist, and a light sensor sees a tiny dip once per heartbeat. That's photoplethysmography (PPG), the same idea the Apple Watch uses.

The hard part is arm movement, which makes dips far bigger than the heartbeat. The laptop cleans it up in four steps:
1. **Keep only heartbeat speeds:** 42-210 bpm (band-pass filter, 0.7-3.5 Hz).
2. **Subtract the arm:** the accelerometer knows exactly how the arm moved, and a filter learns what that movement looks like in the light signal and removes it, like noise-cancelling headphones (adaptive noise cancellation, NLMS).
3. **Find the beat:** the strongest repeating rhythm that isn't the arm's rhythm (spectral peak + autocorrelation).
4. **Score the quality.** Below a threshold, no number is given at all. Never a guess.

On synthetic test data (30 sessions per activity, 5 minutes each, never used for tuning):

| Activity | Average error | Gives a reading |
| --- | --- | --- |
| Sitting | 0.6 bpm | 100% of the time |
| Walking | 1.5 bpm | 68% |
| Arm work | 6.3 bpm | 49% |
| (Simple peak counter, for comparison) | 22-47 bpm | |

Real numbers come from the band's own recordings, which our hardware team is capturing now (sitting, walking, arm swings and fake falls, 2 minutes each).

### Falls

A fall is a sharp jolt, then lying flat, then no movement, confirmed by the barometer seeing the band drop about a meter (air pressure changes about 0.12 hPa per meter). The tilt switch stays as a backup trigger. Movement or a button press cancels the check, and a red heat alert nobody acknowledges also starts one.

---

## Part 2: Estimating core temperature

You can't easily measure core temperature on a job site. The gold standard is a pill you swallow, about $50 each and single use. Skin temperature doesn't work, because skin can be cool while the core overheats.

But **heart rate rises as the core heats up,** because blood has to do two jobs at once: feed the muscles and carry heat to the skin. The US Army built a model around that (ECTemp, Buller et al. 2013), and we use it exactly as published.

It's a **Kalman filter.** Every minute it:
1. **Predicts:** core temp is probably what it was a minute ago, but it gets a little less sure.
2. **Compares:** the heart rate it just got vs. the heart rate that core temp should produce (a curve: about 79 bpm at 37 °C, 120 at 38 °C, 137 at 38.5 °C).
3. **Corrects:** nudges the estimate, weighted by how much it trusts its guess vs. the noisy reading.

It moves about 5% of the way toward what the heart rate says each minute, a half-life of about 13 minutes. One weird reading (standing up fast, a coffee) barely moves it. A sustained change does. With two heart rate sources, it blends them by how noisy each one is, and if heart rate disappears it keeps predicting and the dashboard says so.

**On 10 unseen people, it's off by 0.398 °C on average.** Good, but it only knows *now*, and it's always a bit behind. Preventing heat stroke needs a warning *before*. That's what the neural net is for.

---

## Part 3: The forecaster

### The data

A neural net learns from examples, and examples of core temperature changing minute by minute during heat stress are rare. We combined two sources.

**Real people.** A published study (Mendeley) where **65 people** walked on a treadmill at 5.31 km/h on a 4% incline, once in a 40 °C room and once at 22 °C, with a swallowed core temperature pill and a chest strap. **36 sessions stopped early** because people got too hot, which is exactly the zone we care about. The dataset had no weather or workload columns, so we read the study's methods and filled them in from the protocol (correcting our first guess after checking the paper).

**Simulation.** 65 people isn't enough. So we generated **9,000 simulated work shifts** using the international heat-strain standard (ISO 7933, Predicted Heat Strain): big and small bodies, fit and unfit, used to heat or not, mild and brutal weather, light and heavy work, breaks. We tuned the simulator's heart rate to match the real people, and a second version adds realistic pulse-sensor noise.

The net learns physics from the simulation first, then real bodies from the real people (pretraining, then fine-tuning).

**The fairness rule:** every person sits on exactly one side. **10 people were locked away** the whole project and scored exactly once at the very end.

### The network

A **GRU encoder-decoder**, a kind of neural network built for time series.
- The **reader** goes through the last hour minute by minute and keeps a running memory of what's been happening.
- The **writer** produces the next 30 minutes, one minute at a time, also using what we know is coming (the weather forecast and the planned work).
- **15 inputs per minute:** heart rate and how fast it's changing, skin temperature, movement, outside heat (WBGT), workload, the Kalman's estimate and trend, time into the shift, and a "missing" flag for each sensor.
- It predicts the **correction to the Kalman estimate**, not the raw temperature, so it builds on the proven formula and falls back to it when unsure.
- It outputs **three numbers per minute:** low (10%), likely (50%) and high (90%). Yellow alerts use the high one.

### How it learned

Training means "guess, check against what really happened, nudge thousands of knobs a little, repeat". The score it tries to shrink has four parts:
1. **Range accuracy:** grades the low, likely and high numbers fairly (pinball loss).
2. **Safety penalty:** guessing too low costs extra, and **3× extra** when the person really is above 38 °C. Saying someone's fine when they're overheating is the dangerous mistake.
3. **Physics:** a forecast that jumps faster than 0.05 °C per minute gets penalized, because bodies can't do that.
4. **Right now:** the current estimate has to be right too, along with how sure it is.

We randomly hid sensors during training so it copes when one drops out.

### Making it trustworthy

- **30 recipes tried** (network size, depth, learning rate, loss weights, simulator version). The **top 5** kept.
- **A team of 5 networks** (22,000 to 323,000 knobs each) that vote, and we average them. Each makes different random mistakes, and averaging cancels them out.
- **Taking turns:** the 55 non-test people were split into 5 groups of 11. Each network trained on 44 people and was checked on 11 it never saw, so every person helped train 4 networks and tested the 5th.
- **Honest ranges (conformal prediction):** using every network's mistakes on people it never saw, we stretched the ranges until "90% sure" really held 90% of the time. Before: 85%. After: 90%.

### Results on the 10 locked people (20 sessions, scored once)

| | Army formula (Kalman) | Kalman + simple trend | **Our forecaster** |
| --- | --- | --- | --- |
| Error right now | 0.398 °C | 0.398 °C | **0.347 °C** |
| Error at +10 / +20 / +30 min | 0.442 / 0.481 / 0.500 | 0.384 / 0.380 / 0.400 | **0.363 / 0.370 / 0.371** |
| "90% sure" range held (+20 min) | 52% | 98% | 95% |
| Heat warnings caught early | 12% | 88% | 88% |
| Warning lead time | | 13.0 min | **16.9 min** |
| False alarms per hour (yellow) | 0.03 | 0.23 | **0.20** |
| Red alerts caught | 7 of 10 | **10 of 10** | 9 of 10 |

**What this honestly says:**
- **23% more accurate** than the published method at 20 minutes, with ranges that actually mean what they say.
- Against a simple "keep going the direction it's heading" baseline, the accuracy gain is small (3-7%), but warnings come **30% earlier** with fewer false alarms.
- On red alerts, the simple trend caught 10 of 10 and we caught 9. With 10 cases, one miss is 10 points. We report it as is.
- During development our best model scored 0.230 on the checking group. On the locked test it scored 0.370. The checking score was flattered by being used to pick the model, so **0.370 is the number we stand behind.**

The 95% error bars on "right now" are 0.21-0.40 °C, from resampling people. The network runs every minute for every worker, and it's frozen during a shift: it only gets updated after passing a test it's never seen.

---

## Part 4: Fitting each worker

At the same heart rate, some people run hotter than others: fitness, heat adaptation, hydration, body size. When we dug into the errors, most of each person's miss was a **steady offset**, like a scale that always reads 2 pounds heavy (the per-session error spread was 0.276 °C, and a person's offset repeated across their two sessions with a correlation of +0.57).

| What we tried | Result |
| --- | --- |
| 16 personal numbers learned from each worker's heart rate | **Worse:** 0.30 → 0.42 °C. Heart rate can't reveal the offset (correlation -0.02) |
| Meta-learning: a network built to adapt fast with 4 numbers (CAVIA) | No gain |
| **Quick thermometer checks** | **0.308 → 0.270 °C** at 20 minutes (12% better) |

The thermometer method: a couple of times a shift, the worker takes a quick reading and types it in. The offset is all their misses divided by (number of checks + 2). That "+2" acts like two imaginary checks saying "this person is average", so one odd reading can't swing it. It's capped at 0.6 °C, ignores anything more than 1.5 °C off, and never moves an alert line.

That 0.270 came from a test on 19 people using the study's own pill readings as stand-in checks. A real ear thermometer is noisier, so the real gain will be somewhat smaller. We'll measure it during our treadmill tests.

---

## Part 5: Alerts and safety

All plain code, in one config file, never learned:
- **Yellow:** the high end of the 20-minute forecast crosses **38.0 °C**.
- **Red:** the current estimate or the 10-minute forecast crosses **38.5 °C**.
- These lines come from ACGIH, the main US heat stress guideline: 38.0 for workers not used to heat, 38.5 for workers who are. 38.5 is deliberately set with a margin for measurement error and time to cool down.
- An alert only clears 0.1 °C below its line, so it doesn't flicker.
- **We trigger on where the worker is heading, not where they are.** The forecast already includes both their level and how fast they're heating.
- When several things happen at once, the band shows the most urgent: man-down, then red heat, then lightning, then yellow, then flood or fire.

**Privacy:** the crew lead sees green / yellow / red and minutes to danger, never heart rate, core temperature or personal offsets. The worker sees their own data. We test this on every message the server sends.

---

## Part 6: Weather and site hazards

We pull free, live data for **79 real Florida power plants**:

| Source | What |
| --- | --- |
| National Weather Service | Heat, storm, flood and fire warnings, matched to each site's zone |
| Open-Meteo | Hourly weather for all 79 sites in one call |
| NOAA GOES-19 satellite | Every lightning flash, in files every 20 seconds (about 17 seconds behind real time) |
| NOAA river gauges | 847 gauges with current and forecast flood levels |
| NASA FIRMS | Wildfire hot spots |
| iNaturalist | Recent alligator and venomous snake sightings nearby |

It all boils down to two things the rest of the system uses:
1. **Can we work right now?** Two or more good lightning flashes within 10 miles stops outdoor work, and work resumes only after 30 minutes with no new flash. If the lightning feed goes quiet, we **never** give the all-clear, because silence might just be missing data.
2. **How likely is bad weather, and how hot will it feel?** Storm and rain chances per time slot, plus **WBGT**, the heat number safety rules use (temperature, humidity, sun and wind combined, computed with the Liljegren method from Argonne National Lab). These feed the forecaster and the planner.

### Tomorrow's risk score

A **0-10 danger score per site for tomorrow**, ready by 6 PM.
- **Trained on 7 years (2018-2024), 202,003 site-days** of NWS warnings, storm reports, storm damage records and OSHA heat injuries near each site.
- **Inputs:** only what's known by 6 PM the day before. A test scrambles everything after the cutoff and fails if any input changes.
- **Models:** gradient-boosted trees and logistic regression, blended. The trees catch complex patterns, and the regression keeps working in record heat the trees never saw.
- **Tested on 2023-2024 only,** and again with whole regions of Florida held out.
- **Ship rule:** it's only used for a hazard if it clearly beats the normal rate for that site and month.

| Held-out 2023-2024 | Normal rate | **Our model** |
| --- | --- | --- |
| Any warning | 0.29 | **0.71** |
| Heat advisory | 0.18 | **0.70** |
| Flood warning | 0.24 | **0.80** |
| Storm warning | 0.16 | **0.35** |
| Fire, heat injury | no clear gain | normal rate used |

(PR-AUC: how well it ranks risky days above safe ones, focused on rare events. Higher is better.)

Every feed saves its last good result. With no internet, the dashboard shows it marked "stale", and nothing crashes.

---

## Part 7: The planner

**Goal:** get the most work done while nobody overheats and nobody's outside in a storm.

### A puzzle solver
A crew of 6 over 16 half-hour slots, each picking from about 13 options, has around **10^107 possible schedules**, more than the atoms in the universe. We use Google's free **CP-SAT** solver (constraint programming), which never lists them. It crosses out impossible options instantly, drops hopeless branches early, and remembers dead ends so it never tries them twice. It finds a near-best schedule in seconds.

- **Hard rules:** everyone's predicted core temp (the high number) stays below 38.0 °C every minute, required shade breaks happen, no outdoor work during lightning or listed warnings, tasks get the right people and skills.
- **A points system:** points for work done (important tasks worth more), minus points for predicted heat, outdoor work when storms are likely, and giving one person all the heavy jobs.
- **Predict, then optimize:** for every schedule it considers, it predicts each worker's heat minute by minute with the same heat physics as our simulator, adjusted by their personal offset.

### AI proposes, code approves
Claude sits on top. It asks the solver for several candidate schedules, checks them, critiques them, combines the best parts, and explains the result in plain words ("moved transformer work to 7:30 because storms are 60% likely after 1 PM"). It also writes the 6 PM briefing for tomorrow and answers the crew lead's questions.

But **a code rule checker re-checks every plan before anyone sees it.** When Claude planned on its own, **92%** of its plans broke a safety rule. With the checker: **0%**.

### Adjusting during the day
Every minute, if someone's 20-minute forecast reaches 37.7 °C (just under the yellow line), the planner simulates a few fixes (break now, swap their heavy task with a cooler coworker, push it later) and picks the one that cools things most for the least lost work. Changes that can only make things safer happen automatically. Anything that costs work goes to the crew lead. That's our "AI coordinator, not AI manager" decision.

### Results on 12 held-out days

| | Safety violations | Work done | Heat alerts per day |
| --- | --- | --- | --- |
| How crews schedule today | 107 | 86% | 1.94 |
| Claude alone | in 92% of plans | 86% | 1.67 |
| Solver + rule checker | 0 | 68% | 0.19 |
| **Full planner** | **0** | 67% | **0.17** |

- Adjusting on the fly: **22% less** time above 38.0 °C, time above 38.5 °C down to **zero**, no work lost.
- Storm chances in the points: outdoor work in stormy slots dropped from **29 to 9** units with the same total work.
- The fixed schedule only "gets more done" because it ignores heat. That's the danger.

---

## Part 8: The self-improving agent

The planner's AI gets better every day, by itself, without code changes and without ever touching a safety rule. It practices on simulated days and is scored on work done, minus anyone who'd get a heat alert, minus how hot the hottest worker ran. Zero safety violations is required.

1. **It invents its own practice** (self-play curriculum, from the 2025 "Absolute Zero" research). Claude makes up hard days: heat waves, new hires, afternoon storms, short crews, tight deadlines. The agent tries each one 6 times, and a day is kept only if it sometimes wins and sometimes fails. **27 of 93** invented days made the cut.
2. **It writes its own playbook** (Agentic Context Engineering, ACE, Stanford 2025). After each day: plan, reflect on what went wrong, write a short lesson, and keep it only if the score improves on other days. Over 14 days, **3 of 18** proposed lessons survived. On a held-out heat-wave day, the agent used a lesson from day 2 to move a worker's 11:30 task, and his peak temperature dropped 0.18 °C with the same work done.
3. **It evolves its own strategy** (GEPA + the Darwin Gödel Machine, 2025). Claude reads where the agent failed and rewrites its strategy. We keep a family tree of different good versions, explore new branches, and pick winners only on days it never trained on. Score: **0.378 → 0.452**. The winner learned to care 5× more about peak heat and to give new hires lighter work early.
4. **The nightly loop** ties it together: learn from yesterday, update each worker's profile, test tomorrow's plan against 30 random versions of tomorrow (Monte Carlo), practice, and give the 6 PM briefing a risk number. Over 14 simulated nights with the same crew, the **risk number got better calibrated** (Brier score 0.140 → 0.105, lower is better: on day 14, "0-10% risk" days went wrong about 10% of the time, while day 1 over-warned), and the profiles picked up that one worker runs hot on moderate work. But the **plans themselves didn't get safer** on held-out days (workers alerted per day 0.24 → 0.27, work done down 11%). The loop kept 1 lesson and accepted 0 of 9 strategy edits. Honest result: it learned to *predict* tomorrow's risk better, not yet to *plan* better. Cost: $0.45.

| 12 held-out days | Violations | Work done | Heat alerts per day |
| --- | --- | --- | --- |
| Agent, day 1 | 0 | 67% | 0.17 |
| After 14 days of playbook learning | 0 | 67% | **0.11** |
| After strategy evolution + playbook | 0 | 61% | **0.03** |

The system itself is the big jump (107 violations to zero). Learning adds a real but smaller gain on top: alerts down another third with the same work. All of the learning work cost **$2.49** in Claude calls.

One trap we caught: the solver gave slightly different answers depending on how busy the laptop was, and our first loop "learned" improvements that were just that noise. We made the solver deterministic and re-ran everything.

---

## Part 9: Our own offline AI

Job sites often have no signal, and Claude needs the internet. Worker health data is also better off staying on the laptop. So we built our own small AI planner that runs locally, for free.

1. **Started from an open-source model:** Qwen3, the 1.7-billion-parameter version, shrunk to about **1 GB** (4-bit). Out of the box, **0%** of its schedules were even valid.
2. **Claude taught it** (knowledge distillation): Claude's planner solved **263 practice days** (never the test days, enforced by a content check), and we saved every question and answer.
3. **Only good answers kept** (STaR filtering): plans that passed the rule checker, got at least 30% of the work done, and scored at least as well as the plain solver. **224** days kept.
4. **Trained a small add-on** (LoRA): only about **1%** of the model's knobs, trained on the Mac's own chip in **98 minutes**. The result is a **70 MB** file.

| 12 held-out days | Valid schedules | Passes every safety rule alone | Inside our full system |
| --- | --- | --- | --- |
| Claude | 100% | 8% | 0 violations, score 0.435, $0.155 per plan |
| Qwen before training | 0% | 0% | 0 violations, score 0.425 |
| **Qwen after training** | 43% | 17% | **0 violations, score 0.431, $0** |

Inside our system it matches Claude for free. On its own it's not a planner (its passing plans rest people a lot), which is exactly why no model plans without the rule checker.

**Making it better (in progress), validation days:**

| Step | Valid | Passes every safety rule |
| --- | --- | --- |
| As trained | 79% | 22% |
| + Forcing the output format (constrained decoding) | **100%** | 22% |
| + Safety rules in its instructions | 100% | 37% |
| + A fix loop: the rule checker says what broke, it retries | 100% | **56%** |

Next: training it to *edit* the solver's plan instead of writing one from scratch, then one final score on the held-out days. Teaching data cost **$23.57**.

---

## Part 10: The emergency call

When a fall is confirmed or someone holds SOS:
1. The crew lead and safety officer get notified.
2. A **30-second cancel window** for false alarms (10 seconds in the quick demo).
3. An **AI voice agent** (ElevenLabs, through a Twilio phone number) calls and says:

> "Emergency. This is an automated call from PlanIT Control, a worker safety system. A worker needs help at Deerhaven Generating Station, GPS 29.75810 north, 82.38980 west. Worker: Alex Rivera. They collapsed, no movement for 115 seconds. It started at 12:40 am, 115 seconds ago. Last vitals: estimated core temperature 39.1 degrees Celsius, heart rate 142 beats per minute. Again, the location is Deerhaven Generating Station, GPS 29.75810 north, 82.38980 west."

Location first and repeated at the end, the way dispatchers want it. It answers follow-up questions only from the incident data, and says "Sorry, I don't know the answer" to anything else. It never guesses and never gives medical advice.

**It never calls 911.** Automated calls to 911 are restricted and tie up dispatchers. In the demo it calls a teammate's phone. 911, 112, 999, 988, 933 and short codes are blocked in code (we test 16 different ways of writing them), only allowlisted numbers can be dialed, there's one call per incident, and live calling is off unless explicitly switched on. A real product would route the 911 leg through a licensed emergency-dispatch provider.

We placed real test calls to our own phone. The first one failed because this Mac's Python couldn't verify HTTPS certificates, a bug that would also have silently broken live storm data and crew texts. Fixing it fixed all three.

---

## Part 11: How we built it

**Stack:** Python, FastAPI with WebSockets, SQLite, PyTorch, scikit-learn, NumPy/SciPy, pythermalcomfort, Google OR-Tools, the Claude API, Apple MLX, bleak (Bluetooth), pyserial; Next.js, Tailwind, Recharts and Leaflet for the dashboards; Arduino C++ on the band. ElevenLabs and Twilio for the call.

**The team:** our hardware team built and wired the band. On the software side, one founder ran the whole build with Claude Code: about a dozen parallel workstreams (live system, planner, site hazards, hazard model, emergency call, frontend, learning loops, curriculum, explainability, offline model, band sensors, end-to-end tests), each in its own branch, merged into main with the full test suite run after every merge.

**How we kept it honest:**
- **ML first.** We built and measured the models before the polished app, and every model had to beat a simple baseline before it shipped.
- **Fake data and replay first.** Every module runs and tests without hardware, so a dead cable never blocks anyone. If the band fails during judging, we flip to replay.
- **Acceptance tests written before the code was finished:** 36 backend checks and 24 frontend checks, turned into automated tests. They found **15 real bugs**, including a crash at startup with no internet, two SOS calls getting mixed up, and full phone numbers showing on the projector. All fixed.
- **674 automated tests:** 386 backend, 79 ML, 159 end-to-end (the whole system: band lines, alerts, privacy, calls, hazards, restarts, 6 workers × 8 hours of load), 50 frontend.
- **One locked test set, scored once.**
- **Every claim traceable** to a results file in the repo.

---

## Part 12: Key decisions

| Decision | Why |
| --- | --- |
| Code makes every safety call, AI only predicts and suggests | A learned model can be wrong in ways nobody checked. AI alone broke safety rules 92% of the time |
| Bluetooth heart rate, then a pulse sensor on the band | Job sites have no signal, and we couldn't order parts |
| Thermometer checks instead of heart-rate twins | We measured it: heart rate carries no information about the offset |
| AI coordinator, not AI manager | Safe changes are automatic, humans approve anything that costs work, accountability stays with a person |
| Calls a teammate, never 911 | Automated 911 calls are restricted and tie up dispatchers |
| Locked test set, scored once | We report 0.370, not the flattering 0.230 |
| Our own offline AI | No-signal sites and worker data privacy |
| Treadmill in layers, not a sauna, for field tests | A sauna (70-100 °C) is unsafe for people and for the batteries and electronics |
| Test research claims instead of copying them | A 2024 study suggested shorter work/rest cycles. In our planner it didn't hold up, so we kept the default |

---

## Part 13: What didn't work

| Tried | What happened | What we did |
| --- | --- | --- |
| Personal twins learned from heart rate | Forecasts got 40% worse | Switched to thermometer checks |
| Meta-learning twins | No gain | Dropped |
| Tuning the planner's point values | Better on practice days, 6% fewer tasks on test days | Kept the defaults |
| 20 min work / 10 min rest in hot hours (from a 2024 study) | 3% less time above yellow, but more above red (34 vs 8 worker-minutes) | Kept current breaks, 20/10 is an option |
| Small AI planning on its own | Safe only by resting everyone | Used only inside the rule-checked planner |
| Nightly learning loop, 14 simulated nights | Risk number got better calibrated, but plans didn't get safer and work dropped 11% | Use it for the 6 PM risk number, keep the planner as is |
| Self-training the small AI from its own plans | Only 3.6% of its plans were both safe and productive, too few to learn from | Switched to format forcing, editing and a fix loop |
| Red-alert catch rate | 9 of 10 vs 10 of 10 for a simple trend | Reported as is |

---

## Part 14: What's next

1. **Real-world data.** The band's own pulse recordings, treadmill tests in layers with thermometer checks, then a pilot with a real crew.
2. **The band alone.** The Kalman filter running on the band itself, so it works with no phone or laptop at all.
3. **Better personal fit** from real thermometer data, and acclimatization tracked over days.
4. **The nightly learning loop on real days,** not simulated ones, and learning time-of-day patterns (it missed a worker who only runs hot after lunch).
5. **A stronger offline model** (a 4-billion-parameter version, and reward-based training).
6. **Licensed emergency dispatch** for the 911 leg, and a license for the Army's patented ECTemp method (or our own replacement for it).

---

*Sources for every technique: [techniques.md](techniques.md). Results: [results.md](results.md) and the session notes in [sessions/](sessions/). Technical reference: [technical-overview.md](technical-overview.md).*
