"""Labeled demo heart rate input: ranges, gradual ramps, off, and the pipeline ignoring other sources."""

import random

import pytest

from backend import config
from backend.alerts.band_output import MultiBand
from backend.app.demo_hr import DemoHeartRate
from backend.app.hub import Hub
from backend.app.schemas import Reading

REST, ACTIVE, RAMP = config.DEMO_HR_REST_BPM, config.DEMO_HR_ACTIVE_BPM, config.DEMO_HR_RAMP_S


def run(gen, t0, seconds):
    return [(t, v) for t in range(int(t0), int(t0 + seconds)) if (v := gen.next(float(t))) is not None]


def test_rest_stays_in_range_one_reading_per_second():
    gen = DemoHeartRate(random.Random(1))
    gen.set_mode("rest", "w1", 0.0)
    vals = run(gen, 0, 600)
    assert len(vals) == 600
    after_ramp = [v for t, v in vals if t >= RAMP]
    assert all(REST[0] <= v <= REST[1] for v in after_ramp)
    assert gen.next(599.5) is None                         # never more than one per second


def test_rest_to_active_climbs_gradually_then_stays_in_range():
    for seed in range(5):
        gen = DemoHeartRate(random.Random(seed))
        gen.set_mode("rest", "w1", 0.0)
        run(gen, 0, 120)
        gen.set_mode("active", "w1", 120.0)
        vals = run(gen, 120, 600)
        steps = [b - a for (_, a), (_, b) in zip(vals, vals[1:])]
        biggest = (ACTIVE[1] - REST[0]) / RAMP + 2 * config.DEMO_HR_JITTER_BPM + 0.5
        assert max(abs(s) for s in steps) <= biggest       # never jumps
        assert vals[0][1] < ACTIVE[0]                       # starts low...
        mid = [v for t, v in vals if t == 120 + RAMP // 2][0]
        assert REST[1] < mid < ACTIVE[0] + 5                # ...is halfway-ish half way through...
        assert all(ACTIVE[0] <= v <= ACTIVE[1] for t, v in vals if t >= 120 + RAMP)   # ...then in range


def test_off_stops_and_restarts_from_rest():
    gen = DemoHeartRate(random.Random(2))
    gen.set_mode("active", "w1", 0.0, start_bpm=62)
    run(gen, 0, 100)
    gen.set_mode("off", "w1", 100.0)
    assert run(gen, 100, 60) == []
    assert gen.state() == {"mode": "off", "worker_id": None, "demo": False, "source": "demo_input",
                           "range_bpm": None, "ramp_s": RAMP}
    with pytest.raises(ValueError):
        gen.set_mode("sprint", "w1", 0.0)


class Clock:
    speed = 1.0

    def __init__(self):
        self.t = 1_000_000.0

    def now(self):
        return self.t


def test_hub_injects_labeled_readings_and_ignores_other_sources():
    hub = Hub(band=MultiBand([]))
    clock = Clock()
    hub.register_worker("w1", clock=clock)
    events = []
    hub.add_listener(events.append)
    hub.set_demo_heart_rate("rest", "w1")
    assert hub.pipelines["w1"].demo_hr_only
    for _ in range(150):
        clock.t += 1
        hub.handle_input(Reading(worker_id="w1", timestamp=clock.t, hr=190.0, source="watch"), source="watch")
        hub.tick()
    est = [e for e in events if e["type"] == "estimate"]
    assert est and all(e["hr_sources"] == ["demo_input"] and e["hr_source"] == "demo_input" for e in est)
    assert all(REST[0] - 3 <= e["hr"] <= REST[1] + 3 for e in est)    # the watch's 190 never got in
    demo = [e for e in events if e["type"] == "reading" and e.get("source") == "demo_input"]
    assert len(demo) >= 140
    mode_events = [e for e in events if e["type"] == "demo_heart_rate"]
    assert mode_events[0]["mode"] == "rest" and mode_events[0]["demo"] is True
    hub.set_demo_heart_rate("off", "w1")
    assert not hub.pipelines["w1"].demo_hr_only
    n = len([e for e in events if e["type"] == "reading" and e.get("source") == "demo_input"])
    for _ in range(10):
        clock.t += 1
        hub.tick()
    assert len([e for e in events if e["type"] == "reading" and e.get("source") == "demo_input"]) == n


def test_walk_is_centered_on_70_and_110_and_always_inside_the_range():
    for mode, (lo, hi), mid in (("rest", REST, 70), ("active", ACTIVE, 110)):
        assert (lo + hi) / 2 == mid
        for seed in range(5):
            gen = DemoHeartRate(random.Random(seed))
            gen.set_mode(mode, "w1", 0.0, start_bpm=mid)
            vals = [v for t, v in run(gen, 0, 1800) if t >= RAMP]
            assert all(lo <= v <= hi for v in vals)                    # clamped after jitter, every value
            assert abs(sum(vals) / len(vals) - mid) < 1.5               # centered on the middle
