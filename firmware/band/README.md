# Redline band program: how to upload

Two files, kept together in a folder named `band`:
- `band.ino`: the program
- `config.h`: pins, which parts are plugged in, and the skin temperature calibration

Set up for the Oct 18 build: Prop-Maker Wing accelerometer (auto-detected), photoresistor on pin 10 for sunlight (`LIGHT:`), diode on A5, button 6, buzzer 5. No tilt switch and no pulse sensor, so those are switched off in `config.h` (`ENABLE_TILT 0`, `ENABLE_PPG 0`). Missing I2C parts are skipped automatically.

## Upload (Arduino IDE)

1. Open **Arduino IDE** on the laptop that already uploaded the band's test sketches.
2. **File → Open** → pick `band/band.ino`. (The folder must be called `band`, or the IDE complains.)
3. **Tools → Manage Libraries** → search **Adafruit NeoPixel** → **Install**.
4. **Tools → Board → esp32 → Adafruit Feather ESP32-S3 2MB PSRAM** (this board has 4 MB flash; "No PSRAM" fails to upload).
5. **Tools → USB CDC On Boot → Enabled.** Without this no serial port shows up.
6. Plug the band in. **Tools → Port** → pick the band's port.
7. Click **Upload** (the → arrow).
8. If it fails: hold **BOOT**, tap **RST**, release **BOOT**, re-pick the port, click Upload again. Press **RST** after "Hard resetting".

## Check it worked

1. **Tools → Serial Monitor**, set to **115200 baud**.
2. You should see:
   - `#Redline band ready`, plus lines saying which accelerometer and air sensor it found
   - `#accelerometer: LIS3DH at 0x18`
   - `TEMP:` every 5 s, `LIGHT:` every second
   - `ACC:` 50 times a second
3. Type `BUZZ:HEAT_RED` and press Enter: flashing red + a long beep every 10 s. Short-press the button: beeping stops, a white blink, `BTN:1` prints.
4. Hold the button 3 s: purple blinks, 3 beeps, `BTN:SOS` prints.
5. **Close the Serial Monitor** before running the Redline app. Only one program can use the band at a time.

## Calibrate skin temperature (5 minutes, do it once)

1. In the Serial Monitor, watch the `#DIODE_MV:` number.
2. Room temperature: hold the diode next to a real thermometer for a minute. Write down the temperature and the mV.
3. Warm hand (or ice water): hold the diode tight in your hand with the thermometer next to it for a minute. Write down both again.
4. Put the 4 numbers in `config.h` (`CAL_T1_C`, `CAL_MV1`, `CAL_T2_C`, `CAL_MV2`) and upload again.

## Upload from the Mac (no Arduino IDE)

```bash
bash tools/flash_band.sh
```
It compiles and uploads with arduino-cli. Stop the Redline backend first (it holds the USB port).
