# PlanIT Control: Team Project Doc

Oct 17, 2026 · @Aiden

## Overview

We're building a wristband that forecasts heat strain in outdoor workers and buzzes them before they overheat. It estimates core body temperature live from heart rate, predicts where it's headed 10 to 30 minutes ahead, and alerts the worker and their crew lead.

**The problem:** heat illness among outdoor workers is badly undercounted, and the person overheating often can't tell. Florida summers make this a real, local risk.

**Where we're entering:** SwampHacks XII, Sperry Tech PlanIT Control track. We may also qualify for a hardware prize and the Best Overall prize. Judging is expo-style: judges come to our table and watch a live demo.

**Why it can win:** last year's Best Overall winners combined custom ML, a clear human story and a polished live demo. One was a wearable with almost the same architecture as ours.

## MVP and priorities

The MVP is the demo that works even if nothing else gets built: heart rate streaming to the laptop, a live core temperature estimate, a dashboard chart, and a buzz plus LED alert when it crosses the threshold. Replay mode is part of the MVP as the backup.

After the MVP, build in this order:

1. Hazard alerts from the National Weather Service, with distinct buzz and LED patterns
2. Man-down detection
3. Neural network forecaster (10, 20 and 30 minutes ahead)
4. Skin temperature as a model input
5. Stretch: sweat sensing, personal twins, planning agent

Don't start a stretch feature until everything above it works.

## Hardware

The band runs on an Adafruit ESP32-S3 Feather, which is wireless and has a built-in battery charger. The Arduino Uno is the backup board. Heart rate comes from an Apple Watch through the Pulsoid app, not a sensor on the band.

&#91;embedded content: concept sketch · the band on a forearm\]

The Feather, buzzer, button and tilt switch sit on a mini breadboard strapped to the forearm (the hardware team calls the built device the "Crew Beacon"). Status uses the Feather's built-in NeoPixel LED; the LED strip is not used (its stubs need soldering). The diode is taped against the skin under the strap, and the Apple Watch stays on the wrist for heart rate. When we get the Grove accelerometer, it replaces the tilt switch.

**What we have**

| Part | Job | Where it came from |
| --- | --- | --- |
| Adafruit ESP32-S3 Feather (headers soldered) | Main board, WiFi | CpE Design Lab |
| Arduino Uno + cable | Backup board | Ours |
| APA102 LED strip, 5 LEDs | Not used in the build (needs soldering); the built-in NeoPixel shows status | Lab |
| Half-size breadboard | Prototyping | Lab |
| Push button | Short press: acknowledge alert / cancel man-down. Hold 3 s: SOS | Lab |
| Buzzer | Alert sounds | Lab |
| 2 tilt switches | Man-down detection until we get an accelerometer | Lab |
| 2 small glass diodes (1N4148) | DIY skin temperature sensor | Lab |
| 10kΩ and 100Ω resistors | Temp sensor and buzzer | Lab |
| Jumper wires | Connections | Lab |

**Still to get**

- [ ] More jumper wires (about 30 total)
- [ ] USB-C data cable for the Feather
- [ ] Electrical tape and a velcro strap for mounting
- [ ] Small LiPo battery (check plug polarity with lab staff) or a USB power bank
- [ ] Pulsoid set up on the Apple Watch and iPhone
- [ ] At the hackathon: Grove 3-Axis Accelerometer from the MLH hardware lab

**Wiring to the Feather (as built, Oct 17; full checklist for the beacon program: `docs/firmware-contract.md`)**

| Part | Connection |
| --- | --- |
| Status light | Built-in NeoPixel (no wiring) |
| Passive buzzer | pin 5 → buzzer → GND, driven with `tone(5, 2000)` |
| Button | pin 6 → button → GND (INPUT\_PULLUP; LOW = pressed) |
| Tilt switch | pin 9 → switch → GND (HIGH = tipped) |
| Diode temp sensor | 3V → 10kΩ → diode → GND (striped end to GND); read the resistor-diode junction on A5, average 64 readings |
| Accelerometer (later) | VCC → 3V, GND → GND, SDA → SDA, SCL → SCL, or the STEMMA QT port |

Use A5 for the temperature sensor: A0 to A4 stop working as analog inputs while WiFi is on. Check pins against Adafruit's Feather ESP32-S3 pinout before wiring. Calibrate the diode against a real thermometer at two temperatures; it changes about 2 mV per °C.

**Firmware**

Arduino C++ in the Arduino IDE or PlatformIO, using each part's existing library: FastLED (set to APA102) or Adafruit DotStar for the LED strip, and the Adafruit or Grove library for the accelerometer once we have one. Board setting in the IDE: Adafruit Feather ESP32-S3 2MB PSRAM. If an upload fails, hold BOOT, tap RST, release BOOT and retry.

The band sends one text line per reading and listens for command lines. Over USB this is serial at 115200 baud; over WiFi the same lines go through a WebSocket or UDP to the laptop. The ESP32 only joins 2.4 GHz networks, so turn on Maximize Compatibility on an iPhone hotspot.

| Direction | Example line | Meaning |
| --- | --- | --- |
| Band → laptop | TEMP:34.62 | Skin temperature, °C (from the calibrated diode) |
| Band → laptop | TILT:1,ACT:7 | Tilt switch state and activity (switch flickers per second) |
| Band → laptop | ACC:0.98,0.02,0.11 | Accelerometer x,y,z in g (once we have one) |
| Band → laptop | BTN:1 | Short press: acknowledge alert / cancel man-down ("I'm OK") |
| Band → laptop | BTN:SOS | Button held 3 s: SOS, starts emergency escalation right away |
| Laptop → band | BUZZ:HEAT\_YELLOW | Heat warning pattern |
| Laptop → band | BUZZ:HEAT\_RED | Heat danger pattern |
| Laptop → band | BUZZ:LIGHTNING | Lightning pattern |
| Laptop → band | BUZZ:FLOOD\_FIRE | Flood or fire weather pattern |
| Laptop → band | BUZZ:MANDOWN | Man-down check |
| Laptop → band | BUZZ:OFF | Clear alerts |

Heart rate does not come from the band. A laptop script reads it from Pulsoid's real-time WebSocket (wss://dev.pulsoid.net/api/v1/data/real\_time with an access token). Each message has a timestamp and beats per minute; Apple Watch readings arrive about every 5 seconds. Start a workout on the watch so it measures continuously, and keep the phone on a hotspot rather than venue WiFi.

## System architecture

&#91;embedded content: system architecture · inputs, laptop, outputs\]

The band and the watch send readings to the laptop. The laptop estimates and forecasts core temperature, updates both dashboards, and sends alert commands back to the band. Everything uses one message format: worker\_id, timestamp, hr, skin\_temp, tilt, activity. The band sends text lines like HR:82,TEMP:34.6 and listens for commands like BUZZ:HEAT\_RED.

**Backend details**

- **Ingestion:** pyserial reads the band over USB; a WebSocket or UDP listener reads it over WiFi; a Pulsoid WebSocket client reads heart rate. bleak can read a Bluetooth heart rate strap if we ever use one. Timestamp everything on the laptop as it arrives so all signals line up.
- **Data contract:** agree on one message format before building anything, so every piece snaps together: worker\_id, timestamp, hr, skin\_temp, tilt, activity, accel (optional). Downstream code gets one-minute averages for the models.
- **API server:** FastAPI (Python), with WebSockets to stream live data to the frontend.
- **Storage:** SQLite for speed, or Supabase (Postgres) if we want it hosted. Tables: readings, workers, alerts, plans.
- **Hazard feeds (required by the track brief):** the app pulls live hazard APIs for every job site and reports them on the dashboard, the band and the 6 PM briefing. See "Site hazard layer" below and `docs/data-sources.md`.
  - Weather and extreme heat: NWS active alerts (free, no key, needs a User-Agent) plus Open-Meteo for temperature, humidity, solar radiation, wind and soil moisture. Compute WBGT with the Liljegren method (the NWS hourly forecast has no solar radiation).
  - Lightning: GOES-19 satellite lightning flashes, for a 10-mile stop-work rule with a 30-minute all-clear countdown.
  - Flooding: NWS river gauges (NWPS) and flood alerts, NOAA Tides & Currents for coastal sites.
  - Wildfire: NASA FIRMS detections, NIFC incidents, Red Flag Warnings.
  - Soil saturation: Open-Meteo soil moisture. Wildlife: iNaturalist plus crew photo reports.
- **Alert router:** decides which pattern to send to the band and who to notify (the worker, the crew lead, nearby crewmates).
- **Replay mode:** streams recorded or synthetic data as if it were live, including a speed-up factor (for example 10x). If hardware fails during judging, flip to this. Build it in the first few hours.

## The ML

Two models: a Kalman filter that estimates core temperature right now (the MVP and our baseline), and a neural network that forecasts it 10 to 30 minutes ahead and has to beat that baseline.

**Kalman filter (ECTemp, MVP)**

The U.S. Army's ECTemp model, written in NumPy. Core temperature is the hidden state, and one-minute average heart rate is a noisy measurement of it. Each minute:

```latex
\begin{aligned}
&\text{Predict: } CT^- = CT_{prev}, \quad v^- = v_{prev} + \gamma^2 \\
&\text{Observe: } h(CT) = b_2 CT^2 + b_1 CT + b_0, \quad c = 2 b_2 CT^- + b_1 \\
&\text{Update: } K = \frac{v^- c}{c^2 v^- + \sigma^2}, \quad CT = CT^- + K\,(HR - h(CT^-)), \quad v = (1 - Kc)\,v^-
\end{aligned}
```

Published parameters (verify against Buller et al., 2013): γ = 0.022, σ = 18.88, b2 = −4.5714, b1 = 384.4286, b0 = −7887.1, starting core temperature 37.1 °C.

**Neural network forecaster**

- Model: sequence-to-sequence GRU in PyTorch, 2 layers, 64 hidden units. Trains in minutes on a laptop CPU.
- Inputs: the last 30 to 60 minutes at one-minute resolution: heart rate and its rate of change, skin temperature, activity, WBGT (heat index from weather), the Kalman estimate and its variance, time into shift.
- Outputs: core temperature 10, 20 and 30 minutes ahead at the 10th, 50th and 90th percentiles, so every forecast has an uncertainty band.
- Loss: quantile (pinball) loss.
- Training data: synthetic shifts simulated with pythermalcomfort, then real public datasets with measured core temperature for fine-tuning and testing.
- Split train and test by person, never by time, to avoid leakage.

The model has tens of thousands of parameters. The decoder also receives future known inputs: the forecast WBGT and the planned task's workload. The quantile loss for each percentile q:

```latex
L_q = \max\big(q\,(y - \hat{y}),\; (q - 1)\,(y - \hat{y})\big)
```

**Residual correction network (stretch)**

A second small GRU that corrects the Kalman estimate using extra signals. Its loss has three parts:

1. Gaussian negative log-likelihood (nn.GaussianNLLLoss), so it outputs honest uncertainty.
2. An asymmetric safety penalty, since underestimating core temperature is more dangerous than overestimating. Weighted heavier when the true temperature is above 38 °C.
3. A physiology smoothness penalty, since core temperature can't jump fast.

```latex
\begin{aligned}
&\text{Safety: } \lambda_1 \cdot w \cdot \max(0,\, y - \hat{y})^2 \\
&\text{Smoothness: } \lambda_2 \cdot \max(0,\, |\Delta \hat{y}| - r_{max})^2
\end{aligned}
```

**Training data**

- Synthetic: simulate thousands of shift-hours with pythermalcomfort (ISO 7933 Predicted Heat Strain and two-node models), which output core temperature and sweat loss. Add a simple heart rate model on top: resting HR + workload + core temperature rise + noise. Randomize body size, fitness, acclimatization, clothing, tasks, and real Gainesville summer weather.
- Real: public datasets with measured core temperature (for example the Mendeley firefighter dataset and the Abertay heat acclimation dataset) for fine-tuning and testing.
- Track runs with Weights & Biases. A laptop CPU is enough; free Colab or Kaggle GPUs are optional.

**How we measure it**

Automate these checks and always report the network next to the Kalman baseline:

- Estimation: RMSE, bias, Bland-Altman limits of agreement.
- Alerts: share of dangerous episodes caught, average warning lead time, false alarms per hour.
- Plots of predicted against real core temperature on held-out people.
- Flag anything suspiciously better than published research; it usually means a bug.

## Alerts

Each hazard gets its own buzz and LED pattern, so the worker knows what's wrong without looking at a phone. Heat alerts use the forecast's 90th percentile, so the system leans cautious. Until the forecaster works, alert on the Kalman estimate alone.

| Event | Trigger | Buzz | LED |
| --- | --- | --- | --- |
| All clear | No active alert | Silent | Solid green |
| Heat warning | 20-min forecast (90th percentile) crosses 38.0 °C | One long buzz | Solid yellow |
| Heat danger | Current estimate or 10-min forecast crosses 38.5 °C | Long buzz every 10 s until a short press | Flashing red |
| Lightning | GOES-19 lightning within 10 miles (2+ good-quality flashes); stays on until 30 min with no strikes | Rapid pulses | Rapid white flashes |
| Flood or fire weather | Active NWS alert at the site | Two short buzzes | Solid blue |
| Man-down check | Band tips over, then no movement for 30–60 s | Continuous buzz; short press or movement cancels | Red breathing pulse |
| SOS | Worker holds the button 3 s | 3 beeps | Blinking purple; starts emergency escalation immediately |

If a man-down check isn't cancelled within a minute, the crew lead and nearby crewmates get an alert on the dashboard. Thresholds are configurable; safety limits are hard-coded and never learned.

Where the heat lines come from: ACGIH's heat stress TLV aims to keep core temperature below 38.0 °C for workers not used to heat and 38.5 °C for acclimatized workers; the 38.5 limit leaves a margin for measurement error and time to cool down, and sustained core temps above 39 °C signal losing the ability to cope (secondary sources quoting ACGIH; the primary ACGIH documentation is paywalled). For context: heat exhaustion usually happens at about 38-40 °C, and heat stroke is usually defined as core temp above 40 °C (some use 40.5-40.6) plus confusion, seizures or collapse (Bouchama and Knochel, 2002).

## Man-down detection

Man-down fires when the worker appears to have fallen or stopped responding. We start with a rule and upgrade to ML if time allows.

- **Version 1, tilt switch (what we have now):** the switch flickers rapidly while the wearer moves, so flickers per second give an activity level. A fall = the switch flips to the tipped position, then no flickers for 30 to 60 seconds.
- **Version 1, accelerometer (once we get the Grove one):** a sharp acceleration spike, then near-zero movement for 30 to 60 seconds.
- **Also trigger** on a red heat alert with no button press within a minute.
- **Version 2 (ML):** a small classifier on accelerometer windows, trained on recorded falls, sits and normal movement from teammates.

**On trigger:** buzz the worker first with the continuous man-down pattern. Any movement or a short button press cancels it. A 3-second hold (SOS) skips the wait and escalates right away. If it isn't cancelled, alert the crew lead and nearby crewmates with the worker's location, then start emergency escalation (below).

## Emergency escalation (confirmed man-down)

When a man-down is **confirmed** (no cancel within a minute, see above), an AI voice agent built on ElevenLabs places a phone call and reads out a short, factual emergency report: worker name, site name and GPS location, what happened ("collapsed, no movement for 90 seconds"), last vitals (estimated core temp, heart rate) and how long ago it started. It can answer simple follow-up questions from the person on the line ("Is he breathing?" → "Unknown. Last movement was at 2:14 pm").

Rules:
- **The decision to call is made by code, never by the LLM.** Fixed rule: confirmed man-down → call. The voice agent only delivers the report.
- **Call order:** crew lead and site safety officer first (phone + dashboard), then emergency services. A crew lead can press "cancel, false alarm" within a short window.
- **Never call real 911 from the prototype.** Automated or prerecorded calls to 911 are restricted by law, and test calls tie up dispatchers. For the demo, the agent calls a teammate's phone acting as the dispatcher, and the screen says "Demo: would call 911".
- **Real deployment:** route the 911 leg through a licensed emergency-dispatch provider that connects to 911 call centers with location data, instead of dialing 911 directly.
- Every call is logged with time, number, transcript and outcome.

## Site hazard layer (track brief)

The track brief asks for a site layer on top of the worker layer. Full source list and gotchas: `docs/data-sources.md`.

- **Job sites:** 50+ real Florida power plants from EIA-860M (public domain), converted once to `plants.geojson`. Mix gas, nuclear and solar.
- **Live hazards per site:** heat, lightning (10-mile rule, 30-min countdown), flooding, wildfire, soil saturation, wildlife (see Hazard feeds above). Each hazard has a status, a source, a timestamp and a band/LED pattern.
- **0-10 hazard index:** an ML model scoring each site for tomorrow, using only what's known by 6 PM the day before. Labels: NWS warnings that covered the site (IEM archive), Storm Events, Local Storm Reports, OSHA heat and fall injuries. Split by time, judged by PR-AUC.
- **Risk-ranked map:** sites ranked by the index, flagged when a worker on site has a heat alert or man-down.
- **6 PM briefing:** tomorrow's index and hazards per site, forecast windows, past incidents nearby, and the planner's suggested schedule.
- **Crew photo reports:** workers snap a photo with a note (snake, standing water, downed line). Shows on the map.
- **Reporting:** every hazard event is logged with source and time, so the site has a record of what was reported and when.

## Frontend

Next.js + Tailwind, Recharts for live charts, Leaflet for maps, live updates over WebSockets from FastAPI. If the frontend is fighting us by hour 12, fall back to Streamlit (pure Python).

- **Crew lead dashboard:** each worker's status (green, yellow, red), live core temperature with the forecast band shaded, a hazard panel with active NWS alerts on a job site map, and the day's schedule.
- **Man-down screen:** full-screen alert with the worker's name, location, time since the fall, and last vitals.
- **Worker view:** a mobile-friendly page with a giant status color, the current hazard, and a break countdown ("back to work in 6 min").
- **Demo controls:** buttons to trigger a test NWS alert, switch to replay mode, and set the replay speed.
- **Privacy:** crew leads see status only, never raw biometrics.

## Stretch features

Build these only after the MVP, hazard alerts, man-down and the forecaster all work.

**Sweat loss estimation**

- Hardware: a DHT22 humidity sensor inside a small sealed cap pressed against skin. Rising humidity inside the cap shows local sweat rate. We don't have this part yet.
- The Predicted Heat Strain model gives expected whole-body sweat loss from workload and weather. Each worker's twin learns how their measured sweat compares, for example "Jake sweats about 40% more than predicted."
- A drop in sweating while estimated core temperature keeps rising is flagged as a warning sign.
- Output stays general and honest, with an uncertainty range: "Estimated 1.2 L lost this morning. Drink water, and consider electrolytes since you're sweating heavily." No specific doses or minerals; workers with medical conditions follow their doctor's advice.

**Personal twins**

- Each worker has a learned embedding (8 to 16 numbers) fed into the networks. New workers start at the population average.
- After each shift, fine-tune only that worker's embedding on a self-supervised target: predicting their next 10 minutes of heart rate, which is observable.
- An acclimatization state rises with daily heat exposure and decays on days off.
- For the demo, simulate two weeks of history and show prediction error dropping day by day.

```latex
L_{adapt} = \text{MSE}(HR_{pred}, HR_{actual}) + \beta \, \lVert e - e_{population} \rVert^2
```

**Guardrails:** safety thresholds are hard-coded and never learned, updates are capped, uncertain predictions lean cautious, and ignored alerts never teach the system that someone "can handle more."

**Planning agent**

- Google OR-Tools (CP-SAT) schedules tasks and breaks in 30-minute slots, keeping everyone's predicted 90th percentile under the limit and avoiding NWS hazard windows.
- The Claude API with tool use (get\_forecast, get\_crew\_status, run\_optimizer, simulate\_schedule) powers a chat interface and a morning briefing.
- The LLM never makes safety calls itself.

## Team roles and build order

Four roles, one owner each. Fill in names below.

| Role | Owns | Name |
| --- | --- | --- |
| Hardware | Sensors, wiring, firmware, buzz and LED patterns, mounting |  |
| Backend and data | Ingestion, FastAPI server, storage, NWS feed, replay mode, man-down logic |  |
| ML | Kalman filter, synthetic data, neural network, evaluation |  |
| Frontend and pitch | Dashboard, worker view, demo script, slides, Devpost |  |

**Build order (36 hours)**

1. Hours 0–3: fake data running end to end (fake data → dummy model → basic dashboard → buzz). Heart rate printing on the laptop.
2. Hours 3–10: real sensors in, Kalman filter live, replay mode, heat alerts with buzz and LEDs.
3. Hours 10–16: NWS hazard feed and patterns, man-down detection, synthetic data and first forecaster.
4. Hours 16–22: sleep in shifts.
5. Hours 22–30: forecaster on the dashboard, skin temperature input, stretch features if time allows.
6. Hours 30–36: freeze features, rehearse the demo, write the Devpost and slides.

**Before the hackathon:** get every part working on its own at home, and set up Pulsoid. Anything we prove now is one less thing to debug at 2 a.m.

## Demo plan

The demo runs at our table for each judge who walks up, so it has to work in about 3 minutes, every time.

1. Open with the problem: heat illness among workers is badly undercounted, and the person overheating often can't tell.
2. A teammate wears the band and the Apple Watch. The dashboard shows live heart rate, skin temperature and estimated core temperature.
3. They exercise. The forecast band climbs toward the danger line, and the band buzzes and turns yellow, then red, before they reach it.
4. Trigger a thunderstorm alert. The band switches to rapid pulses and white flashes, and the map updates.
5. The teammate "collapses." Every screen lights up with a man-down alert within seconds.
6. Close with results: our model vs. the Kalman baseline on held-out people.

**The slow-rise problem:** with real heart rate, core temperature takes about 15 to 20 minutes of hard exercise to reach 38.5 °C. A judge won't wait that long. Plan for it:

- Use a clearly labeled demo threshold (for example 37.5 °C) and say so out loud.
- Pre-record a real exercise session and play it back in replay mode at 10x speed, then show a short live segment.
- Lean on the forecaster: a 20-minute-ahead alert fires much earlier, which is exactly the pitch.

If hardware fails during judging, flip to replay mode and keep going.

## To-do and open questions

**This week**

- [ ] Upload Blink to the Feather (board: Adafruit Feather ESP32-S3 2MB PSRAM)
- [ ] Connect the Feather to a phone hotspot over WiFi (2.4 GHz only)
- [ ] Test each part on its own: LED strip, button, buzzer, tilt switch, diode temperature sensor
- [ ] Calibrate the diode against a real thermometer
- [ ] Set up Pulsoid and stream heart rate to a laptop script
- [ ] Write the Kalman filter and test it on recorded heart rate
- [ ] Record one real exercise session for replay mode

**Open questions**

- [ ] Confirm the SwampHacks XII dates, tracks and prize list
- [ ] Ask the organizers what the MLH hardware lab will stock
- [ ] Check whether Pulsoid's free plan includes API access
- [ ] Assign the four roles

## Notes for Claude Code

This section is the brief for starting a Claude Code session. Everything above is the spec; this is how to build it.

**Suggested repo layout**

```text
planit-control/
  firmware/band/          band.ino, config.h (pins, WiFi, thresholds)
  firmware/tests/         one small sketch per part: led, button, buzzer, tilt, diode_temp
  backend/app/            main.py (FastAPI + WebSockets), schemas.py (data contract)
  backend/ingest/         serial_reader.py, wifi_listener.py, pulsoid_client.py
  backend/models/         kalman.py (ECTemp), forecaster.py (GRU inference)
  backend/hazards/        nws.py, wbgt.py (Liljegren)
  backend/alerts/         router.py, mandown.py
  backend/emergency/      escalation.py (call order, cancel window), voice_agent.py (ElevenLabs + phone call)
  backend/planner/        rule checker, optimizer, Claude agent, 6 PM briefing
  backend/sites/          plants.geojson (50+ Florida power plants), site hazard feeds, 0-10 hazard index
  backend/replay/         replay.py (recorded or synthetic data, speed factor)
  backend/storage/        db.py (SQLite: readings, workers, alerts, plans)
  ml/                     simulate.py (pythermalcomfort), train.py, evaluate.py
  frontend/               Next.js + Tailwind + Recharts + Leaflet
  data/recordings/        recorded sessions for replay mode
```

**Hardware facts to respect**

- Board: Adafruit ESP32-S3 Feather, 3.3V logic. Sensors on 3V, LED strip power on the USB pin (5V).
- Pins: LED strip data D11 and clock D12; buzzer D9 (through 100Ω); button D10; tilt switch D5; diode temperature on A5. Button and tilt use INPUT\_PULLUP.
- A0 to A4 are on ADC2 and don't read while WiFi is on. Analog inputs must use ADC1 pins (A5, D5, D6, D9, D10). Verify against Adafruit's pinout.
- Cap LED brightness around 25% to keep current draw low on USB power.
- The diode sensor reads about −2 mV per °C; store a two-point calibration in config.h.

**Specs to implement exactly**

- The line protocol table in the Hardware section, at 115200 baud over USB.
- The data contract fields in Backend details, with laptop-side timestamps.
- The Kalman equations and published parameters in The ML (verify against Buller et al., 2013).
- Alert thresholds from the Alerts table, as named constants in one config file.
- NWS API calls need a User-Agent header. Job site default: Gainesville, FL.

**Rules**

- Build in the MVP order. Replay mode and a fake data generator come first, so every module runs without hardware.
- Every piece should be testable on its own with fake or recorded data.
- Safety thresholds are hard-coded constants, never learned. The LLM never makes safety calls.
- Always report model results next to the Kalman baseline, split by person.
- Keep the demo controls (test alert, replay toggle, demo threshold) one click away on the dashboard.

**First tasks**

1. Scaffold the repo and the data contract.
2. Fake heart rate generator → Kalman filter → FastAPI WebSocket → a bare dashboard chart → BUZZ command printed to the console.
3. Replay mode with a speed factor.
4. Firmware test sketches for each part, then band.ino with the line protocol.
5. Pulsoid client streaming real heart rate into the same pipeline.

**Licensing note:** the Army's ECTemp method is patented. That's fine for a hackathon; talk to UF's technology licensing office before turning this into a product.
