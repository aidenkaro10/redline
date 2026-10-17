"""Accelerometer activity + falls (band_motion.py) and the air sensor (band_air.py)."""

import numpy as np
import pytest

from backend import config
from backend.ingest.band_air import AirTracker, wbgt_delta
from backend.ingest.band_motion import ActivityTracker, FallDetector, activity_from_motion

FS = config.BAND_SAMPLE_HZ


def fall_samples(impact_g=3.5, new_down=(1.0, 0.0, 0.0), after_noise=0.003, seconds=20, seed=0):
    """5 s upright and still, an impact, then lying at a new angle. Returns [(t, xyz)]."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(seconds * FS):
        t = 100.0 + i / FS
        if i < 5 * FS:
            a = np.array([0.0, 0.0, 1.0]) + rng.normal(0, 0.003, 3)
        elif i < 5 * FS + 3:
            a = np.array([0.0, 0.0, impact_g])
        else:
            a = np.array(new_down) + rng.normal(0, after_noise, 3)
        out.append((t, tuple(a)))
    return out


def run(det: FallDetector, samples, baro=None):
    """Feed samples (and baro [(t, hPa)]) in time order; return the band_fall events."""
    events, baro = [], list(baro or [])
    for t, xyz in samples:
        while baro and baro[0][0] <= t:
            det.add_baro(*baro.pop(0))
        ev = det.add_acc(t, xyz)
        if ev:
            events.append(ev)
    return events


def baro_series(rise, noise=0.005, seed=1):
    rng = np.random.default_rng(seed)
    return [(100.0 + s, 1012.0 + rng.normal(0, noise) + (rise if s >= 6 else 0.0)) for s in range(20)]


def test_activity_scale_matches_the_tilt_switch_count():
    assert activity_from_motion(0.005) == 0.0                         # still -> exactly 0 (man-down needs 0)
    assert activity_from_motion(0.25) == pytest.approx(config.BAND_ACT_PER_G * 0.25)
    assert activity_from_motion(float("nan")) == 0.0
    tracker = ActivityTracker()
    secs = [tracker.add(10 + i / FS, (0.0, 0.0, 1.0)) for i in range(2 * FS + 1)]
    secs = [s for s in secs if s]
    assert len(secs) == 2 and all(s.activity == 0 and s.n == FS for s in secs)


def test_fall_without_pressure_sensor_is_accepted_on_the_accelerometer():
    det = FallDetector()
    (ev,) = run(det, fall_samples())
    assert ev["accepted"] and ev["confirmed_by"] == "accelerometer"
    assert ev["impact_g"] >= 3.4 and ev["angle_deg"] > 80
    assert det.down


def test_fall_confirmed_by_a_pressure_rise():
    det = FallDetector()
    (ev,) = run(det, fall_samples(), baro_series(rise=0.10))
    assert ev["accepted"] and ev["confirmed_by"] == "pressure" and ev["baro_rise_hpa"] == pytest.approx(0.1, abs=0.02)


def test_soft_impact_with_no_height_drop_is_rejected_but_a_hard_one_counts():
    det = FallDetector()
    (ev,) = run(det, fall_samples(impact_g=2.8), baro_series(rise=0.0))
    assert not ev["accepted"] and ev["reason"] == "no_height_drop" and not det.down
    det = FallDetector()
    (ev,) = run(det, fall_samples(impact_g=4.5), baro_series(rise=0.0))
    assert ev["accepted"] and ev["confirmed_by"] == "hard impact"


def test_noisy_pressure_is_ignored_not_trusted():
    det = FallDetector()
    (ev,) = run(det, fall_samples(impact_g=2.8), baro_series(rise=0.0, noise=0.2))
    assert ev["accepted"] and "noisy" in ev["confirmed_by"]


def test_bumps_that_are_not_falls():
    det = FallDetector()
    (ev,) = run(det, fall_samples(new_down=(0.0, 0.0, 1.0)))      # clap/hammer: same angle after
    assert not ev["accepted"] and ev["reason"] == "same_angle"
    det = FallDetector()
    (ev,) = run(det, fall_samples(after_noise=0.3))                # jumped down and kept moving
    assert not ev["accepted"] and ev["reason"] == "kept_moving"


def test_getting_up_clears_down():
    det = FallDetector()
    run(det, fall_samples())
    assert det.down
    tracker = ActivityTracker()
    rng = np.random.default_rng(0)
    for i in range(int((config.BAND_GOT_UP_S + 2) * FS)):
        sec = tracker.add(200 + i / FS, tuple(np.array([0, 0, 1.0]) + rng.normal(0, 0.3, 3)))
        if sec:
            det.on_second(sec)
    assert not det.down


# ---------------------------------------------------------------------------
# Air sensor
# ---------------------------------------------------------------------------
def minute(tracker, temp, hum, n=60, temp_sd=0.03):
    rng = np.random.default_rng(0)
    for _ in range(n):
        tracker.add(temp + rng.normal(0, temp_sd), hum, 1012.0)


def test_wbgt_delta_direction():
    assert wbgt_delta(30, 50, 30, 50) == pytest.approx(0)
    assert wbgt_delta(33, 50, 30, 50) > 1.5            # hotter at the worker -> higher WBGT
    assert wbgt_delta(30, 70, 30, 50) > 0              # more humid -> higher
    assert wbgt_delta(28, 50, 30, 50) < 0


def test_sane_minute_gives_a_capped_correction():
    air = AirTracker()
    minute(air, 36.0, 60)
    out = air.close_minute(60.0, skin_temp=34.0, site_air=(30.0, 50.0))
    assert out["air_sane"] and out["wbgt_correction_c"] == config.WBGT_LOCAL_MAX_UP_C   # capped
    air = AirTracker()
    minute(air, 27.0, 50)
    out = air.close_minute(60.0, skin_temp=34.0, site_air=(30.0, 50.0))
    assert out["wbgt_correction_c"] == -config.WBGT_LOCAL_MAX_DOWN_C


@pytest.mark.parametrize("why,temp,hum,n,sd,skin,site", [
    ("too few", 31.0, 55, 5, 0.03, 34.0, (30.0, 50.0)),
    ("jumping", 31.0, 55, 60, 3.0, 34.0, (30.0, 50.0)),
    ("skin", 33.5, 55, 60, 0.03, 34.0, (30.0, 50.0)),
    ("no site", 31.0, 55, 60, 0.03, 34.0, None),
    ("too far", 45.0, 55, 60, 0.03, 34.0, (30.0, 50.0)),
])
def test_insane_air_gives_no_correction(why, temp, hum, n, sd, skin, site):
    air = AirTracker()
    minute(air, temp, hum, n=n, temp_sd=sd)
    out = air.close_minute(60.0, skin_temp=skin, site_air=site)
    assert not out["air_sane"] and out["wbgt_correction_c"] is None, why


def test_bmp280_without_humidity_still_reports_temperature():
    air = AirTracker()
    for _ in range(60):
        air.add(31.0, None, 1012.0)
    out = air.close_minute(60.0, site_air=(30.0, 50.0))
    assert out["air_temp_c"] == 31.0 and out["humidity_pct"] is None and out["wbgt_correction_c"] is None
    assert air.close_minute(120.0) is None                        # nothing new this minute
