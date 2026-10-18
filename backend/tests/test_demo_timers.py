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
