// Redline band firmware (Adafruit Feather ESP32-S3).
//
// The band REPORTS and OBEYS. The laptop decides everything (heat, man-down, lightning).
// It sends sensor lines over Bluetooth LE ("Redline Band", Nordic UART Service) AND USB serial
// at 115200, and follows BUZZ: commands from either. No WiFi or internet needed.
// Full line list: docs/firmware-contract.md. Settings and pins: config.h.
//
// Needs one library: "Adafruit NeoPixel" (Arduino IDE > Tools > Manage Libraries).
// Board: Adafruit Feather ESP32-S3, Tools > USB CDC On Boot > Enabled.
//
// No delay() in loop(): every job runs on its own timer, so the 50-per-second
// pulse and motion samples stay evenly spaced.

#include <Wire.h>
#include <Adafruit_NeoPixel.h>
#include "config.h"
#include <stdarg.h>
#if ENABLE_BLE
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLE2902.h>
#endif

// ============================================================ output: every line goes to USB and Bluetooth
#if ENABLE_BLE
// Nordic UART Service: a standard "serial port over Bluetooth". The laptop subscribes to TX
// (band -> laptop) and writes BUZZ: commands to RX (laptop -> band).
#define NUS_SERVICE "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_RX      "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_TX      "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
BLEServer* bleServer = nullptr;
BLECharacteristic* bleTx = nullptr;
volatile bool bleConnected = false;
char bleOut[1024];                // lines waiting to go out over Bluetooth
size_t bleOutLen = 0;
char bleIn[256];                  // bytes the laptop wrote, waiting for loop() to read
volatile size_t bleInLen = 0;
portMUX_TYPE bleInLock = portMUX_INITIALIZER_UNLOCKED;
#endif

void bleQueue(const char* s, size_t n);

// printf-style: send one formatted chunk (normally a whole line ending in \n) everywhere.
void out(const char* fmt, ...) {
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  if (n <= 0) return;
  if (n >= (int)sizeof buf) n = sizeof buf - 1;
  Serial.write((const uint8_t*)buf, n);
  bleQueue(buf, n);
}

// ============================================================ status light
Adafruit_NeoPixel pixel(1, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);

enum Pattern { P_OFF, P_HEAT_YELLOW, P_HEAT_RED, P_LIGHTNING, P_FLOOD_FIRE, P_MANDOWN };
Pattern pattern = P_OFF;
uint32_t patternStartMs = 0;      // when the current pattern began (for blink timing)
uint32_t lastCommandMs = 0;       // last BUZZ: line of any kind (the laptop re-sends every 20 s)
bool acknowledged = false;        // short press during HEAT_RED / MANDOWN stops the beeping

// Short overlays the band shows on its own (button feedback).
uint32_t whiteBlinkUntil = 0;     // short press: one quick white blink
uint32_t sosFeedbackUntil = 0;    // SOS: blinking purple

uint32_t shownColor = 0xFFFFFFFF;   // what the LED shows now (so we only update on a change)

void setColor(uint8_t r, uint8_t g, uint8_t b) {
  uint32_t c = pixel.Color(r, g, b);
  if (c == shownColor) return;
  shownColor = c;
  pixel.setPixelColor(0, c);
  pixel.show();
}

// Called every loop: works out what the light should be right now.
void updateLight(uint32_t now) {
  if (now < whiteBlinkUntil) { setColor(255, 255, 255); return; }
  if (now < sosFeedbackUntil) {                       // blinking purple
    bool on = ((now / 150) % 2) == 0;
    on ? setColor(160, 0, 255) : setColor(0, 0, 0);
    return;
  }
  if (now - lastCommandMs > NO_COMMAND_MS) {          // no laptop: slow cyan blink
    bool on = ((now / 1000) % 2) == 0;
    on ? setColor(0, 255, 255) : setColor(0, 0, 0);
    return;
  }
  uint32_t t = now - patternStartMs;
  switch (pattern) {
    case P_OFF:         setColor(0, 255, 0); break;                           // solid green
    case P_HEAT_YELLOW: setColor(255, 160, 0); break;                         // solid yellow
    case P_HEAT_RED:    ((t / 250) % 2 == 0) ? setColor(255, 0, 0) : setColor(0, 0, 0); break;   // 2 flashes/s
    case P_LIGHTNING:   ((t / 100) % 2 == 0) ? setColor(255, 255, 255) : setColor(0, 0, 0); break; // 5 flashes/s
    case P_FLOOD_FIRE:  setColor(0, 0, 255); break;                           // solid blue
    case P_MANDOWN: {                                                         // red "breathing"
      float phase = (t % 2000) / 2000.0f;                                     // 2 s per breath
      float level = 0.5f - 0.5f * cosf(phase * 2.0f * PI);                    // 0 -> 1 -> 0
      setColor((uint8_t)(255 * level), 0, 0);
      break;
    }
  }
}

// ============================================================ sounds
// A tiny beep queue: each entry is a tone (or a gap) of some length. Never blocks.
struct Beep { uint16_t hz; uint16_t ms; };   // hz = 0 means silence
Beep beepQueue[24];
int qLen = 0, qPos = 0;
uint32_t beepStepUntil = 0;

void playSequence(const Beep* seq, int n) {
  qLen = min(n, 24);
  for (int i = 0; i < qLen; i++) beepQueue[i] = seq[i];
  qPos = 0;
  beepStepUntil = 0;   // start right away
}

void stopSound() {
  qLen = 0; qPos = 0;
  noTone(PIN_BUZZER);
}

// Plays the next step when the current one is over. One-shot sequences end with a
// short silence step, so the buzzer is always left off. Never blocks.
void updateSound(uint32_t now) {
  if (qPos >= qLen || now < beepStepUntil) return;
  Beep b = beepQueue[qPos++];
  if (b.hz) tone(PIN_BUZZER, b.hz); else noTone(PIN_BUZZER);
  beepStepUntil = now + b.ms;
}

// Sounds that repeat while a pattern is active.
uint32_t nextRepeatMs = 0;

const Beep LONG_BEEP[]   = {{BEEP_HZ, 1000}, {0, 1}};
const Beep TWO_SHORT[]   = {{BEEP_HZ, 150}, {0, 150}, {BEEP_HZ, 150}, {0, 1}};
const Beep RAPID[]       = {{BEEP_HZ, 80}, {0, 80}, {BEEP_HZ, 80}, {0, 80}, {BEEP_HZ, 80}, {0, 80},
                            {BEEP_HZ, 80}, {0, 80}, {BEEP_HZ, 80}, {0, 1}};
const Beep THREE_BEEPS[] = {{BEEP_HZ, 120}, {0, 120}, {BEEP_HZ, 120}, {0, 120}, {BEEP_HZ, 120}, {0, 1}};
const Beep MANDOWN_CYCLE[] = {{BEEP_HZ, 300}, {BEEP_LOW_HZ, 300}};   // two-tone siren, repeated

void updatePatternSound(uint32_t now) {
  if (now < sosFeedbackUntil) return;   // let the SOS beeps finish
  switch (pattern) {
    case P_HEAT_RED:      // long beep every 10 s until a short press
      if (!acknowledged && now >= nextRepeatMs) { playSequence(LONG_BEEP, 2); nextRepeatMs = now + 10000; }
      break;
    case P_LIGHTNING:     // rapid short beeps, every 10 s
      if (now >= nextRepeatMs) { playSequence(RAPID, 10); nextRepeatMs = now + 10000; }
      break;
    case P_MANDOWN:       // continuous until a short press
      if (!acknowledged && qPos >= qLen && now >= beepStepUntil) playSequence(MANDOWN_CYCLE, 2);
      break;
    default: break;       // OFF / YELLOW / FLOOD_FIRE only beep once when they start
  }
}

// ============================================================ commands from the laptop
String inbox;        // USB serial bytes until a newline
String bleLine;      // Bluetooth bytes until a newline

void applyCommand(const String& name, uint32_t now) {
  Pattern p;
  if      (name == "OFF")         p = P_OFF;
  else if (name == "HEAT_YELLOW") p = P_HEAT_YELLOW;
  else if (name == "HEAT_RED")    p = P_HEAT_RED;
  else if (name == "LIGHTNING")   p = P_LIGHTNING;
  else if (name == "FLOOD_FIRE")  p = P_FLOOD_FIRE;
  else if (name == "MANDOWN")     p = P_MANDOWN;
  else { out("#unknown command: %s\n", name.c_str()); return; }

  lastCommandMs = now;
  if (p == pattern) return;      // the 20 s re-send: keep going, don't restart the sounds

  pattern = p;
  patternStartMs = now;
  acknowledged = false;
  nextRepeatMs = now;            // repeating patterns start at once
  stopSound();
  if (p == P_HEAT_YELLOW) playSequence(LONG_BEEP, 2);
  if (p == P_FLOOD_FIRE)  playSequence(TWO_SHORT, 4);
}

void handleLine(String line, uint32_t now) {
  line.trim();
  if (line.startsWith("BUZZ:")) applyCommand(line.substring(5), now);
}

void readCommands(uint32_t now) {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { handleLine(inbox, now); inbox = ""; }
    else if (inbox.length() < 64) inbox += c;
  }
#if ENABLE_BLE
  char tmp[256];
  size_t n = 0;
  portENTER_CRITICAL(&bleInLock);            // copy what the Bluetooth task wrote, then clear it
  n = bleInLen;
  memcpy(tmp, bleIn, n);
  bleInLen = 0;
  portEXIT_CRITICAL(&bleInLock);
  for (size_t i = 0; i < n; i++) {
    char c = tmp[i];
    if (c == '\n' || c == '\r') { handleLine(bleLine, now); bleLine = ""; }
    else if (bleLine.length() < 64) bleLine += c;
  }
#endif
}

// ============================================================ button
bool btnDown = false;            // debounced state
bool btnRaw = false;
uint32_t btnRawChangedMs = 0;
uint32_t pressStartMs = 0;
bool sosSent = false;

void onShortPress(uint32_t now) {
  out("BTN:1\n");
  whiteBlinkUntil = now + 150;
  if (pattern == P_HEAT_RED || pattern == P_MANDOWN) {
    acknowledged = true;         // stop beeping; the light keeps showing the alert
    stopSound();
  }
}

void onSos(uint32_t now) {
  out("BTN:SOS\n");
  sosFeedbackUntil = now + 1500;
  stopSound();
  playSequence(THREE_BEEPS, 6);
}

void updateButton(uint32_t now) {
  bool raw = digitalRead(PIN_BUTTON) == LOW;
  if (raw != btnRaw) { btnRaw = raw; btnRawChangedMs = now; }
  if (now - btnRawChangedMs < DEBOUNCE_MS || btnRaw == btnDown) {
    // no settled change; check for a held SOS
    if (btnDown && !sosSent && now - pressStartMs >= SOS_HOLD_MS) { sosSent = true; onSos(now); }
    return;
  }
  btnDown = btnRaw;
  if (btnDown) {                 // just pressed
    pressStartMs = now;
    sosSent = false;
  } else {                       // just released
    uint32_t held = now - pressStartMs;
    if (!sosSent && held < SHORT_PRESS_MAX_MS) onShortPress(now);
  }
}

// ============================================================ tilt switch
int tiltLast = -1;
uint16_t tiltChanges = 0;
uint32_t nextTiltMs = 0;

void updateTilt(uint32_t now) {
  int t = digitalRead(PIN_TILT) == HIGH ? 1 : 0;   // 1 = tipped
  if (tiltLast >= 0 && t != tiltLast) tiltChanges++;
  tiltLast = t;
  if (now >= nextTiltMs) {
    nextTiltMs = now + TILT_EVERY_MS;
    out("TILT:%d,ACT:%u\n", t, tiltChanges);
    tiltChanges = 0;
  }
}

// ============================================================ sunlight (photoresistor)
uint32_t nextLightMs = 0;

void updateSunlight(uint32_t now) {
  if (now < nextLightMs) return;
  nextLightMs = now + LIGHT_EVERY_MS;
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++) sum += analogRead(PIN_LIGHT);   // average 16 readings
  out("LIGHT:%u\n", (unsigned)(sum / 16));           // 0 = dark, 4095 = very bright
}

// ============================================================ skin temperature (diode)
uint32_t nextTempMs = 0;

void updateTemp(uint32_t now) {
  if (now < nextTempMs) return;
  nextTempMs = now + TEMP_EVERY_MS;
  uint32_t sum = 0;
  for (int i = 0; i < 64; i++) sum += analogReadMilliVolts(PIN_DIODE);   // average 64 readings
  float mv = sum / 64.0f;
  float slope = (CAL_T2_C - CAL_T1_C) / (CAL_MV2 - CAL_MV1);              // °C per mV (negative)
  float tempC = CAL_T1_C + (mv - CAL_MV1) * slope;
  out("TEMP:%.2f\n", tempC);
  out("#DIODE_MV:%.0f\n", mv);                                  // for calibration
}

// ============================================================ I2C sensors (auto-detected)
enum AccType { ACC_NONE, ACC_ADXL345, ACC_LIS3DH, ACC_MMA7660 };
AccType accType = ACC_NONE;
uint8_t accAddr = 0;
uint8_t airAddr = 0;
bool airHasHumidity = false;

bool i2cPresent(uint8_t addr) { Wire.beginTransmission(addr); return Wire.endTransmission() == 0; }

void writeReg(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr); Wire.write(reg); Wire.write(val); Wire.endTransmission();
}

bool readRegs(uint8_t addr, uint8_t reg, uint8_t* buf, uint8_t n) {
  Wire.beginTransmission(addr); Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(addr, n) != n) return false;
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

uint8_t readReg(uint8_t addr, uint8_t reg) { uint8_t v = 0; readRegs(addr, reg, &v, 1); return v; }

void setupAccelerometer() {
  if (i2cPresent(0x53) && readReg(0x53, 0x00) == 0xE5) {              // ADXL345
    accType = ACC_ADXL345; accAddr = 0x53;
    writeReg(accAddr, 0x2C, 0x0A);   // 100 samples/s
    writeReg(accAddr, 0x31, 0x0B);   // full resolution, ±16 g (falls don't max out)
    writeReg(accAddr, 0x2D, 0x08);   // measure
    out("#accelerometer: ADXL345 at 0x53\n");
    return;
  }
  for (uint8_t a : {0x19, 0x18}) {                                     // LIS3DH / LIS3DHTR
    if (i2cPresent(a) && readReg(a, 0x0F) == 0x33) {
      accType = ACC_LIS3DH; accAddr = a;
      writeReg(accAddr, 0x20, 0x57); // 100 samples/s, x/y/z on
      writeReg(accAddr, 0x23, 0xA8); // block update, ±8 g, high resolution
      out("#accelerometer: LIS3DH at 0x%02X\n", a);
      return;
    }
  }
  if (i2cPresent(0x4C)) {                                              // MMA7660 (±1.5 g only)
    accType = ACC_MMA7660; accAddr = 0x4C;
    writeReg(accAddr, 0x07, 0x00);   // standby to configure
    writeReg(accAddr, 0x08, 0x00);   // 120 samples/s
    writeReg(accAddr, 0x07, 0x01);   // active
    out("#accelerometer: MMA7660 at 0x4C (max 1.5 g)\n");
    return;
  }
  out("#accelerometer: none found\n");
}

bool readAccel(float& x, float& y, float& z) {
  uint8_t b[6];
  switch (accType) {
    case ACC_ADXL345:
      if (!readRegs(accAddr, 0x32, b, 6)) return false;
      x = (int16_t)(b[1] << 8 | b[0]) * 0.0039f;     // 3.9 mg per count (full resolution)
      y = (int16_t)(b[3] << 8 | b[2]) * 0.0039f;
      z = (int16_t)(b[5] << 8 | b[4]) * 0.0039f;
      return true;
    case ACC_LIS3DH:
      if (!readRegs(accAddr, 0x28 | 0x80, b, 6)) return false;   // 0x80 = auto-increment
      x = ((int16_t)(b[1] << 8 | b[0]) >> 4) * 0.004f;   // 12-bit, 4 mg per count at ±8 g
      y = ((int16_t)(b[3] << 8 | b[2]) >> 4) * 0.004f;
      z = ((int16_t)(b[5] << 8 | b[4]) >> 4) * 0.004f;
      return true;
    case ACC_MMA7660: {
      if (!readRegs(accAddr, 0x00, b, 3)) return false;
      float v[3];
      for (int i = 0; i < 3; i++) {
        int8_t r = b[i] & 0x3F;                           // 6-bit two's complement
        if (r & 0x20) r -= 64;
        v[i] = r / 21.33f;                                // 21.33 counts per g
      }
      x = v[0]; y = v[1]; z = v[2];
      return true;
    }
    default: return false;
  }
}

// ---- BME280 / BMP280 (air temperature, humidity, pressure), Bosch datasheet formulas ----
uint16_t dig_T1, dig_P1; int16_t dig_T2, dig_T3, dig_P2, dig_P3, dig_P4, dig_P5, dig_P6, dig_P7, dig_P8, dig_P9;
uint8_t dig_H1, dig_H3; int16_t dig_H2, dig_H4, dig_H5; int8_t dig_H6;
uint32_t nextAirMs = 0;

void setupAirSensor() {
  for (uint8_t a : {0x76, 0x77}) {
    if (!i2cPresent(a)) continue;
    uint8_t id = readReg(a, 0xD0);
    if (id != 0x60 && id != 0x58) continue;        // 0x60 = BME280, 0x58 = BMP280
    airAddr = a; airHasHumidity = (id == 0x60);
    uint8_t c[26];
    readRegs(a, 0x88, c, 26);
    dig_T1 = c[1] << 8 | c[0]; dig_T2 = c[3] << 8 | c[2]; dig_T3 = c[5] << 8 | c[4];
    dig_P1 = c[7] << 8 | c[6]; dig_P2 = c[9] << 8 | c[8]; dig_P3 = c[11] << 8 | c[10]; dig_P4 = c[13] << 8 | c[12];
    dig_P5 = c[15] << 8 | c[14]; dig_P6 = c[17] << 8 | c[16]; dig_P7 = c[19] << 8 | c[18]; dig_P8 = c[21] << 8 | c[20];
    dig_P9 = c[23] << 8 | c[22]; dig_H1 = c[25];
    if (airHasHumidity) {
      uint8_t h[7];
      readRegs(a, 0xE1, h, 7);
      dig_H2 = h[1] << 8 | h[0]; dig_H3 = h[2];
      dig_H4 = (int16_t)((int8_t)h[3] << 4 | (h[4] & 0x0F));
      dig_H5 = (int16_t)((int8_t)h[5] << 4 | (h[4] >> 4));
      dig_H6 = (int8_t)h[6];
      writeReg(a, 0xF2, 0x01);     // humidity x1 (must come before 0xF4)
    }
    writeReg(a, 0xF5, 0x08);       // filter x4 (steadier pressure for fall detection)
    writeReg(a, 0xF4, 0x4F);       // temp x2, pressure x4, normal mode
    out("#air sensor: %s at 0x%02X\n", airHasHumidity ? "BME280" : "BMP280", a);
    return;
  }
  out("#air sensor: none found\n");
}

void updateAir(uint32_t now) {
  if (!airAddr || now < nextAirMs) return;
  nextAirMs = now + AIR_EVERY_MS;
  uint8_t d[8];
  if (!readRegs(airAddr, 0xF7, d, airHasHumidity ? 8 : 6)) return;
  int32_t adcP = (int32_t)d[0] << 12 | d[1] << 4 | d[2] >> 4;
  int32_t adcT = (int32_t)d[3] << 12 | d[4] << 4 | d[5] >> 4;

  double v1 = (adcT / 16384.0 - dig_T1 / 1024.0) * dig_T2;
  double v2 = (adcT / 131072.0 - dig_T1 / 8192.0); v2 = v2 * v2 * dig_T3;
  double tFine = v1 + v2;
  double tempC = tFine / 5120.0;

  v1 = tFine / 2.0 - 64000.0;
  v2 = v1 * v1 * dig_P6 / 32768.0;
  v2 = v2 + v1 * dig_P5 * 2.0;
  v2 = v2 / 4.0 + dig_P4 * 65536.0;
  v1 = (dig_P3 * v1 * v1 / 524288.0 + dig_P2 * v1) / 524288.0;
  v1 = (1.0 + v1 / 32768.0) * dig_P1;
  if (v1 == 0) return;
  double p = 1048576.0 - adcP;
  p = (p - v2 / 4096.0) * 6250.0 / v1;
  v1 = dig_P9 * p * p / 2147483648.0;
  v2 = p * dig_P8 / 32768.0;
  p = p + (v1 + v2 + dig_P7) / 16.0;                    // pascals

  out("AIRT:%.2f\n", tempC);
  if (airHasHumidity) {
    int32_t adcH = (int32_t)d[6] << 8 | d[7];
    double h = tFine - 76800.0;
    h = (adcH - (dig_H4 * 64.0 + dig_H5 / 16384.0 * h)) *
        (dig_H2 / 65536.0 * (1.0 + dig_H6 / 67108864.0 * h * (1.0 + dig_H3 / 67108864.0 * h)));
    h = h * (1.0 - dig_H1 * h / 524288.0);
    h = constrain(h, 0.0, 100.0);
    out("HUM:%.1f\n", h);
  }
  out("BARO:%.2f\n", p / 100.0);          // hPa
}

// ============================================================ pulse + motion, 50 per second
uint16_t ppg[10];
int ppgCount = 0;
uint32_t nextSampleUs = 0;
// Anti-flicker: between two 20 ms samples we read the light sensor as often as the loop allows
// and average everything. Fast flicker (phone flashlights, LED lamps pulse hundreds of times a
// second) averages out, while the slow heartbeat (about 1 per second) stays. Without this, flicker
// "aliases" into a fake steady rhythm (we saw a perfect 90 bpm that wasn't a pulse).
uint32_t ppgSum = 0;
uint16_t ppgReads = 0;

void accumulatePpg() {
#if ENABLE_PPG
  ppgSum += analogRead(PIN_PPG);
  ppgReads++;
#endif
}

void updateSamples() {
  uint32_t nowUs = micros();
  if ((int32_t)(nowUs - nextSampleUs) < 0) return;
  nextSampleUs += SAMPLE_EVERY_US;                   // from the due time, so spacing stays even
  if ((int32_t)(nowUs - nextSampleUs) > 100000) nextSampleUs = nowUs + SAMPLE_EVERY_US;  // fell far behind: resync

#if ENABLE_PPG
  accumulatePpg();                                   // at least one reading per sample
  ppg[ppgCount++] = (uint16_t)(ppgSum / ppgReads);   // average of all reads in the last 20 ms
  ppgSum = 0; ppgReads = 0;
  if (ppgCount == 10) {                              // one PPG line per 10 samples (200 ms)
    out("PPG:%u,%u,%u,%u,%u,%u,%u,%u,%u,%u\n", ppg[0], ppg[1], ppg[2], ppg[3], ppg[4],
        ppg[5], ppg[6], ppg[7], ppg[8], ppg[9]);
    ppgCount = 0;
  }
#endif

  float x, y, z;
  if (accType != ACC_NONE && readAccel(x, y, z)) out("ACC:%.2f,%.2f,%.2f\n", x, y, z);
}

// ============================================================ Bluetooth LE link
#if ENABLE_BLE
class ServerEvents : public BLEServerCallbacks {
  void onConnect(BLEServer* server) override { bleConnected = true; }
  void onDisconnect(BLEServer* server) override {
    bleConnected = false;
    server->startAdvertising();              // let the laptop reconnect right away
  }
};

class RxEvents : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {   // runs in the Bluetooth task, so just copy the bytes
    String v = c->getValue();
    portENTER_CRITICAL(&bleInLock);
    for (size_t i = 0; i < v.length() && bleInLen < sizeof bleIn; i++) bleIn[bleInLen++] = v[i];
    portEXIT_CRITICAL(&bleInLock);
  }
};

void setupBle() {
  BLEDevice::init(BLE_NAME);
  BLEDevice::setMTU(185);                    // bigger packets: fewer notifications per second
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new ServerEvents());
  BLEService* svc = bleServer->createService(NUS_SERVICE);
  bleTx = svc->createCharacteristic(NUS_TX, BLECharacteristic::PROPERTY_NOTIFY);
  bleTx->addDescriptor(new BLE2902());
  BLECharacteristic* rx = svc->createCharacteristic(
      NUS_RX, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rx->setCallbacks(new RxEvents());
  svc->start();
  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(NUS_SERVICE);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  out("#bluetooth: advertising as \"%s\"\n", BLE_NAME);
}

void bleQueue(const char* s, size_t n) {
  if (!bleConnected) return;                 // nobody listening: don't pile up old lines
  if (bleOutLen + n > sizeof bleOut) bleOutLen = 0;   // laptop fell far behind: drop and carry on
  memcpy(bleOut + bleOutLen, s, n);
  bleOutLen += n;
}

uint32_t nextBleFlushMs = 0;

// Every 40 ms send what's queued, in chunks that fit one Bluetooth packet.
void updateBle(uint32_t now) {
  if (!bleConnected || bleOutLen == 0 || now < nextBleFlushMs) return;
  nextBleFlushMs = now + 40;
  uint16_t mtu = bleServer->getPeerMTU(bleServer->getConnId());
  size_t chunk = (mtu > 23 ? mtu : 23) - 3;  // a notification carries MTU - 3 bytes
  for (size_t i = 0; i < bleOutLen; i += chunk) {
    size_t n = min(chunk, bleOutLen - i);
    bleTx->setValue((uint8_t*)bleOut + i, n);
    bleTx->notify();
  }
  bleOutLen = 0;
}
#else
void bleQueue(const char*, size_t) {}
#endif

// ============================================================ setup + loop
void setup() {
  Serial.begin(115200);

#if defined(NEOPIXEL_POWER)
  pinMode(NEOPIXEL_POWER, OUTPUT);
  digitalWrite(NEOPIXEL_POWER, HIGH);
#endif
  pixel.begin();
  pixel.setBrightness(LED_BRIGHTNESS);
  setColor(0, 255, 0);

  pinMode(PIN_BUTTON, INPUT_PULLUP);
#if ENABLE_TILT
  pinMode(PIN_TILT, INPUT_PULLUP);
#endif
  pinMode(PIN_BUZZER, OUTPUT);
  analogReadResolution(12);

#if ENABLE_PPG
  pinMode(PIN_PULSE_LED, OUTPUT);
  digitalWrite(PIN_PULSE_LED, HIGH);                 // pulse LED always on
#endif

#if ENABLE_I2C
#if defined(PIN_I2C_POWER)
  pinMode(PIN_I2C_POWER, OUTPUT);
  digitalWrite(PIN_I2C_POWER, HIGH);
  delay(10);                                         // setup only, fine here
#endif
  Wire.begin();
  Wire.setClock(400000);
  setupAccelerometer();
  setupAirSensor();
#endif

  uint32_t now = millis();
  lastCommandMs = now;          // don't blink cyan right at boot
  patternStartMs = now;
  nextSampleUs = micros();
#if ENABLE_BLE
  setupBle();
#endif
  out("#Redline band ready\n");
}

void loop() {
  uint32_t now = millis();
  readCommands(now);
  updateButton(now);
#if ENABLE_TILT
  updateTilt(now);
#endif
#if ENABLE_LIGHT
  updateSunlight(now);
#endif
  accumulatePpg();
  updateSamples();
  updateTemp(now);
#if ENABLE_I2C
  updateAir(now);
#endif
  updatePatternSound(now);
  updateSound(now);
  updateLight(now);
#if ENABLE_BLE
  updateBle(now);
#endif
}
