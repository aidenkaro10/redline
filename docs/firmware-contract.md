# Crew Beacon: firmware checklist

For the hardware team. The wiring is done and tested (nice). This page is what the **beacon program** has to do so it plugs straight into the laptop software. If the band follows this exactly, everything snaps together on the day.

## The big idea

- **The band reports. The laptop decides.** The band sends sensor lines and obeys commands. It doesn't decide heat status, man-down or lightning on its own. The laptop has the heart rate, the forecast and the weather.
- **One exception:** button feedback (a tiny blink so the worker knows the press registered).

## Pins (as built, Oct 17)

| Part | Pin | Reads as |
| --- | --- | --- |
| Tilt switch | 9 | LOW = upright, HIGH = tipped (ball rolls off) |
| Button | 6 (INPUT_PULLUP) | LOW = pressed |
| Diode temp sensor | A5 | ~0.5-0.65 V, about -2 mV per °C |
| Passive buzzer | 5 | `tone(5, 2000)` to beep, `noTone(5)` to stop |
| Status light | built-in NeoPixel | any color; cap brightness around 25% |

Keep A5 for the sensor: A0-A4 stop working while WiFi is on.

## 1. What the band sends (one line each, ending in a newline, 115200 baud)

| When | Line | Notes |
| --- | --- | --- |
| Every 5 s | `TEMP:34.62` | Skin temp in °C, 2 decimals. Average your 64 readings, then apply the two-point calibration (room temp + ice water). Put the two calibration numbers in `config.h`. |
| Every 1 s | `TILT:0,ACT:7` | TILT: 1 = tipped, 0 = upright. ACT = how many times the tilt switch changed state in the last second (it flickers while the wearer moves, which is our activity level). |
| Short press (under 1 s) | `BTN:1` | "I'm OK": acknowledges an alert or cancels a man-down. |
| Hold 3 s | `BTN:SOS` | Worker asks for help. Send it once when the 3 s is reached, not again until released. |

Rules:
- Exactly this format: capital letters, a colon, no spaces, no units. Anything else gets ignored as noise.
- Debounce the button (~30 ms).
- Extra debug prints are fine, but put them on lines starting with `#` so they're clearly not data.

## 2. Commands the band must obey

The laptop sends one line, like `BUZZ:HEAT_RED`. The band shows that pattern until a new command arrives. Every alert must look and sound different, because there's only one light:

| Command | Light | Sound |
| --- | --- | --- |
| `BUZZ:OFF` | Solid green | Silent |
| `BUZZ:HEAT_YELLOW` | Solid yellow | One long beep (1 s) |
| `BUZZ:HEAT_RED` | Flashing red (2 per second) | Long beep every 10 s **until a short press**. After the press, keep flashing red but stop beeping (and still send `BTN:1`) |
| `BUZZ:LIGHTNING` | Rapid white flashes (5 per second) | Rapid short beeps, repeat every 10 s |
| `BUZZ:FLOOD_FIRE` | Solid blue | Two short beeps |
| `BUZZ:MANDOWN` | Red "breathing" pulse (fade in and out) | Continuous beeping until a short press (then stop beeping and send `BTN:1`) |

Button feedback (the band does this itself, on top of whatever pattern is showing):
- Short press: one quick white blink.
- 3 s hold (SOS): blinking purple + 3 beeps, then return to the current pattern.

No connection: if the band hasn't received any command for 60 s, blink cyan slowly so we can spot a dead cable during the demo. (The laptop re-sends the current status every 20 s so this never triggers when things are fine.)

## 2b. New parts: pulse, accelerometer, air sensor (added Oct 17)

Goal: the band measures heart rate itself, so no Apple Watch is needed. The band sends raw numbers fast and evenly spaced; the laptop does the hard part (finding heartbeats, throwing out arm movement, spotting falls). Step-by-step wiring, code and tests: `docs/band-finish-guide.md`.

**Wiring (all sensors on 3V, never 5V)**
- Light sensor signal: **D10** (an ADC1 pin, so it keeps working with WiFi on; A0-A4 don't). Pulse LED: **D12**, held on.
- Accelerometer and air sensor (BME280/BMP280): the I2C pins **SDA/SCL**, sharing the same two wires.

**New lines**

| When | Line | Notes |
| --- | --- | --- |
| Every 200 ms | `PPG:512,515,520,518,511,507,509,514,519,516` | 10 raw `analogRead` values taken exactly 20 ms apart (50 per second), oldest first. Timer-driven (`micros()`), no `delay()`, no filtering. |
| Every 20 ms | `ACC:0.98,0.02,0.11` | One accelerometer reading, x,y,z in g (gravity = 1.00), 2 decimals. Same 50 per second as PPG so the laptop can cancel arm movement and see fall impacts. |
| Every 1 s | `LIGHT:2100` | Photoresistor on pin 10, 0 (dark) to 4095 (very bright): sun vs shade. |
| Every 1 s | `AIRT:31.42` | Air temperature at the worker, °C. |
| Every 1 s | `HUM:58.2` | Relative humidity, % (BME280 only). |
| Every 1 s | `BARO:1012.35` | Air pressure, hPa, 2 decimals (about 0.12 hPa per meter of height: helps confirm falls). |

Keep sending `TEMP`, `TILT` and `BTN` exactly as before (the laptop keeps the tilt switch as the fallback: it still catches a slow slump with no impact, and takes over again if `ACC` stops). The laptop side for these lines is built (`docs/sessions/band-sensors.md`): a `PPG` line with anything other than exactly 10 whole numbers 0-4095 is dropped whole. If the band already computes heart rate, it can also send `HR:72`; the laptop uses both.

**Oct 18 build (as wired):** Prop-Maker Wing LIS3DH accelerometer (I2C 0x18), photoresistor on pin 10 (`LIGHT:`), diode on A5, button 6, buzzer 5. No tilt switch (no `TILT:` line; movement and falls come from `ACC:`), no pulse sensor (no `PPG:`; heart rate comes over Bluetooth), no air sensor. The program is `firmware/band/band.ino`; switches for each part are in `config.h`.

## 3. WiFi (optional, later)

Same lines, same commands, over UDP to the laptop on port 5005. The laptop replies to whatever address the band sent from. The ESP32 only joins 2.4 GHz networks, so turn on Maximize Compatibility on an iPhone hotspot.

## 4. How to test without us

1. Open the Arduino Serial Monitor at 115200 baud.
2. You should see `TEMP:` every 5 s and `TILT:...,ACT:...` every second.
3. Shake it: ACT should go up. Tip it: TILT should go to 1.
4. Type `BUZZ:HEAT_RED` and press Enter: flashing red + a long beep every 10 s.
5. Short-press the button: the beeping stops, a white blink, and `BTN:1` prints.
6. Hold the button 3 s: purple blinks, 3 beeps, `BTN:SOS` prints.
7. Try every command in the table above.

Questions or changes: tell Aiden first, so the laptop side changes too.
