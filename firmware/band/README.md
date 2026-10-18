# Redline band program: how to upload

Two files, kept together in a folder named `band`:
- `band.ino`: the program
- `config.h`: pins, which parts are plugged in, and the skin temperature calibration

It works with whatever is wired right now. Missing parts are skipped: no accelerometer or air sensor = no `ACC`/`AIRT` lines, and set `ENABLE_PPG 0` in `config.h` if the light sensor isn't on yet.

## Upload (Arduino IDE)

1. Open **Arduino IDE** on the laptop that already uploaded the band's test sketches.
2. **File → Open** → pick `band/band.ino`. (The folder must be called `band`, or the IDE complains.)
3. **Tools → Manage Libraries** → search **Adafruit NeoPixel** → **Install**.
4. **Tools → Board → esp32 → Adafruit Feather ESP32-S3** (2MB PSRAM or No PSRAM, whichever matches the board you used before).
5. **Tools → USB CDC On Boot → Enabled.** Without this no serial port shows up.
6. Plug the band in. **Tools → Port** → pick the band's port.
7. Click **Upload** (the → arrow).
8. If it fails: hold **BOOT**, tap **RST**, release **BOOT**, re-pick the port, click Upload again. Press **RST** after "Hard resetting".

## Check it worked

1. **Tools → Serial Monitor**, set to **115200 baud**.
2. You should see:
   - `#Redline band ready`, plus lines saying which accelerometer and air sensor it found
   - `TEMP:` every 5 s, `TILT:...,ACT:...` every second
   - `PPG:` 5 times a second, `ACC:` 50 times a second (if those parts are on)
   - `AIRT:`, `HUM:`, `BARO:` every second (if the air sensor is on)
3. Type `BUZZ:HEAT_RED` and press Enter: flashing red + a long beep every 10 s. Short-press the button: beeping stops, a white blink, `BTN:1` prints.
4. Hold the button 3 s: purple blinks, 3 beeps, `BTN:SOS` prints.
5. **Close the Serial Monitor** before running the Redline app. Only one program can use the band at a time.

## Calibrate skin temperature (5 minutes, do it once)

1. In the Serial Monitor, watch the `#DIODE_MV:` number.
2. Room temperature: hold the diode next to a real thermometer for a minute. Write down the temperature and the mV.
3. Ice water: seal the diode in tape, put it in ice water next to the thermometer for a minute. Write down both again.
4. Put the 4 numbers in `config.h` (`CAL_T1_C`, `CAL_MV1`, `CAL_T2_C`, `CAL_MV2`) and upload again.
