"""
The band stream: where the band's fast lines go instead of straight into the hub.

    PPG (5 lines/s) ─┐
    ACC (50 lines/s) ├─> BandStream (one per worker) ──> a few slow outputs ──> hub
    AIRT/HUM/BARO ───┘

What comes out, and how often:
    Reading(hr, hr_quality, source="band_ppg")     every ~5 s, ONLY when the pulse code is confident
    Reading(tilt, activity, source="band_acc")     once a second while ACC is streaming
    "band_fall" event                              when a possible fall is decided (accepted or not)
    "band_minute" event                            once a minute: activity, movement, pulse stats,
                                                   air temp / humidity / pressure, WBGT correction

So SQLite and the dashboards see ~1 reading a second (same as the old tilt line), never 55.
All the work is plain numpy on small windows (a few ms every 5 s), so it can't block the
event loop.

Tilt switch vs accelerometer (man-down):
    While ACC is streaming (heard in the last BAND_ACC_LIVE_S), the band's own "TILT:..,ACT:.."
    lines lose their tilt/activity fields here, and the once-a-second band_acc reading replaces
    them: activity from the accelerometer, and tilt = 1 when the accelerometer saw a fall OR the
    tilt switch says tipped (so a slow slump with no impact is still caught). When ACC stops,
    the tilt switch lines pass through untouched again (the fallback).
"""

import math
from collections import Counter

import numpy as np

from backend import config
from backend.app.schemas import BandSample, Reading
from backend.ingest.band_air import AirTracker
from backend.ingest.band_motion import ActivityTracker, FallDetector, TipDetector
from backend.ingest.band_pulse import PulseEstimator

MIN_SAMPLES_PER_SECOND = 10        # fewer ACC samples than this in a second: don't judge movement from it


class BandStream:
    def __init__(self, worker_id: str, use_nlms: bool = True):
        self.worker_id = worker_id
        self.pulse = PulseEstimator(use_nlms=use_nlms)
        self.activity = ActivityTracker()
        self.falls = FallDetector()
        self.tips = TipDetector()
        self.air = AirTracker()
        self.last_acc_t: float | None = None
        self.switch_tilt: int | None = None          # the tilt switch's last value (TILT line)
        self.switch_tilt_t: float | None = None
        self.skin_temp: float | None = None          # for the "air sensor touching skin" check
        self.minute: int | None = None
        self._new_minute_stats()

    def _new_minute_stats(self) -> None:
        self.m_activity: list[float] = []
        self.m_movement: list[float] = []
        self.m_hr: list[float] = []
        self.m_quality: list[float] = []
        self.m_cadence: list[float] = []
        self.m_reasons: Counter = Counter()
        self.m_falls = 0

    def acc_live(self, t: float) -> bool:
        return self.last_acc_t is not None and t - self.last_acc_t <= config.BAND_ACC_LIVE_S

    # ------------------------------------------------------------------
    def feed(self, s: BandSample, site_air: tuple[float, float] | None = None) -> tuple[list[Reading], list[dict]]:
        """One BandSample in -> (readings for the hub, events for the hub). Usually both empty."""
        t = s.timestamp
        readings: list[Reading] = []
        events = self._roll_minute(t, site_air)
        if s.accel is not None:
            self.last_acc_t = t
            self.pulse.add_acc(t, s.accel)
            sec = self.activity.add(t, s.accel)
            if sec is not None and sec.n >= MIN_SAMPLES_PER_SECOND:
                for ev in (self.falls.on_second(sec), self._tip(sec)):
                    if ev is not None:
                        self.m_falls += int(ev["type"] == "band_fall")
                        events.append({**ev, "worker_id": self.worker_id})
                self.m_activity.append(sec.activity)
                self.m_movement.append(sec.movement_g)
                readings.append(Reading(worker_id=self.worker_id, timestamp=sec.t, tilt=self._tilt(sec.t),
                                        activity=sec.activity, source="band_acc"))
            fall = self.falls.add_acc(t, s.accel)
            if fall is not None:
                self.m_falls += int(fall["accepted"])
                events.append({**fall, "worker_id": self.worker_id})
        if s.ppg is not None:
            r = self.pulse.add_ppg(t, s.ppg)
            if r is not None:
                self.m_reasons[r.reason] += 1
                if r.cadence_bpm:
                    self.m_cadence.append(r.cadence_bpm)
                if r.hr is not None:                      # never a made-up number: only confident ones go out
                    self.m_hr.append(r.hr)
                    self.m_quality.append(r.quality)
                    readings.append(Reading(worker_id=self.worker_id, timestamp=t, hr=r.hr,
                                            hr_quality=round(r.quality, 2), source="band_ppg"))
        if s.air_temp is not None or s.humidity is not None or s.pressure is not None:
            self.air.add(s.air_temp, s.humidity, s.pressure)
            if s.pressure is not None:
                self.falls.add_baro(t, s.pressure)
        return readings, events

    def _tip(self, sec):
        """Tipped-and-still check, unless a fall is being decided or already has the worker down."""
        if not self.tips.tipped and (self.falls.down or self.falls.pending is not None):
            return None
        return self.tips.on_second(sec)

    def _tilt(self, t: float) -> int:
        """
        1 = down: the accelerometer saw a fall, or the wrist tipped over and went still, or the
        tilt switch (older bands) says tipped (recently heard).
        """
        switch_fresh = self.switch_tilt_t is not None and t - self.switch_tilt_t <= config.BAND_ACC_LIVE_S
        return int(self.falls.down or self.tips.tipped or (switch_fresh and self.switch_tilt == 1))

    def filter_reading(self, r: Reading) -> Reading | None:
        """
        A Reading from the band's own lines. While ACC streams, its tilt/activity fields are
        dropped (the band_acc reading replaces them). None = nothing left to pass on.
        """
        if r.skin_temp is not None:
            self.skin_temp = r.skin_temp
        if r.tilt is not None:
            self.switch_tilt, self.switch_tilt_t = r.tilt, r.timestamp
        if r.source != "band" or not self.acc_live(r.timestamp) or (r.tilt is None and r.activity is None):
            return r
        rest = r.model_copy(update={"tilt": None, "activity": None})
        if all(getattr(rest, f) is None for f in ("hr", "skin_temp", "accel")):
            return None
        return rest

    # ------------------------------------------------------------------
    def _roll_minute(self, t: float, site_air) -> list[dict]:
        m = math.floor(t / 60)
        if self.minute is None:
            self.minute = m
        if m == self.minute:
            return []
        ev = self._close_minute((self.minute + 1) * 60.0, site_air)
        self.minute = m
        return [ev]

    def _close_minute(self, t: float, site_air) -> dict:
        mean = lambda xs, nd: round(float(np.mean(xs)), nd) if xs else None   # noqa: E731
        air = self.air.close_minute(t, self.skin_temp, site_air) or {}
        windows = sum(self.m_reasons.values())
        ev = {
            "type": "band_minute", "timestamp": t, "worker_id": self.worker_id, "minute_start": t - 60,
            "activity": mean(self.m_activity, 2), "movement_g": mean(self.m_movement, 3),
            "cadence_spm": round(float(np.median(self.m_cadence)), 1) if self.m_cadence else None,
            "hr_band_ppg": mean(self.m_hr, 1), "hr_quality": mean(self.m_quality, 2),
            "hr_windows": windows, "hr_given": len(self.m_hr), "hr_reasons": dict(self.m_reasons),
            "falls": self.m_falls, "down": self.falls.down or self.tips.tipped,
            "air_temp_c": air.get("air_temp_c"), "humidity_pct": air.get("humidity_pct"),
            "pressure_hpa": air.get("pressure_hpa"), "air_sane": air.get("air_sane"),
            "air_note": air.get("air_note"), "wbgt_correction_c": air.get("wbgt_correction_c"),
            "site_air_temp_c": air.get("site_air_temp_c"), "site_humidity_pct": air.get("site_humidity_pct"),
        }
        self._new_minute_stats()
        return ev


class BandStreams:
    """One BandStream per worker. site_air_of(worker_id) -> (temp °C, RH %) of that worker's site, or None."""

    def __init__(self, site_air_of=None, use_nlms: bool = True):
        self.streams: dict[str, BandStream] = {}
        self.site_air_of = site_air_of or (lambda wid: None)
        self.use_nlms = use_nlms

    def __contains__(self, worker_id: str) -> bool:
        return worker_id in self.streams

    def feed(self, s: BandSample) -> tuple[list[Reading], list[dict]]:
        stream = self.streams.get(s.worker_id)
        if stream is None:
            stream = self.streams[s.worker_id] = BandStream(s.worker_id, self.use_nlms)
        return stream.feed(s, self.site_air_of(s.worker_id))

    def filter_reading(self, r: Reading) -> Reading | None:
        stream = self.streams.get(r.worker_id)
        return r if stream is None else stream.filter_reading(r)

    def latest_air(self, worker_id: str) -> dict | None:
        stream = self.streams.get(worker_id)
        return None if stream is None else stream.air.latest

    def drop(self, worker_id: str) -> None:
        self.streams.pop(worker_id, None)
