# Finishing the band: pulse, accelerometer, air sensor

For the hardware team. Picks up from the band as built on Oct 17 (Feather ESP32-S3, buzzer D5, button D6, tilt D9, diode on A5, built-in LED). Everything in the team doc still stands. This adds the new parts and the steps to finish.

**Goal:** the band measures heart rate itself, so we don't need the Apple Watch. The band sends raw numbers fast. The laptop does the hard math (finding heartbeats, ignoring arm movement, spotting falls).

**Rules that matter most**
1. Power every new sensor from **3V**, never USB/5V. A 5V signal into a Feather pin can kill it.
2. **No `delay()` anywhere in the main loop** once the pulse code is in. It breaks the timing. Use `millis()` / `micros()` timers.
3. Keep everything that already works (TEMP, TILT, BTN lines, all BUZZ commands) exactly as it is.

---

## 1. What you should have

| Part | Count | Job |
| --- | --- | --- |
| Grove Light Sensor | 1 | Pulse (sees tiny light changes from blood) |
| LED (Grove LED socket kit or a bare LED) | 1 | Shines into the skin for the pulse |
| Grove 3-Axis Accelerometer | 2 (1 spare) | Movement, falls, cleaning up the pulse |
| Temperature/barometer sensor (BME280 or BMP280) | 1 | Real air temp at the worker + height change for falls |
| Grove-to-Grove cables | 3 | Cut in half to reach the breadboard |
| Jumper wires (male-to-male / female-to-female) | a handful | Extra connections |
| 100 Ω resistor | 1 | Only for a bare LED |
| Black electrical tape | | Blocks room light from the pulse sensor |

**Write down the chip name** printed on the accelerometer and the air sensor (for example ADXL345, LIS3DH, MMA7660, BME280, BMP280). You'll need it in step 6.

---

## 2. Pin plan

| Part | Feather pin | Breadboard (right side) |
| --- | --- | --- |
| Light sensor signal | **D10** | row 11 (check the label next to it says 10) |
| Pulse LED | **D12** | row 9 (label 12) |
| Accelerometer SCL | **SCL** | row 15 |
| Accelerometer SDA | **SDA** | row 16 |
| Air sensor SCL | **SCL** | row 15 (same row, another free hole) |
| Air sensor SDA | **SDA** | row 16 (same row, another free hole) |
| All new sensors: power | 3V | red rail |
| All new sensors: ground | GND | blue rail |

Why D10 for the light sensor: it's one of the few analog pins that keeps working when WiFi is on (A0-A4 don't). If you ever bring back the LED strip, move its data/clock from D10/D11 to D11/D13.

Before plugging anything in, **check the tiny label next to each row on the Feather.** If row 11 isn't 10, use whatever row is.

---

## 3. Make the cables

Grove plugs don't fit a breadboard, so:
1. Cut a Grove-to-Grove cable in the middle. Each half = one sensor cable.
2. Strip about 5 mm off the end of each of the 4 wires.
3. Twist the copper strands tight so they push into the breadboard cleanly.

**Grove wire colors**

| Color | I2C sensors (accelerometer, air sensor) | Analog sensors (light sensor) |
| --- | --- | --- |
| Black | GND | GND |
| Red | 3V | 3V |
| White | SDA | not used (tape the end) |
| Yellow | SCL | signal |

Out of Grove cables? Push male-to-male jumpers straight into the sensor's Grove socket, one per hole, and tape them in place.

---

## 4. Wire it

Unplug USB while wiring.

**Accelerometer**
1. Black → blue (GND) rail.
2. Red → red (3V) rail.
3. Yellow (SCL) → a free hole in row 15, right side.
4. White (SDA) → a free hole in row 16, right side.

**Air sensor (BME280/BMP280)**
1. Same four connections, into different free holes in the same rails and rows. Both sensors share the same two data wires. That's normal for I2C.

**Light sensor**
1. Black → blue rail.
2. Red → red rail.
3. Yellow (signal) → a free hole in row 11, right side (D10).
4. White → not used. Tape the bare end so it can't touch anything.

**Pulse LED**
- **Grove LED socket kit:** black → blue rail, red → red rail, yellow → row 9 (D12). Turn its little knob to max brightness.
- **Bare LED:** D12 (row 9) → 100 Ω resistor → LED **long leg**. LED **short leg** → blue rail.
- Color: **green is best** for reading a pulse on the wrist. Red works too.

**Leave the tilt switch where it is for now.** The laptop still uses it until the accelerometer is proven.

---

## 5. Mount it

**Pulse sensor (the part that decides if this works)**
1. Put the light sensor and the LED side by side on the **underside of the wrist**, both facing the skin, about 5-10 mm apart.
2. Put a strip of black tape (or cardboard) standing between them, so LED light can't shine straight into the sensor. It has to go *through the skin* first.
3. Cover both with black tape on the outside to block room light.
4. Strap snug, like a watch. Too loose = light leaks and motion noise. Too tight = cuts off blood flow and the pulse disappears.

**Accelerometer:** tape it flat to the breadboard or the strap so it can't wobble on its own. It should move exactly with the arm.

**Air sensor:** facing **out**, on the outside of the strap, not touching skin and not next to the Feather (the board gets warm). It needs open air to read the real temperature.

---

## 6. Firmware

### 6a. Install libraries
Arduino IDE → **Tools → Manage Libraries**, search and install the ones that match your chips:

| Chip | Library |
| --- | --- |
| ADXL345 | Adafruit ADXL345 |
| LIS3DH / LIS3DHTR | Adafruit LIS3DH |
| MMA7660 | Seeed "Accelerometer MMA7660" (heads up: max 1.5 g, a hard fall maxes it out, but it still works for movement) |
| BME280 | Adafruit BME280 |
| BMP280 | Adafruit BMP280 |
| BMP180 / BMP085 | Adafruit BMP085 |

Each one also asks to install **Adafruit Unified Sensor**. Say yes.

### 6b. Find your sensors (I2C scan)
1. Make a new sketch with the code below.
2. Upload it, open the Serial Monitor at 115200.
3. It lists the address of every I2C sensor it finds.

```cpp
#include <Wire.h>

void setup() {
  Serial.begin(115200);
  delay(1500);
#ifdef PIN_I2C_POWER
  pinMode(PIN_I2C_POWER, OUTPUT);       // some Feathers switch I2C power with a pin
  digitalWrite(PIN_I2C_POWER, HIGH);
  delay(10);
#endif
  Wire.begin();
}

void loop() {
  Serial.println("Scanning...");
  for (byte addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print("Found 0x");
      Serial.println(addr, HEX);
    }
  }
  delay(3000);
}
```

| Address | Chip |
| --- | --- |
| 0x53 | ADXL345 accelerometer |
| 0x18 or 0x19 | LIS3DH accelerometer |
| 0x4C | MMA7660 accelerometer |
| 0x76 or 0x77 | BME280 / BMP280 / BMP180 air sensor |

Nothing found? Check the white/yellow wires aren't swapped and that red is on 3V.

### 6c. Add the new sensors to band.ino
Paste this into band.ino. Call `sensorsSetup()` at the end of `setup()` and `sensorsLoop()` in `loop()`. This version is for **ADXL345 + BME280**. Swaps for other chips are below.

```cpp
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_ADXL345_U.h>
#include <Adafruit_BME280.h>

const int PPG_PIN = 10;          // light sensor signal (D10, works with WiFi on)
const int PULSE_LED_PIN = 12;    // pulse LED (D12)

Adafruit_ADXL345_Unified accel(12345);
Adafruit_BME280 bme;
bool haveAccel = false;
bool haveAir = false;

uint16_t ppg[10];                // 10 light readings = one PPG line
int ppgCount = 0;
uint32_t nextSampleUs;           // when the next 20 ms sample is due
uint32_t nextAirMs;              // when the next 1 s air reading is due

void sensorsSetup() {
#ifdef PIN_I2C_POWER
  pinMode(PIN_I2C_POWER, OUTPUT);
  digitalWrite(PIN_I2C_POWER, HIGH);
  delay(10);                     // fine here: setup only
#endif
  Wire.begin();
  Wire.setClock(400000);         // fast I2C so 50 reads a second is easy

  haveAccel = accel.begin();
  if (haveAccel) accel.setRange(ADXL345_RANGE_16_G);   // big range so a fall doesn't max out
  haveAir = bme.begin(0x76) || bme.begin(0x77);

  analogReadResolution(12);      // light readings 0-4095
  pinMode(PULSE_LED_PIN, OUTPUT);
  digitalWrite(PULSE_LED_PIN, HIGH);   // pulse LED always on

  nextSampleUs = micros();
  nextAirMs = millis();
}

void sensorsLoop() {
  // Every 20 ms exactly (50 times a second): one light reading + one accelerometer reading.
  if ((int32_t)(micros() - nextSampleUs) >= 0) {
    nextSampleUs += 20000;       // schedule from the last due time, not from "now", so spacing stays even

    ppg[ppgCount++] = analogRead(PPG_PIN);
    if (ppgCount == 10) {        // every 200 ms send the 10 readings, oldest first
      Serial.print("PPG:");
      for (int i = 0; i < 10; i++) {
        if (i) Serial.print(',');
        Serial.print(ppg[i]);
      }
      Serial.println();
      ppgCount = 0;
    }

    if (haveAccel) {
      sensors_event_t e;
      accel.getEvent(&e);        // m/s^2, divide by 9.80665 to get g
      Serial.printf("ACC:%.2f,%.2f,%.2f\n",
                    e.acceleration.x / 9.80665, e.acceleration.y / 9.80665, e.acceleration.z / 9.80665);
    }
  }

  // Every 1 s: air temperature, humidity, pressure.
  if (haveAir && (int32_t)(millis() - nextAirMs) >= 0) {
    nextAirMs += 1000;
    Serial.printf("AIRT:%.2f\n", bme.readTemperature());          // °C
    Serial.printf("HUM:%.1f\n", bme.readHumidity());              // %
    Serial.printf("BARO:%.2f\n", bme.readPressure() / 100.0);     // hPa
  }
}
```

**Swaps for other chips**
- **LIS3DH:** `#include <Adafruit_LIS3DH.h>`, `Adafruit_LIS3DH accel;`, `haveAccel = accel.begin(0x19) || accel.begin(0x18);`, then `accel.setRange(LIS3DH_RANGE_8_G); accel.setDataRate(LIS3DH_DATARATE_100_HZ);`. `getEvent` works the same.
- **BMP280:** `#include <Adafruit_BMP280.h>`, `Adafruit_BMP280 bme;`. There's no humidity, so delete the `HUM` line.
- **MMA7660 / BMP180:** use the library's example sketch to get the reading, then print it in the same line format.

### 6d. Lines the band now sends

| Every | Line | Meaning |
| --- | --- | --- |
| 5 s | `TEMP:34.62` | Skin temp (unchanged) |
| 1 s | `TILT:0,ACT:7` | Tilt switch (unchanged) |
| on press | `BTN:1`, `BTN:SOS` | Button (unchanged) |
| 200 ms | `PPG:512,515,520,518,511,507,509,514,519,516` | 10 raw light readings, 20 ms apart, oldest first |
| 20 ms | `ACC:0.98,0.02,0.11` | Accelerometer x,y,z in g, 2 decimals |
| 1 s | `AIRT:31.42` | Air temperature °C |
| 1 s | `HUM:58.2` | Humidity % (BME280 only) |
| 1 s | `BARO:1012.35` | Air pressure, hPa, 2 decimals |

Exact format: capital letters, a colon, commas, no spaces, no units. The laptop safely ignores lines it doesn't know yet, so you can send these right away.

---

## 7. Test each part

Open the Serial Monitor at 115200.

1. **Rates look right:** about 5 `PPG:` lines and 50 `ACC:` lines a second, plus `AIRT`/`HUM`/`BARO` once a second. The old lines still show up.
2. **Accelerometer:** band lying flat → the axis pointing down reads about 1.00 (or -1.00), the other two near 0. Shake it → numbers jump.
3. **Air sensor:** `AIRT` close to room temp. Breathe on it → `HUM` goes up. Lift the band from the floor to above your head → `BARO` drops about 0.2 hPa.
4. **Pulse, the big one:**
   1. Temporarily change the PPG print to one number per line (`Serial.println(analogRead(PPG_PIN));` every 20 ms) and close the Serial Monitor.
   2. Open **Tools → Serial Plotter**.
   3. Band on the wrist, room light blocked, sit still, arm resting on a table.
   4. You want a wavy line that bumps **about once a second**, matching your pulse (count it with two fingers on your neck).
   5. Flat line? Tighten the strap a little, check the LED is on, try the other side of the wrist, block more light.
   6. Line stuck at 0 or 4095? The sensor is getting no light or too much. Move the LED closer or farther, or turn the Grove LED's knob.
   7. Switch back to the normal `PPG:` line format when it works.
5. **Everything old still works:** run the 7 checks from the firmware contract (BUZZ commands, button, SOS).

---

## 8. Record test data for the laptop

The laptop's pulse code gets tuned on **your real recordings**, so this step matters a lot.

1. Plug the band into Aiden's Mac. Close the Arduino Serial Monitor (only one program can use the port).
2. Have a reference heart rate: an Apple Watch or chest strap worn at the same time, or count your neck pulse for 30 s at the start and end.
3. Find the port: `ls /dev/cu.usbmodem*`
4. Record 4 sessions of 2 minutes each, in the project folder:

```bash
.venv/bin/python tools/record_band.py /dev/cu.usbmodem1101 data/recordings/band_sit.txt --seconds 120
```

| File name | What to do |
| --- | --- |
| `band_sit.txt` | Sit still |
| `band_walk.txt` | Walk around the room |
| `band_arms.txt` | Swing arms / carry something |
| `band_fall.txt` | A few gentle fake falls onto a mat or bed, lie still 30 s after each |

5. Write the reference heart rates in a note next to the files (for example "sit: 72 bpm watch, walk: 95").

---

## 9. Finish the band

- [ ] Put the 100 Ω resistor in series with the buzzer (team doc, "Recommended small fix").
- [ ] Calibrate the skin diode against a real thermometer at two temperatures and put the numbers in `config.h`.
- [ ] Power: small LiPo (check the plug polarity with lab staff before plugging in) or a USB power bank.
- [ ] Mount: velcro strap, electrical tape over loose wires, nothing sharp against the skin.
- [ ] Optional: WiFi to the laptop (same lines over UDP port 5005, 2.4 GHz only, iPhone hotspot with Maximize Compatibility on).

---

## 10. Send Aiden

1. The I2C scan output (which addresses showed up).
2. The exact chip names.
3. A photo of the wired breadboard and one of the band on the wrist.
4. The 4 recordings from step 8 plus the reference heart rates.
5. Anything you changed from this guide (different pins, a different chip).

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| I2C scan finds nothing | White/yellow swapped; red not on 3V; loose stripped wire. Re-seat the wires |
| Only one I2C sensor found | That sensor's power or ground wire is loose |
| `ACC` lines come in bursts or slow | Something in loop() uses `delay()` or waits. Replace it with a millis() timer |
| PPG line is pure noise | Room light leaking in, strap too loose, or LED pointing straight into the sensor. Add the black divider and cover |
| Board resets when the buzzer goes off | Power dip. Use a better USB cable/port or the battery |
| Upload fails | Hold BOOT, tap RST, release BOOT, re-pick the port, retry (team doc) |
| No serial port while the sketch runs | Tools → USB CDC On Boot → Enabled |
