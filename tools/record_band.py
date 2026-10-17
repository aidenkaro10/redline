"""
Record everything the band prints over USB into a text file, for testing the band's
pulse sensor and accelerometer later on the laptop.

Each saved line is: <laptop time in seconds> TAB <the band's line>
(we stamp on arrival, same rule as the live system).

Run (band plugged in, Arduino Serial Monitor CLOSED, only one program can use the port):
    .venv/bin/python tools/record_band.py /dev/cu.usbmodem1101 data/recordings/band_sit.txt --seconds 120

Find the port name with:  ls /dev/cu.usbmodem*
"""

import argparse
import time
from pathlib import Path

import serial  # pyserial, already in requirements.txt


def main():
    ap = argparse.ArgumentParser(description="Save the band's raw lines to a file.")
    ap.add_argument("port", help="serial port, e.g. /dev/cu.usbmodem1101")
    ap.add_argument("out", help="file to write, e.g. data/recordings/band_sit.txt")
    ap.add_argument("--seconds", type=float, default=120, help="how long to record (default 120)")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    counts = {}                                   # how many lines of each kind (PPG, ACC, ...)

    # timeout=1 so the loop can check the clock even if the band goes quiet
    with serial.Serial(args.port, 115200, timeout=1) as port, out.open("w") as f:
        print(f"Recording {args.port} for {args.seconds:.0f} s into {out} ...")
        end = time.time() + args.seconds
        while time.time() < end:
            raw = port.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            f.write(f"{time.time():.3f}\t{line}\n")
            kind = line.split(":", 1)[0] if ":" in line else "other"
            counts[kind] = counts.get(kind, 0) + 1

    # A quick sanity check: at the right rates, 120 s gives about 600 PPG lines and 6000 ACC lines.
    print("Done. Lines per kind:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none")


if __name__ == "__main__":
    main()
