"""--demo-timers: man-down beeps after 10 s still and confirms 10 s later; off by default."""

from backend import config
from backend.alerts import mandown
from backend.alerts.mandown import ManDownDetector


def run(seconds_still_until_check, seconds_until_confirm):
    d = ManDownDetector()
    d.on_reading(0.0, tilt=1, activity=0.0)                 # tipped over, not moving
    t, checked, confirmed = 0.0, None, None
    while t < 200 and confirmed is None:
        t += 1.0
        for ev in d.on_reading(t, tilt=1, activity=0.0) + d.tick(t):
            if ev["type"] == "mandown_check" and checked is None:
                checked = t
            if ev["type"] == "mandown_confirmed":
                confirmed = t
    return checked, confirmed


def test_demo_timers_are_off_by_default_and_use_the_real_constants():
    assert not mandown.demo_timers_on()
    checked, confirmed = run(None, None)
    assert checked is not None and checked >= config.MANDOWN_STILL_S
    assert confirmed - checked >= config.MANDOWN_ESCALATE_S


def test_demo_timers_make_the_flow_fast_and_never_touch_config():
    real = (config.MANDOWN_STILL_S, config.MANDOWN_ESCALATE_S)
    mandown.use_demo_timers(True)
    try:
        checked, confirmed = run(None, None)
        assert checked is not None and checked <= config.DEMO_MANDOWN_STILL_S + 1
        assert confirmed - checked <= config.DEMO_MANDOWN_ESCALATE_S + 1
    finally:
        mandown.use_demo_timers(False)
    assert (config.MANDOWN_STILL_S, config.MANDOWN_ESCALATE_S) == real


def test_detected_fall_latches_only_the_button_stops_it():
    """Aiden's real-band test: a hard drop was detected, then sensor wobble cancelled the check over
    and over. After a detected fall, movement must not cancel; beep at +10 s, call at +20 s (demo)."""
    mandown.use_demo_timers(True)
    try:
        d = ManDownDetector()
        evs = d.on_fall(100.0, impact_at=100.0)
        t, checked, confirmed = 100.0, None, None
        while t < 130 and confirmed is None:
            t += 1.0
            act = 3.0 if int(t) % 2 else 0.0                  # wobble / being picked up: ignored
            for ev in evs + d.on_reading(t, tilt=0, activity=act) + d.tick(t):
                if ev["type"] == "mandown_check" and checked is None:
                    checked = t
                if ev["type"] == "mandown_cancelled":
                    raise AssertionError("movement must not cancel after a detected fall")
                if ev["type"] == "mandown_confirmed":
                    confirmed = t
            evs = []
        assert checked is not None and checked <= 100 + config.DEMO_MANDOWN_STILL_S + 1
        assert confirmed is not None and confirmed <= 100 + config.DEMO_MANDOWN_STILL_S + config.DEMO_MANDOWN_ESCALATE_S + 1
    finally:
        mandown.use_demo_timers(False)


def test_button_still_cancels_a_detected_fall():
    mandown.use_demo_timers(True)
    try:
        d = ManDownDetector()
        d.on_fall(0.0, impact_at=0.0)
        evs = []
        for t in range(1, 13):
            evs += d.on_reading(float(t), tilt=0, activity=0.0) + d.tick(float(t))
        assert any(e["type"] == "mandown_check" for e in evs)
        out = d.on_button(13.0)
        assert out and out[0]["type"] == "mandown_cancelled" and out[0]["by"] == "button"
        assert not d.fall_latched and d.state == "ok"
    finally:
        mandown.use_demo_timers(False)
