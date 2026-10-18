#!/bin/bash
# Compile and upload the band program from this Mac (Apple Silicon), no Arduino IDE needed.
# Needs: brew install arduino-cli, the esp32 core, and the Adafruit NeoPixel library (already set up).
# Run from the project folder:   bash tools/flash_band.sh
set -euo pipefail
BOARD="esp32:esp32:adafruit_feather_esp32s3:CDCOnBoot=cdc"     # Feather ESP32-S3, 4 MB flash / 2 MB PSRAM
# Arduino's bundled ctags is an Intel-only binary. band.ino defines every function before it's used,
# so automatic prototypes aren't needed: a do-nothing stand-in avoids installing Rosetta.
FAKE_CTAGS="$(mktemp -d)/ctags"; printf '#!/bin/sh\nexit 0\n' > "$FAKE_CTAGS"; chmod +x "$FAKE_CTAGS"
PORT="$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)"
if [ -z "$PORT" ]; then echo "No band found. Plug it in (USB-C data cable) and try again." >&2; exit 1; fi
echo "Band on $PORT. Close the Arduino Serial Monitor and stop the Redline backend if they're using it."
arduino-cli compile -b "$BOARD" --build-property "tools.ctags.cmd.path=$FAKE_CTAGS" firmware/band
arduino-cli upload -p "$PORT" -b "$BOARD" firmware/band
echo "Uploaded."
