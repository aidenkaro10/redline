"""
Demo heart rate input: a clearly labeled stand-in for a pulse sensor during live demos
(the band has none). Turned on with POST /api/demo/heart_rate {"mode": "rest" | "active" | "off"}.

While it's on, the hub feeds one reading per second (on that worker's clock) through the
normal reading path, tagged source "demo_input", and that worker's other heart rate sources
are ignored so the values don't fight. Every message about that heart rate says "demo_input".

The values are a smooth random walk inside the mode's range (backend/config.py):
  rest   DEMO_HR_REST_BPM    (65-75, centered on 70)
  active DEMO_HR_ACTIVE_BPM  (105-115, centered on 110)
Switching modes ramps from the current value to the new range over DEMO_HR_RAMP_S (45 s),
so rest -> active climbs gradually and never jumps. Small jitter: ±DEMO_HR_JITTER_BPM.

It never starts by itself and is off after every server restart (it lives only in memory).
"""

import random

from backend import config

MODES = ("off", "rest", "active")
RANGES = {"rest": config.DEMO_HR_REST_BPM, "active": config.DEMO_HR_ACTIVE_BPM}
EVERY_S = 1.0                     # one reading per second
DRIFT_BPM = 0.5                   # how far the slow middle of the walk can move per second
PULL_BACK = 0.1                   # per second, how strongly the walk is pulled back to the range's middle


class DemoHeartRate:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.mode = "off"
        self.worker_id: str | None = None
        self.value: float | None = None         # the last value sent
        self.center: float | None = None        # the slow middle of the random walk
        self.ramp: tuple[float, float, float] | None = None   # (from bpm, to bpm, start time)
        self.last_t: float | None = None

    def set_mode(self, mode: str, worker_id: str | None, t: float, start_bpm: float | None = None) -> None:
        """
        Change mode. start_bpm: where to ramp from when turning on (e.g. the worker's last real
        heart rate); default the middle of the rest range.
        """
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode == "off":
            self.mode, self.ramp, self.last_t = "off", None, None
            return
        lo, hi = RANGES[mode]
        frm = self.value if self.mode != "off" and self.value is not None else (
            start_bpm if start_bpm is not None else sum(config.DEMO_HR_REST_BPM) / 2)
        to = (lo + hi) / 2                                                      # land on the middle (70 / 110)
        self.mode, self.worker_id = mode, worker_id
        self.ramp = (frm, to, t)
        self.center = to
        if self.last_t is None:
            self.last_t = t - EVERY_S                                           # first reading right away

    def next(self, t: float) -> float | None:
        """The next heart rate if a second has passed on this worker's clock, else None."""
        if self.mode == "off" or self.last_t is None or t - self.last_t < EVERY_S:
            return None
        dt = min(t - self.last_t, 5.0)
        self.last_t = t
        lo, hi = RANGES[self.mode]
        jitter = self.rng.uniform(-config.DEMO_HR_JITTER_BPM, config.DEMO_HR_JITTER_BPM)
        frm, to, start = self.ramp
        frac = (t - start) / config.DEMO_HR_RAMP_S
        if frac < 1.0:
            # Ramping: straight line from where we were to the new range, a little jitter, no jumps.
            value = frm + (to - frm) * max(frac, 0.0) + 0.5 * jitter
            low, high = min(frm, to), max(frm, to)
            value = min(max(value, low - config.DEMO_HR_JITTER_BPM), high + config.DEMO_HR_JITTER_BPM)
        else:
            # In range: a random walk centered on the range's middle (pulled back toward it), plus
            # jitter, then clamped so the value is ALWAYS inside the range.
            mid = (lo + hi) / 2
            self.center += -PULL_BACK * (self.center - mid) * dt + self.rng.gauss(0, DRIFT_BPM) * dt ** 0.5
            self.center = min(max(self.center, lo), hi)
            value = min(max(self.center + jitter, lo), hi)
        self.value = value
        return round(value, 1)

    def state(self) -> dict:
        """For GET /api/demo/heart_rate and DemoState (no heart rate values: those are body data)."""
        return {"mode": self.mode, "worker_id": self.worker_id if self.mode != "off" else None,
                "demo": self.mode != "off", "source": "demo_input",
                "range_bpm": list(RANGES[self.mode]) if self.mode != "off" else None,
                "ramp_s": config.DEMO_HR_RAMP_S}
