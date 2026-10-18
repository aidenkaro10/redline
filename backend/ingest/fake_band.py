"""
Fake band: pretends to be the wristband, speaking the exact line protocol,
so the live path (serial or WiFi) can be tested with no hardware.

Like the real band (docs/firmware-contract.md) it sends:
    # fake band booting   a debug line (the laptop ignores "#" lines)
    TEMP:34.62            skin temperature, every 5 s
    TILT:0,ACT:6          tilt switch + activity (switch changes in the last second), every 1 s
and optionally HR:120 every 5 s (--hr), standing in for Pulsoid when there's no watch.

With --sensors it also speaks the new lines (firmware contract section 2b), made from
synthetic signals (backend/ingest/band_synth.py) whose true heart rate is the same ramp:
    PPG:512,...       10 raw light readings, every 200 ms
    ACC:0.98,0.02,0.1 accelerometer, every 20 ms (rest / walk / arm work, one minute each)
    AIRT:31.42  HUM:58.2  BARO:1012.35   air sensor, every 1 s
A --fall-at fall then also shows on the accelerometer (impact, new angle, still) and the
pressure (+0.1 hPa), so the laptop's accelerometer fall detection runs too.

It prints every command the laptop sends back (BUZZ:HEAT_RED ...), and can:
    --fall-at 30     tip over and stop moving 30 s in (for --fall-for seconds)
    --ack 5          press the button 5 s after it gets BUZZ:HEAT_RED
    --button-at 20   press the button once at 20 s
    --sos-at 40      hold the button 3 s at 40 s (sends BTN:SOS)

Run it (server first, in another terminal):
    WiFi:  python -m backend.ingest.fake_band --udp --hr --fall-at 60
           python -m backend.ingest.fake_band --udp --sensors --fall-at 150   (pulse/accel/air)
           (server: python -m backend.app.main --source live)
    USB:   python -m backend.ingest.fake_band --pty --hr
           it prints a port like /dev/ttys012; then start the server with
           python -m backend.app.main --source live --serial /dev/ttys012
"""

import argparse
import os
import random
import select
import socket
import time
import tty

from backend import config
from backend.ingest import band_synth


FAST_EVERY_S = 0.2           # --sensors: send the fast lines in batches every 200 ms (like the PPG line)


class FakeBand:
    TEMP_EVERY_S = 5.0      # firmware: TEMP every 5 s (HR from Pulsoid also arrives about every 5 s)

    def __init__(self, send_hr=False, ramp_min=10.0, fall_at=None, fall_for=180.0, ack_after=None,
                 button_at=None, seed=0, sos_at=None, sensors=False):
        self.send_hr = send_hr
        self.ramp_s = ramp_min * 60
        self.fall_at, self.fall_for = fall_at, fall_for
        self.ack_after = ack_after
        self.button_at = button_at
        self.rng = random.Random(seed)
        self.pending_buttons: list[float] = [] if button_at is None else [button_at]
        self.sos_at = sos_at
        self.next_temp = 0.0
        self.booted = False
        self.received: list[str] = []
        # --sensors: synthetic PPG / ACC / air whose true heart rate is true_hr(t)
        self.sensors = band_synth.SensorStream(self.true_hr, seed=seed) if sensors else None
        if self.sensors is not None and fall_at is not None:
            self.sensors.fall(fall_at, fall_for)
        self.next_sample = 0                 # next 20 ms sample to send
        self.ppg_buf: list[int] = []         # PPG samples waiting for a full line of 10
        self.next_air = 0.0

    def true_hr(self, t: float) -> float:
        """The heart rate the fake worker really has at second t (ramps up like the HR: line)."""
        return 95 + 80 * min(t / max(self.ramp_s, 1), 1)

    def fast_lines(self, t: float) -> list[str]:
        """--sensors: every ACC/PPG line due by second t (50 ACC + 5 PPG a second), and the air lines."""
        if self.sensors is None:
            return []
        lines = []
        while self.next_sample < int(t * band_synth.FS):
            p, a = self.sensors.sample(self.next_sample)
            self.ppg_buf.append(p)
            lines.append(f"ACC:{a[0]:.2f},{a[1]:.2f},{a[2]:.2f}")
            if len(self.ppg_buf) == 10:
                lines.append("PPG:" + ",".join(str(v) for v in self.ppg_buf))
                self.ppg_buf = []
            self.next_sample += 1
        if t >= self.next_air:
            self.next_air += 1.0
            temp, hum, pres = self.sensors.air_at(t)
            lines += [f"AIRT:{temp:.2f}", f"HUM:{hum:.1f}", f"BARO:{pres:.2f}"]
        return lines

    def lines_at(self, t: float) -> list[str]:
        """What the band says at second t of its run."""
        fallen = self.fall_at is not None and self.fall_at <= t < self.fall_at + self.fall_for
        lines = []
        if not self.booted:
            lines.append("# fake band booting")
            self.booted = True
        if t >= self.next_temp:                          # every 5 s
            self.next_temp += self.TEMP_EVERY_S
            skin = 33.8 + 1.2 * min(t / max(self.ramp_s, 1), 1) + self.rng.gauss(0, 0.05)
            lines.append(f"TEMP:{skin:.2f}")
            if self.send_hr:
                hr = self.true_hr(t) + self.rng.gauss(0, 2)
                lines.append(f"HR:{hr:.0f}")
        lines.append("TILT:1,ACT:0" if fallen else f"TILT:0,ACT:{self.rng.randint(3, 8)}")   # every call (1 s)
        while self.pending_buttons and self.pending_buttons[0] <= t:
            self.pending_buttons.pop(0)
            lines.append("BTN:1")
        if self.sos_at is not None and t >= self.sos_at:
            self.sos_at = None
            lines.append("BTN:SOS")
        return lines

    def on_command(self, line: str, t: float) -> None:
        line = line.strip()
        if not line:
            return
        self.received.append(line)
        print(f"[fake band] got {line}", flush=True)
        if line == "BUZZ:HEAT_RED" and self.ack_after is not None:
            self.pending_buttons.append(t + self.ack_after)
            self.pending_buttons.sort()


def run_udp(band: FakeBand, host: str, port: int, every: float, duration: float | None) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    start = time.monotonic()
    next_send = 0.0
    print(f"[fake band] sending to {host}:{port} over UDP", flush=True)
    next_fast = 0.0
    while duration is None or time.monotonic() - start < duration:
        t = time.monotonic() - start
        if t >= next_send:
            sock.sendto(("\n".join(band.lines_at(t)) + "\n").encode(), (host, port))
            next_send += every
        if band.sensors is not None and t >= next_fast:          # fast lines, one packet every 200 ms
            sock.sendto(("\n".join(band.fast_lines(t)) + "\n").encode(), (host, port))
            next_fast += FAST_EVERY_S
        ready, _, _ = select.select([sock], [], [], 0.02)
        if ready:
            data, _ = sock.recvfrom(1024)
            for line in data.decode().splitlines():
                band.on_command(line, t)


def run_pty(band: FakeBand, every: float, duration: float | None) -> None:
    master, slave = os.openpty()
    tty.setraw(slave)                       # no echo, no line editing: behave like a USB serial cable
    print(f"[fake band] serial port ready: {os.ttyname(slave)}", flush=True)
    print(f"[fake band] start the server with: python -m backend.app.main --source live --serial {os.ttyname(slave)}",
          flush=True)
    start = time.monotonic()
    next_send = 0.0
    buf = b""
    next_fast = 0.0
    while duration is None or time.monotonic() - start < duration:
        t = time.monotonic() - start
        if t >= next_send:
            os.write(master, ("\n".join(band.lines_at(t)) + "\n").encode())
            next_send += every
        if band.sensors is not None and t >= next_fast:
            os.write(master, ("\n".join(band.fast_lines(t)) + "\n").encode())
            next_fast += FAST_EVERY_S
        ready, _, _ = select.select([master], [], [], 0.02)
        if ready:
            buf += os.read(master, 1024)
            *lines, buf = buf.split(b"\n")
            for line in lines:
                band.on_command(line.decode(errors="replace"), t)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fake wristband speaking the line protocol")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--udp", action="store_true", help="send over WiFi/UDP")
    mode.add_argument("--pty", action="store_true", help="act as a USB serial port (prints its path)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=config.UDP_PORT)
    ap.add_argument("--every", type=float, default=1.0, help="seconds between readings")
    ap.add_argument("--hr", action="store_true", help="also send HR lines (stand-in for Pulsoid)")
    ap.add_argument("--ramp", type=float, default=10.0, help="minutes for HR/skin temp to ramp up")
    ap.add_argument("--fall-at", type=float, default=None, help="tip over at this second")
    ap.add_argument("--fall-for", type=float, default=180.0)
    ap.add_argument("--ack", type=float, default=None, help="press the button N s after BUZZ:HEAT_RED")
    ap.add_argument("--button-at", type=float, default=None)
    ap.add_argument("--sos-at", type=float, default=None, help="send BTN:SOS (3 s hold) at this second")
    ap.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    ap.add_argument("--sensors", action="store_true",
                    help="also send PPG / ACC / AIRT / HUM / BARO lines (band pulse, accelerometer, air sensor)")
    a = ap.parse_args(argv)
    band = FakeBand(a.hr, a.ramp, a.fall_at, a.fall_for, a.ack, a.button_at, sos_at=a.sos_at, sensors=a.sensors)
    try:
        if a.udp:
            run_udp(band, a.host, a.port, a.every, a.duration)
        else:
            run_pty(band, a.every, a.duration)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
