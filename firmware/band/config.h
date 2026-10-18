// Redline band settings. Change these, not band.ino.
#pragma once

// ---------- Pins (Adafruit Feather ESP32-S3, numbers = GPIO numbers printed on the board) ----------
#define PIN_BUZZER      5     // passive buzzer: tone() only
#define PIN_BUTTON      6     // button to GND, INPUT_PULLUP (LOW = pressed)
#define PIN_TILT        9     // tilt switch to GND, INPUT_PULLUP (HIGH = tipped). Not wired on the Oct 18 build
#define PIN_DIODE       A5    // skin temp diode junction (A5 keeps working with WiFi on)
#define PIN_LIGHT       10    // photoresistor + 10k divider: sun vs shade (D10, ADC1: works with WiFi on)
#define PIN_PPG         10    // only if a pulse sensor is wired here instead (ENABLE_PPG)
#define PIN_PULSE_LED   12    // LED for the pulse sensor (ENABLE_PPG only)

// ---------- Which parts are plugged in (1 = yes, 0 = no) ----------
// Oct 18 build (Noah's hardware rundown): Prop-Maker Wing accelerometer (LIS3DH, 0x18) on I2C,
// photoresistor on pin 10 for sunlight, diode on A5, button 6, buzzer 5. No tilt switch, no pulse sensor.
#define ENABLE_TILT     0     // 1 only if the tilt switch is wired to PIN_TILT (an empty pin reads "tipped")
#define ENABLE_LIGHT    0     // photoresistor on PIN_LIGHT: sends LIGHT:<0-4095> every second
#define ENABLE_PPG      1     // light sensor + LED pulse on PIN_PPG (not on this build; can't share pin 10 with LIGHT)
#define ENABLE_I2C      1     // accelerometer and/or air sensor on SDA/SCL (auto-detected)

// ---------- Skin temperature calibration (two-point) ----------
// How to calibrate:
//   1. Open the Serial Monitor. Every 5 s the band prints "#DIODE_MV:<number>".
//   2. Hold the diode next to a real thermometer at room temperature. Write down both numbers.
//   3. Second point: hold the diode tight in a warm hand (or seal it in tape and put it in ice water),
//      with the thermometer right next to it. Write down both numbers. Farther apart = more accurate.
//   4. Put them below and upload again.
// Until then it uses a rough default (about -2 mV per °C, 536 mV at 25 °C).
#define CAL_T1_C        25.0f   // temperature at point 1 (°C)
#define CAL_MV1         536.0f  // diode mV at point 1
#define CAL_T2_C        0.0f    // temperature at point 2 (°C)
#define CAL_MV2         586.0f  // diode mV at point 2

// ---------- Light ----------
#define LED_BRIGHTNESS  64      // 0-255. About 25% keeps current low on USB power

// ---------- Sounds ----------
#define BEEP_HZ         2000    // buzzer pitch
#define BEEP_LOW_HZ     1400    // second pitch for man-down

// ---------- Timing (ms) ----------
#define TEMP_EVERY_MS       5000   // TEMP line
#define TILT_EVERY_MS       1000   // TILT line (only with ENABLE_TILT)
#define LIGHT_EVERY_MS      1000   // LIGHT line
#define AIR_EVERY_MS        1000   // AIRT / HUM / BARO lines
#define SAMPLE_EVERY_US     20000  // PPG + ACC sample: 50 per second
#define DEBOUNCE_MS         30
#define SHORT_PRESS_MAX_MS  1000   // shorter = "I'm OK" (BTN:1)
#define SOS_HOLD_MS         3000   // hold this long = BTN:SOS
#define NO_COMMAND_MS       60000  // no command for this long = blink cyan ("cable dead?")
