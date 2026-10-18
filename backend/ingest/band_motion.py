"""
The band's accelerometer (ACC:x,y,z in g, 50 per second): activity and falls.

Activity (every second)
    movement_g = how much the three axes wobble around their average in that second
                 (turning the wrist counts, not only shaking).
    activity   = 0 below BAND_STILL_G (perfectly still), else BAND_ACT_PER_G * movement_g.
    That puts the accelerometer on the same scale as the old tilt switch count (ACT: flickers
    per second), which is what the forecaster was trained on.

Falls (FallDetector)
    1. Impact: total acceleration spikes above BAND_FALL_IMPACT_G (2.0 g), OR a drop: free fall
       (under BAND_FREEFALL_G for BAND_FREEFALL_MIN_SAMPLES samples in a row) followed within
       BAND_FREEFALL_WINDOW_S by an impact over BAND_FREEFALL_IMPACT_G (1.8 g).
       (The firmware sends the peak of each 20 ms window when there's a jolt, so short impacts arrive.)
    2. New angle: the wrist's "which way is down" before vs after differs by BAND_FALL_ANGLE_DEG.
    3. Stillness: mostly still for BAND_FALL_STILL_S right after.
    4. Pressure check (only when the air sensor sends BARO and it's steady): falling to the
       ground lowers the band ~0.5-1 m, so air pressure goes UP by ~0.06-0.12 hPa.
       Rise >= BAND_FALL_BARO_MIN_HPA -> confirmed. No rise -> only a very hard impact
       (BAND_FALL_HARD_IMPACT_G) still counts. Noisy or missing pressure -> steps 1-3 decide.
    An accepted fall puts the worker "down" (tilt = 1 for the man-down detector) until they
    move for BAND_GOT_UP_S in a row.

Tipped over and still, no jolt (TipDetector): the wrist ends up BAND_TIPPED_ANGLE_DEG (60°) from
its usual angle (median of the last BAND_TIPPED_BASELINE_S) while perfectly still. That's the old
tilt-switch rule with the accelerometer: it puts the worker "down" too, and the man-down detector
still waits MANDOWN_STILL_S of stillness before its check. Moving (or the wrist back near its old
angle) clears it. The man-down timers themselves (MANDOWN_STILL_S,
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
TIP_MIN_HISTORY_S = 10             # need this many seconds of "usual angle" before judging a tip


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
    mean: tuple = (0.0, 0.0, 1.0)   # average x, y, z (g): which way is down for the wrist


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
            done = Second(float(self.second + 1), m, activity_from_motion(m), len(self.samples),
                          tuple(np.mean(np.asarray(self.samples, dtype=float), axis=0)))
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
        self.ff_run = 0                                      # samples in a row below BAND_FREEFALL_G
        self.ff_start: float | None = None                   # when that run started
        self.ff_until: float | None = None                   # a real free fall seen: impact window ends then

    def add_baro(self, t: float, hpa: float) -> None:
        self.baro.append((t, hpa))

    def on_second(self, sec: Second) -> dict | None:
        """Once a second: a worker who's down and keeps moving for BAND_GOT_UP_S got back up."""
        if not self.down:
            return None
        if sec.activity > 0:
            self.moving_since = self.moving_since if self.moving_since is not None else sec.t - 1
            if sec.t - self.moving_since >= config.BAND_GOT_UP_S:
                self.down, self.down_since, self.moving_since = False, None, None
                return {"type": "band_up", "timestamp": sec.t, "after": "fall", "reason": "moving again"}
        else:
            self.moving_since = None
        return None

    def add_acc(self, t: float, xyz) -> dict | None:
        self.acc.append((t, *xyz))
        g = math.sqrt(xyz[0] ** 2 + xyz[1] ** 2 + xyz[2] ** 2)
        # Free fall: a few samples in a row with almost no acceleration (the band is dropping).
        if g < config.BAND_FREEFALL_G:
            self.ff_run += 1
            if self.ff_run == 1:
                self.ff_start = t
            if self.ff_run >= config.BAND_FREEFALL_MIN_SAMPLES:
                self.ff_until = t + config.BAND_FREEFALL_WINDOW_S
        else:
            self.ff_run = 0
        if self.ff_until is not None and t > self.ff_until:
            self.ff_until = None                             # no impact in time: just a wobble
        if self.pending is None and not self.down:
            if self.ff_until is not None and g > config.BAND_FREEFALL_IMPACT_G:
                self.pending = {"t": t, "impact_g": g, "trigger": "free fall + impact", "start": self.ff_start}
                self.ff_until = None
            elif g >= config.BAND_FALL_IMPACT_G:
                self.pending = {"t": t, "impact_g": g, "trigger": "impact", "start": t}
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
            start = p.get("start", t0)                         # before the free fall, if there was one
            before = self._window(start - PRE_S[0], start - PRE_S[1])
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
                "trigger": p.get("trigger", "impact"),
                "reason": reason, "confirmed_by": confirmed_by, "impact_g": round(p["impact_g"], 2),
                "angle_deg": p.get("angle_deg"), "after_movement_g": p.get("after_movement_g"),
                "baro_rise_hpa": p.get("baro_rise_hpa")}


class TipDetector:
    """
    Tipped over and still, no jolt needed (the old tilt-switch rule, done with the accelerometer).
    Feed one Second at a time. Returns a "band_fall" event (trigger "tipped") when the worker goes
    down, a "band_up" event when they're back, else None. `tipped` = down right now.
    """

    def __init__(self):
        self.history: deque = deque(maxlen=int(config.BAND_TIPPED_BASELINE_S))   # wrist angle each second
        self.tipped = False
        self.ref: np.ndarray | None = None          # the usual angle, frozen while tipped
        self.moving_since: float | None = None

    def on_second(self, sec: Second) -> dict | None:
        v = np.asarray(sec.mean, dtype=float)
        if not np.all(np.isfinite(v)) or np.linalg.norm(v) < 0.3:
            return None                              # in free fall or junk: no angle this second
        v = v / np.linalg.norm(v)
        if not self.tipped:
            event = None
            if len(self.history) >= TIP_MIN_HISTORY_S and sec.activity == 0:
                ref = np.median(np.array(self.history), axis=0)
                angle = _angle_deg(ref, v)
                if angle >= config.BAND_TIPPED_ANGLE_DEG:
                    self.tipped, self.ref, self.moving_since = True, ref, None
                    return {"type": "band_fall", "timestamp": sec.t, "impact_at": None, "accepted": True,
                            "trigger": "tipped", "reason": None, "confirmed_by": "tipped over and still",
                            "impact_g": None, "angle_deg": round(angle, 1), "after_movement_g": round(sec.movement_g, 3),
                            "baro_rise_hpa": None}
            self.history.append(v)
            return event
        # Down: back up once moving for BAND_GOT_UP_S, or the wrist is back near its usual angle.
        back = _angle_deg(self.ref, v) < config.BAND_TIPPED_ANGLE_DEG / 2
        if sec.activity > 0:
            self.moving_since = self.moving_since if self.moving_since is not None else sec.t - 1
        else:
            self.moving_since = None
        moved = self.moving_since is not None and sec.t - self.moving_since >= config.BAND_GOT_UP_S
        if back or moved:
            self.tipped, self.ref, self.moving_since = False, None, None
            self.history.append(v)
            return {"type": "band_up", "timestamp": sec.t, "after": "tipped",
                    "reason": "wrist back at its usual angle" if back else "moving again"}
        return None
