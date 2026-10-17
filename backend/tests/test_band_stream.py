"""
The band's fast lines through the whole live path: fake band -> parser -> hub -> band stream
-> pipeline / man-down / SQLite / dashboard adapter. No hardware.
"""

import numpy as np
import pytest

from backend import config
from backend.alerts.band_output import MultiBand
from backend.app.frontend_api import FrontendAdapter
from backend.app.hub import Hub
from backend.app.schemas import BandSample, Reading, events_from_line
from backend.ingest.fake_band import FakeBand
from backend.storage.db import Database

T0 = 1_700_000_040.0            # a whole minute, so band minutes line up with pipeline minutes


class Clock:
    def __init__(self):
        self.t = T0

    def now(self):
        return self.t


def make_hub():
    clock = Clock()
    hub = Hub(band=MultiBand([]), db=Database(":memory:"))
    hub.real_clock = clock
    events = []
    hub.add_listener(events.append)
    return hub, clock, events


def play(hub, clock, band, seconds, tilt_lines=True):
    """Run the fake band for `seconds` in 200 ms steps, like UDP packets arriving."""
    start = getattr(band, "_played", 0.0)
    for step in range(int(seconds * 5)):
        t = start + step * 0.2
        clock.t = T0 + t
        lines = band.fast_lines(t)
        if step % 5 == 0 and tilt_lines:
            lines += band.lines_at(t)
        for line in lines:
            for ev in events_from_line(line, "w1", clock.t):
                hub.handle_input(ev, "udp")
        if step % 5 == 0:
            hub.tick()
    band._played = start + seconds


@pytest.fixture
def run_fall():
    """5 minutes of the fake band with sensors, a fall at 200 s (man-down check 45 s after)."""
    hub, clock, events = make_hub()
    hub.set_site_weather(hub.default_site_id, 28.0, temp_c=30.0, rh_pct=55.0)
    band = FakeBand(send_hr=False, fall_at=200, fall_for=180, sensors=True)
    play(hub, clock, band, 300)
    return hub, band, events


def test_fast_lines_are_not_stored_one_by_one(run_fall):
    hub, band, events = run_fall
    hub.db.commit()
    n_readings = hub.db.query("SELECT COUNT(*) FROM readings")[0][0]
    # 300 s of 50 ACC + 5 PPG lines a second = 16,500 lines; we keep ~1 reading a second.
    assert n_readings < 2 * 300
    assert hub.db.query("SELECT COUNT(*) FROM band_minutes")[0][0] >= 4
    assert not [e for e in events if e["type"] == "reading" and e.get("accel")]


def test_band_ppg_heart_rate_feeds_the_band_source(run_fall):
    hub, band, events = run_fall
    hrs = [e for e in events if e["type"] == "reading" and e.get("source") == "band_ppg"]
    assert len(hrs) >= 15
    errs = [abs(e["hr"] - band.true_hr(e["timestamp"] - T0 - 5)) for e in hrs]
    assert np.mean(errs) < 5
    assert all(0 <= e["hr_quality"] <= 1 for e in hrs)
    est = [e for e in events if e["type"] == "estimate"]
    assert any(e["hr_band"] is not None and "band" in e["hr_sources"] for e in est)
    assert all(e["hr_watch"] is None for e in est)        # band_ppg is the band device, not the watch


def test_accelerometer_fall_starts_the_man_down_check(run_fall):
    hub, band, events = run_fall
    (fall,) = [e for e in events if e["type"] == "band_fall"]
    assert fall["accepted"] and fall["confirmed_by"] == "pressure"
    (check,) = [e for e in events if e["type"] == "mandown_check"]
    assert check["reason"] == "fall"
    assert check["timestamp"] - fall["impact_at"] >= config.MANDOWN_STILL_S     # timing constant untouched
    assert check["timestamp"] - fall["impact_at"] <= config.MANDOWN_STILL_S + 12


def test_air_corrects_this_workers_wbgt_only(run_fall):
    hub, band, events = run_fall
    minutes = [e for e in events if e["type"] == "band_minute"]
    assert minutes[-1]["air_temp_c"] == pytest.approx(31, abs=1)
    assert minutes[-1]["wbgt_correction_c"] is not None
    p = hub.pipelines["w1"]
    p.set_context(wbgt=28.0)
    out = p._close_minute()
    assert out[0]["wbgt"] == pytest.approx(28.0 + minutes[-1]["wbgt_correction_c"], abs=0.01)
    # A stale correction is dropped.
    p.wbgt_correction_t -= 1000
    assert p._wbgt_correction(p.last_t) == 0.0


def test_tilt_switch_is_the_fallback_when_acc_stops():
    hub, clock, events = make_hub()
    band = FakeBand(send_hr=False, sensors=True)
    play(hub, clock, band, 10)
    # While ACC streams, the band's TILT:..,ACT:.. lines lose tilt/activity (the accelerometer owns them).
    out = hub.band_streams.filter_reading(Reading(worker_id="w1", timestamp=clock.t, tilt=0, activity=7, source="band"))
    assert out is None
    # ACC stops: the tilt switch line passes untouched again.
    clock.t += config.BAND_ACC_LIVE_S + 1
    r = Reading(worker_id="w1", timestamp=clock.t, tilt=1, activity=0, source="band")
    assert hub.band_streams.filter_reading(r) is r


def test_slow_slump_with_no_impact_still_caught_by_the_tilt_switch():
    """ACC streaming, no fall impact, but the tilt switch says tipped and the wrist is perfectly still."""
    hub, clock, events = make_hub()
    for i in range(int((config.MANDOWN_STILL_S + 5) * 50)):
        clock.t = T0 + i / 50
        hub.handle_input(BandSample(worker_id="w1", timestamp=clock.t, accel=(0.0, 0.0, 1.0)), "udp")
        if i % 50 == 0:
            for ev in events_from_line("TILT:1,ACT:0", "w1", clock.t):
                hub.handle_input(ev, "udp")
            hub.tick()
    assert [e for e in events if e["type"] == "mandown_check"]


def test_dashboard_gets_air_but_never_raw_samples():
    hub, clock, events = make_hub()
    fe = FrontendAdapter(hub)
    hub.add_listener(fe.on_event)
    sent = []
    fe.broadcast = sent.append
    hub.set_site_weather(hub.default_site_id, 28.0, temp_c=30.0, rh_pct=55.0)
    play(hub, clock, FakeBand(send_hr=False, sensors=True), 130)
    air = [m for m in sent if m["type"] == "band_air"]
    assert air and air[-1]["air"]["worker_id"] == "w1" and air[-1]["air"]["air_temp_c"] > 25
    assert set(air[-1]["air"]) >= {"air_temp_c", "humidity_pct", "wbgt_correction_c"}
    assert "hr_band_ppg" not in air[-1]["air"] and "activity" not in air[-1]["air"]   # no body data
    assert fe.snapshot({"view": "crew"})["air"]["w1"]["air_temp_c"] > 25
    vitals = [m for m in sent if m["type"] == "vitals"]
    assert len(vitals) < 2 * 130                                     # ~1 a second, not 55
