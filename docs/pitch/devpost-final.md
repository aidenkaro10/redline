## Inspiration

Heat is the deadliest weather hazard in the US, and on job sites it's badly undercounted. The scary part is that the person overheating usually can't tell. By the time you feel dizzy, your core temperature is already high, and judgment is one of the first things to go. Most heat deaths at work happen in someone's first few days on the job, before their body adapts, and new workers are exactly the ones most likely to push through instead of speaking up.

Here in Florida, crews at solar farms, substations and power plants work in this every summer. Their tools today are a thermometer on the wall, a water cooler, and a supervisor's gut feeling. We wanted to give them something that actually sees it coming.

## What it does

A worker wears the Redline band. Every minute, the system estimates their core body temperature from their heart rate and forecasts where it's heading over the next 30 minutes.

- **Warns early.** If the forecast is heading toward danger, the band buzzes yellow about 15-20 minutes ahead. If they get there, it flashes red.
- **Plans the day.** A scheduler builds the crew's workday around tomorrow's heat and weather, and during the shift it adds breaks or swaps heavy tasks when someone starts running hot.
- **Watches the sky.** Live lightning from a NOAA satellite, river flood gauges, wildfires and weather warnings for every site. Lightning within 10 miles stops outdoor work.
- **Catches falls.** If the band detects a hard fall and the worker doesn't press the button within 20 seconds, an AI voice agent calls for help and reads out who it is and exactly where they are. Everyone else's band on the site buzzes too.
- **Works with no signal.** The band talks to the laptop over Bluetooth, and we trained our own small AI planner that runs offline.

The crew lead sees everyone as green, yellow or red. Never their heart rate or temperature. That stays private to the worker.

**The rule behind everything:** AI predicts and suggests. Plain code makes every safety decision. No alert threshold, safety limit or emergency call is ever decided by a learned model.

## How we built it

**The band.** An Adafruit ESP32-S3 Feather on a breadboard strapped to the forearm, with an accelerometer (from an Adafruit Prop-Maker FeatherWing), a diode we use as a skin thermometer, a photoresistor, a buzzer, a button and a status light. We wrote the firmware from scratch in Arduino C++. It streams readings to the laptop over Bluetooth Low Energy and runs off a phone charger. It reads the accelerometer 400 times a second, so it doesn't miss the short spike of a fall.

**Core temperature right now.** We implemented the US Army's ECTemp model, a Kalman filter that estimates core temperature from heart rate. It needs no training and works from minute one.

**The forecaster.** A GRU neural network that reads the last hour minute by minute and predicts the next 30 minutes as a low, likely and high range. We trained it on 9,000 simulated work shifts from the international heat-strain standard (ISO 7933), then fine-tuned on 65 real people from a published heat study who swallowed core temperature pills. Ten of those people were locked away the whole time and scored exactly once at the end. Five networks vote on every forecast (deep ensemble), and we calibrated the ranges so "90% sure" really holds 90% of the time (conformal prediction).

**Personalization.** Some people run hotter than others. We first tried learning each worker's quirks from their heart rate, and it made things worse. So we measured why, and switched to learning a personal offset from quick thermometer checks.

**The planner.** A crew's day has about $10^{107}$ possible schedules. Google's OR-Tools solver finds the most productive one that keeps everyone's predicted temperature under the warning line, and Claude explains the plan in plain words and writes the 6 PM briefing. A code rule checker re-checks every plan before anyone sees it.

**Self-improving agent.** The planner's AI invents its own practice days (self-play curriculum), writes a playbook of lessons from past days (Agentic Context Engineering), and evolves its own strategy (GEPA). A change only sticks if it beats the old version on days it never practiced on.

**Our own offline AI.** We distilled Claude into a 1 GB open-source model (Qwen3-1.7B, LoRA fine-tuning on a MacBook) so the planner still has an AI on sites with no internet. Then we pushed it further with constrained decoding and self-training on its own safe plans.

**Hazard forecasting.** A gradient-boosted model trained on 7 years and $202{,}003$ site-days of Florida weather warnings, storm reports and OSHA heat injuries predicts tomorrow's risk at 79 real power plants.

**The emergency call.** An ElevenLabs voice agent calls through Twilio, reads the location first and repeats it at the end, answers questions only from the facts it has, and says "Sorry, I don't know the answer" to anything else. It never calls 911. In the demo it calls a teammate's phone, and emergency numbers are blocked in code.

**Everything else.** A FastAPI backend streaming over WebSockets, a Next.js dashboard for the crew lead, the worker and the judges, and 729 automated tests, including end-to-end tests that run the whole system.

## Results

All measured on people, days or years the models never trained on:

- **Forecast:** off by $0.370\,^{\circ}\text{C}$ at 20 minutes on the 10 locked test people, versus $0.481\,^{\circ}\text{C}$ for the published Army method ($23\%$ better), with ranges that actually hold.
- **Warnings:** 88% of heat warnings caught early, 16.9 minutes ahead on average.
- **Planner:** on 12 held-out simulated days, Claude planning alone broke a safety rule in 92% of plans. With our rule checker: 0%. Adjusting during the day cut time spent overheating by 22% with no work lost (simulated).
- **Offline AI:** its own schedules passing every safety rule went from 17% to 68%, and its plan explanations had 0 made-up facts in 86 claims, same as Claude.
- **Hazard model:** $2$ to $4\times$ better than normal rates at predicting tomorrow's warnings.

We also report where we didn't win. A simple "temperature stays flat, with an honest range" baseline warned almost as early as our model (16.1 vs 16.9 minutes), so most of the early-warning benefit comes from forecasting with honest uncertainty at all. Our network's edge is accuracy.

## Challenges we ran into

- **No parts could be ordered.** Everything came from the hackathon's hardware lab. There were no Grove cables, so the Grove sensors we picked up couldn't connect, and we rebuilt around a Prop-Maker FeatherWing and a plain photoresistor.
- **No pulse sensor.** We tried reading a pulse through a fingertip with the photoresistor and a phone flashlight. It picked up a real 53 bpm pulse, then a perfectly steady fake 90 bpm showed up. That turned out to be the flashlight's invisible flicker being sampled too slowly. We fixed it in the firmware, but the signal was still too weak to trust. So for the live demo, heart rate is a clearly labeled demo input, and the real path is the Apple Watch over Bluetooth.
- **Our first fall test failed.** A hard drop was detected, then tiny sensor wobble kept cancelling the alarm. Now a real fall locks in, and only the worker's button press can stop the call.
- **The planner's AI alone wasn't safe.** It broke safety rules 92% of the time, which is why every plan goes through code first.
- **Results that looked better than they were.** Our model scored 0.230 on the people we used to pick it, and 0.370 on the locked test. We report 0.370.

## Accomplishments that we're proud of

- A full system working end to end, from a fall on the wrist to a phone ringing with the location.
- A forecaster that beats the published method on people it never saw, with honest error bars.
- Zero safety violations from the AI planner, because code has the final say.
- An offline AI that explains plans as accurately as Claude, for free, on a laptop.
- Testing our own ideas instead of trusting them: we tried research-backed work/rest cycles and tuned planner settings, and when they didn't hold up on test days, we kept the defaults and said so.

## What we learned

- Measure before you believe anything. Half our best ideas didn't survive a fair test, and finding out why led to better ones.
- Safety-critical systems need a clear line between what AI suggests and what code decides.
- Hardware never works the first time. A real compile, a real upload and a real drop test found bugs no simulation did.

## What's next

- A real pulse sensor on the band, and the Kalman filter running on the band itself, so it works with no phone or laptop.
- Real field data: treadmill tests in layers, then a pilot with a real crew.
- Learning from real shifts instead of simulated ones.
- Routing the emergency call through a licensed dispatch provider, and licensing (or replacing) the Army's patented ECTemp method.

## Built with

Python, PyTorch, scikit-learn, NumPy, SciPy, pythermalcomfort, Google OR-Tools, FastAPI, WebSockets, SQLite, Claude API, Apple MLX, Qwen3, bleak (Bluetooth LE), ElevenLabs, Twilio, Next.js, React, TypeScript, Tailwind CSS, Recharts, Leaflet, Arduino C++, ESP32-S3, Adafruit Feather, Adafruit Prop-Maker FeatherWing, NOAA GOES-19, National Weather Service API, Open-Meteo, NASA FIRMS, NOAA NWPS

**Code:** https://github.com/aidenkaro10/redline
