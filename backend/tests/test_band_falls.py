"""
Falls from the band accelerometer, the three ways a worker can go "down":
  drop (free fall + impact), impact over BAND_FALL_IMPACT_G (2.0 g), and tipped over + still.
Plus the things that must NOT count: clapping/hammering, walking, tipping over then moving.
"""

import numpy as np

from backend import config
from backend.alerts.band_output import MultiBand
from backend.app.frontend_api import fall_text
from backend.app.hub import Hub
from backend.app.schemas import BandSample
from backend.ingest import band_synth
from backend.ingest.band_stream import BandStream

FS = config.BAND_SAMPLE_HZ
UP, SIDE = np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0])
T0 = 1_700_000_040.0


def still(vec, seconds, rng, noise=0.003):
    return [vec + rng.normal(0, noise, 3) for _ in range(int(seconds * FS))]


def feed(stream: BandStream, samples, t_start=T0):
    """ACC samples at 50 Hz -> (band_fall/band_up events, band_acc readings)."""
    events, readings = [], []
    for i, a in enumerate(samples):
        r, e = stream.feed(BandSample(worker_id="w", timestamp=t_start + i / FS, accel=tuple(map(float, a))))
        events += [x for x in e if x["type"] in ("band_fall", "band_up")]
        readings += r
    return events, readings


def accepted(events):
    return [e for e in events if e["type"] == "band_fall" and e["accepted"]]


def test_free_fall_then_a_small_impact_is_a_fall():
    rng = np.random.default_rng(0)
    samples = still(UP, 5, rng) + [np.array([0.05, 0.05, 0.1])] * 15 + [np.array([0.0, 0.0, 1.9])] + still(SIDE, 6, rng)
    events, _ = feed(BandStream("w"), samples)
    (fall,) = accepted(events)
    assert fall["trigger"] == "free fall + impact" and fall["impact_g"] < config.BAND_FALL_IMPACT_G
    assert "FALL detected (free fall + impact" in fall_text(fall)


def test_short_wobble_or_free_fall_without_impact_is_not_a_fall():
    rng = np.random.default_rng(1)
    two_low = still(UP, 5, rng) + [np.array([0.1, 0.0, 0.1])] * 2 + [np.array([0.0, 0.0, 1.9])] + still(UP, 5, rng)
    # Impact comes too late after the free fall (1.5 s > BAND_FREEFALL_WINDOW_S) and is under 2.0 g.
    late = still(UP, 5, rng) + [np.array([0.1, 0.0, 0.1])] * 5 + still(UP, 1.5, rng) + [np.array([0.0, 0.0, 1.9])] \
        + still(UP, 5, rng)
    for samples in (two_low, late):
        events, _ = feed(BandStream("w"), samples)
        assert not [e for e in events if e["type"] == "band_fall"]


def test_cushion_slam_single_2_1_g_sample_flip_and_still_is_a_fall():
    rng = np.random.default_rng(2)
    samples = still(UP, 5, rng) + [np.array([0.3, 0.2, 2.1])] + still(SIDE, 6, rng)
    events, readings = feed(BandStream("w"), samples)
    (fall,) = accepted(events)
    assert fall["trigger"] == "impact" and fall["angle_deg"] > 80
    assert readings[-1].tilt == 1                                       # the man-down detector sees "down"


def test_clapping_and_hammering_are_not_falls():
    rng = np.random.default_rng(3)
    samples = still(UP, 5, rng)
    for _ in range(20):                                                 # a hit every 0.5 s, same wrist angle
        samples += [UP * 2.6] + [UP + rng.normal(0, 0.15, 3) for _ in range(FS // 2 - 1)]
    samples += still(UP, 5, rng)
    events, readings = feed(BandStream("w"), samples)
    falls = [e for e in events if e["type"] == "band_fall"]
    assert falls and not accepted(falls)                                # decided, and each one rejected
    assert {e["reason"] for e in falls} <= {"same_angle", "kept_moving"}
    assert all("possible fall ignored" in fall_text(e) for e in falls)
    assert all(r.tilt == 0 for r in readings)


def test_walking_with_arm_swing_is_not_a_fall():
    rng = np.random.default_rng(4)
    acc, _ = band_synth.motion("walk", 180 * FS, rng)
    events, readings = feed(BandStream("w"), list(acc))
    assert not accepted(events)
    assert all(r.tilt == 0 for r in readings)


def slow_tip(rng, upright_s=70, turn_s=10, after=None):
    """Upright, then the wrist slowly rolls 90° (no jolt), then `after` samples."""
    samples = still(UP, upright_s, rng)
    for i in range(turn_s * FS):
        a = np.radians(90) * (i + 1) / (turn_s * FS)
        samples.append(np.array([np.sin(a), 0.0, np.cos(a)]) + rng.normal(0, 0.003, 3))
    return samples + (after if after is not None else [])


def run_hub(samples):
    hub = Hub(band=MultiBand([]))
    clock = type("Clock", (), {"t": T0, "now": lambda self: self.t})()
    hub.real_clock = clock
    events = []
    hub.add_listener(events.append)
    for i, a in enumerate(samples):
        clock.t = T0 + i / FS
        hub.handle_input(BandSample(worker_id="w1", timestamp=clock.t, accel=tuple(map(float, a))), "ble_band")
        if i % FS == 0:
            hub.tick()
    return events


def test_slow_tip_over_then_still_starts_the_man_down_check():
    rng = np.random.default_rng(5)
    events = run_hub(slow_tip(rng, after=still(SIDE, config.MANDOWN_STILL_S + 10, rng)))
    (tip,) = [e for e in events if e["type"] == "band_fall"]
    assert tip["accepted"] and tip["trigger"] == "tipped" and tip["angle_deg"] >= config.BAND_TIPPED_ANGLE_DEG
    (check,) = [e for e in events if e["type"] == "mandown_check"]
    assert check["reason"] == "fall"
    assert check["timestamp"] - tip["timestamp"] >= config.MANDOWN_STILL_S - 1      # timing constant untouched
    assert not [e for e in events if e["type"] == "mandown_cancelled"]


def test_tip_over_then_moving_gives_no_check():
    rng = np.random.default_rng(6)
    moving = [SIDE + rng.normal(0, 0.2, 3) for _ in range((config.MANDOWN_STILL_S + 10) * FS)]
    events = run_hub(slow_tip(rng, after=still(SIDE, 3, rng) + moving))
    assert not [e for e in events if e["type"] == "mandown_check"]
    ups = [e for e in events if e["type"] == "band_up"]
    assert ups and ups[0]["reason"] == "moving again"


def test_lying_still_too_long_never_resets_the_usual_angle():
    """While down, the 'usual angle' is frozen: a 3-minute lie-down can't turn itself into 'upright'."""
    rng = np.random.default_rng(7)
    events = run_hub(slow_tip(rng, after=still(SIDE, 180, rng)))
    assert not [e for e in events if e["type"] in ("band_up", "mandown_cancelled")]
    assert [e for e in events if e["type"] == "mandown_confirmed"]
