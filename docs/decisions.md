# Key decisions: how we adapted to real constraints

Each entry: what we planned, what got in the way, what we chose and why. Good material for the Devpost "challenges" section and for judges' questions.

## 1. Heart rate that works with no internet (2026-10-17)

- **Planned:** Apple Watch heart rate through the Pulsoid app.
- **Problem:** Pulsoid goes through the internet (watch -> iPhone -> Pulsoid cloud -> laptop). Many remote job sites (solar farms, substations) have no WiFi or signal, so heart rate, and with it the whole core temp system, would stop. A safety device can't depend on signal. We also couldn't order a pulse sensor for the band in time.
- **Chose:** read heart rate over **local Bluetooth**. The HeartCast app relays the Apple Watch's heart rate from the iPhone as a standard Bluetooth heart rate sensor, and the laptop reads it directly (Python `bleak`, standard BLE Heart Rate profile). No internet, no API, no cost (check the app's free version). The same input works with any Bluetooth chest strap or a Garmin watch's built-in broadcast.
- **Kept:** Pulsoid as an optional online second source. Sensor fusion (`ECTempKalman.step_fused`) combines whatever heart rate sources are present and keeps going if one drops.
- **Pitch line:** "Works with the watch your crew already owns, and keeps working where there's no signal."
- **Next:** a pulse sensor on the band (generic MAX30102) and the Kalman filter running on the band itself, so the band alone works with no phone, laptop or WiFi.

## 2. Personal twins learn from thermometer checks, not heart rate (2026-10-17)

- **Planned:** each worker's twin learns from their heart rate (observable live).
- **Problem:** tested on real people, it made core temp forecasts worse (0.30 -> 0.42 °C error). Diagnosis: most error is a steady per-person offset that heart rate can't reveal (correlation -0.02). Meta-learning (CAVIA) removed the harm but gave no gain.
- **Chose:** learn each worker's offset from quick ear or oral thermometer spot checks (a signal aligned with what we predict). On unseen real people: +20 min error 0.308 -> 0.270 °C.

## 3. The emergency agent calls a teammate, never 911 (2026-10-17)

- **Planned:** an AI voice agent calls 911 on a confirmed man-down.
- **Problem:** automated calls to 911 are legally restricted and tie up dispatchers.
- **Chose:** the ElevenLabs agent calls an allowlisted test phone in the demo ("Demo: would call 911"), with 911 and other emergency numbers blocked in code. A real product routes the 911 leg through a licensed emergency-dispatch provider.

## 4. The AI plans, the code approves (2026-10-17)

- **Problem:** an AI planning alone broke safety rules in 92% of plans on our held-out scenarios.
- **Chose:** every plan from any model passes a code rule checker (0 violations). The AI proposes, suggests and explains; heat alerts, man-down and emergency calls stay pure code.

## 5. AI coordinator, not AI manager (2026-10-17)

- **Considered:** an AI that replaces the crew lead.
- **Chose:** an always-on AI that makes safer-only changes on its own (extra breaks, earlier heavy work) and asks the human for anything that costs productivity. Accountability and trust stay with a person.

## 6. Single-room data protocol from the real study (2026-10-17)

- **Problem:** the public dataset had no weather or workload columns.
- **Chose:** read the study's full methods (40 °C vs 22 °C at 40% RH, 5.31 km/h at 4% grade, CorTemp pill, Polar H7) and filled those inputs from the protocol, correcting an earlier assumption (5 km/h, 2%) after checking the paper.

## 7. Field testing: treadmill in layers, not a sauna (2026-10-17)

- **Problem:** we wanted our own heat data, but it's cool outside and a sauna (70-100 °C) is unsafe for people and gear (LiPo batteries, the ESP32's 85 °C limit, the watch's ~35 °C limit).
- **Chose:** indoor treadmill or stairs in extra layers, with stop rules (thermometer 38.0 °C, heart rate >= 180 minus age, any symptoms), never alone, batteries kept out of the heat.
