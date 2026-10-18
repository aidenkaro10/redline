"""
The band's accelerometer (ACC:x,y,z in g, 50 per second): activity and falls.

Activity (every second)
    movement_g = how much the three axes wobble around their average in that second
                 (turning the wrist counts, not only shaking).
    activity   = 0 below BAND_STILL_G (perfectly still), else BAND_ACT_PER_G * movement_g.
    That puts the accelerometer on the same scale as the old tilt switch count (ACT: flickers
    per second), which is what the forecaster was trained on.

Falls (FallDetector)
    1. Impact: total acceleration spikes above BAND_FALL_IMPACT_G (2.5 g).
    2. New angle: the wrist's "which way is down" before vs after differs by BAND_FALL_ANGLE_DEG.
    3. Stillness: mostly still for BAND_FALL_STILL_S right after.
    4. Pressure check (only when the air sensor sends BARO and it's steady): falling to the
       ground lowers the band ~0.5-1 m, so air pressure goes UP by ~0.06-0.12 hPa.
       Rise >= BAND_FALL_BARO_MIN_HPA -> confirmed. No rise -> only a very hard impact
       (BAND_FALL_HARD_IMPACT_G) still counts. Noisy or missing pressure -> steps 1-3 decide.
    An accepted fall puts the worker "down" (tilt = 1 for the man-down detector) until they
    move for BAND_GOT_UP_S in a row. The man-down timers themselves (MANDOWN_STILL_S,
    MANDOWN_ESCALATE_S) are untouched: this only decides what "tipped over" means.
"""

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from backend import config

FS = config.BAND_SAMPLE_HZ
FALL_STILL_MAX_G = 0.12            # "mostly still" after a fall: a twitch or two is fine (4x BAND_STILL_G)
PRE_S = (2.0, 0.5)                 # wrist angle "before" = average from 2.0 s to 0.5 s before the impact
SETTLE_S = 1.0                     # ignore the first second after the impact (bouncing)
BARO_PRE_S = 10.0                  # pressure "before" = the 10 s before the impact
BARO_POST_S = (3.0, 8.0)           # pressure "after" = 3-8 s after the impact
BARO_FRESH_S = 3.0                 # pressure counts as streaming if heard this recently


def activity_from_motion(movement_g: float) -> float:
    """Accelerometer movement (g) -> tilt-switch-style activity count (0 = perfectly still)."""
    if not math.isfinite(movement_g) or movement_g < config.BAND_STILL_G:
        return 0.0
    return round(min(config.BAND_ACT_PER_G * movement_g, 200.0), 2)


def movement(samples) -> float:
    """sqrt(var x + var y + var z), in g. 0 for fewer than 2 samples."""
    a = np.asarray(samples, dtype=float)
    if len(a) < 2:
        return 0.0
    return float(np.sqrt(np.sum(np.var(a, axis=0))))


def _angle_deg(u: np.ndarray, v: np.ndarray) -> float:
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu == 0 or nv == 0:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(u @ v / (nu * nv), -1, 1))))


@dataclass
class Second:
    """One second of accelerometer data, summarized."""
    t: float                 # laptop time at the end of that second
    movement_g: float
    activity: float          # tilt-switch-style count
    n: int                   # samples heard (50 when nothing was lost)


class ActivityTracker:
    """Collects ACC samples and closes one Second each time the clock passes a whole second."""

    def __init__(self):
        self.samples: list = []
        self.second: int | None = None

    def add(self, t: float, xyz) -> Second | None:
        sec = math.floor(t)
        done = None
        if self.second is not None and sec != self.second and self.samples:
            m = movement(self.samples)
            done = Second(float(self.second + 1), m, activity_from_motion(m), len(self.samples))
            self.samples = []
        self.second = sec
        self.samples.append(xyz)
        return done


class FallDetector:
    """
    Feed every ACC sample (add_acc) and every BARO reading (add_baro). add_acc returns a
    "band_fall" event dict when a fall candidate is decided (accepted or not), else None.
    `down` is True from an accepted fall until the worker moves again.
    """

    def __init__(self):
        self.acc: deque = deque(maxlen=int(12 * FS))         # (t, x, y, z), last ~12 s
        self.baro: deque = deque(maxlen=60)                  # (t, hPa), last ~60 s
        self.pending: dict | None = None
        self.down = False
        self.down_since: float | None = None
        self.moving_since: float | None = None

    def add_baro(self, t: float, hpa: float) -> None:
        self.baro.append((t, hpa))

    def on_second(self, sec: Second) -> None:
        """Once a second: a worker who's down and keeps moving for BAND_GOT_UP_S got back up."""
        if not self.down:
            return
        if sec.activity > 0:
            self.moving_since = self.moving_since if self.moving_since is not None else sec.t - 1
            if sec.t - self.moving_since >= config.BAND_GOT_UP_S:
                self.down, self.down_since, self.moving_since = False, None, None
        else:
            self.moving_since = None

    def add_acc(self, t: float, xyz) -> dict | None:
        self.acc.append((t, *xyz))
        g = math.sqrt(xyz[0] ** 2 + xyz[1] ** 2 + xyz[2] ** 2)
        if self.pending is None and not self.down and g >= config.BAND_FALL_IMPACT_G:
            self.pending = {"t": t, "impact_g": g}
        elif self.pending is not None and t - self.pending["t"] < 0.5:
            self.pending["impact_g"] = max(self.pending["impact_g"], g)     # keep the biggest spike
        return self._decide(t) if self.pending is not None else None

    # -- the decision, once enough time has passed after the impact -------------------
    def _window(self, start: float, end: float) -> np.ndarray:
        rows = [r[1:] for r in self.acc if start <= r[0] < end]
        return np.array(rows, dtype=float).reshape(-1, 3)

    def _decide(self, t: float) -> dict | None:
        p = self.pending
        t0 = p["t"]
        accel_done = t0 + SETTLE_S + config.BAND_FALL_STILL_S
        if t < accel_done:
            return None
        if "angle_deg" not in p:
            before = self._window(t0 - PRE_S[0], t0 - PRE_S[1])
            after = self._window(t0 + SETTLE_S, accel_done)
            if len(before) < 5 or len(after) < 5:
                return self._finish(t, False, "not_enough_data")
            p["angle_deg"] = round(_angle_deg(before.mean(axis=0), after.mean(axis=0)), 1)
            p["after_movement_g"] = round(movement(after), 3)
            p["before"] = before.mean(axis=0)
            if p["angle_deg"] < config.BAND_FALL_ANGLE_DEG:
                return self._finish(t, False, "same_angle")        # e.g. clapping, hammering
            if p["after_movement_g"] > FALL_STILL_MAX_G:
                return self._finish(t, False, "kept_moving")       # e.g. jumped down and walked on

        # Pressure check, if the air sensor is streaming.
        streaming = bool(self.baro) and self.baro[-1][0] >= t0 - BARO_FRESH_S
        if not streaming:
            return self._finish(t, True, None, confirmed_by="accelerometer")
        if t < t0 + BARO_POST_S[1]:
            return None                                             # wait for the "after" pressure
        pre = [h for (tb, h) in self.baro if t0 - BARO_PRE_S <= tb < t0]
        post = [h for (tb, h) in self.baro if t0 + BARO_POST_S[0] <= tb <= t0 + BARO_POST_S[1]]
        if len(pre) < 3 or len(post) < 2 or np.std(pre) > config.BAND_FALL_BARO_NOISE_HPA:
            return self._finish(t, True, None, confirmed_by="accelerometer (pressure too noisy)")
        rise = float(np.median(post) - np.median(pre))
        p["baro_rise_hpa"] = round(rise, 3)
        if rise >= config.BAND_FALL_BARO_MIN_HPA:
            return self._finish(t, True, None, confirmed_by="pressure")
        if p["impact_g"] >= config.BAND_FALL_HARD_IMPACT_G:
            return self._finish(t, True, None, confirmed_by="hard impact")
        return self._finish(t, False, "no_height_drop")

    def _finish(self, t: float, accepted: bool, reason: str | None, confirmed_by: str | None = None) -> dict:
        p, self.pending = self.pending, None
        if accepted:
            self.down, self.down_since, self.moving_since = True, t, None
        return {"type": "band_fall", "timestamp": t, "impact_at": p["t"], "accepted": accepted,
                "reason": reason, "confirmed_by": confirmed_by, "impact_g": round(p["impact_g"], 2),
                "angle_deg": p.get("angle_deg"), "after_movement_g": p.get("after_movement_g"),
                "baro_rise_hpa": p.get("baro_rise_hpa")}
