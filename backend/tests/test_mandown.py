"""Man-down detector rules on their own."""

from backend import config
from backend.alerts.mandown import CHECKING, CONFIRMED, OK, ManDownDetector

S, E = config.MANDOWN_STILL_S, config.MANDOWN_ESCALATE_S


def lie_still(d, start, seconds):
    events = []
    for t in range(int(start), int(start + seconds) + 1):
        events += d.on_reading(float(t), tilt=1, activity=0.0)
    return events


def test_tipped_and_still_starts_check_after_still_time():
    d = ManDownDetector()
    ev = lie_still(d, 0, S - 1)
    assert ev == [] and d.state == OK
    ev = lie_still(d, S, 0)
    assert ev[0]["type"] == "mandown_check" and ev[0]["reason"] == "fall" and ev[0]["started_at"] == 0


def test_movement_resets_still_timer():
    d = ManDownDetector()
    lie_still(d, 0, S - 5)
    d.on_reading(S - 4, tilt=1, activity=3.0)      # wiggles
    assert lie_still(d, S - 3, 10) == []


def test_movement_cancels_check():
    d = ManDownDetector()
    lie_still(d, 0, S)
    assert d.state == CHECKING
    ev = d.on_reading(S + 5, tilt=0, activity=0.0)  # got up: switch flipped
    assert ev[0]["type"] == "mandown_cancelled" and ev[0]["by"] == "movement"
    assert d.state == OK


def test_confirmed_after_escalate_time_via_tick():
    d = ManDownDetector()
    lie_still(d, 0, S)
    assert d.tick(S + E - 1) == []
    ev = d.tick(S + E)
    assert ev[0]["type"] == "mandown_confirmed" and d.state == CONFIRMED
    assert ev[0]["seconds_since_start"] == S + E


def test_button_after_confirmed_only_reports():
    d = ManDownDetector()
    d.trigger(0, "heat_red_unacknowledged")
    d.tick(E)
    ev = d.on_button(E + 3)
    assert ev[0]["type"] == "mandown_worker_responded" and d.state == CONFIRMED
    assert d.resolve(E + 10)[0]["type"] == "mandown_resolved" and d.state == OK


def test_trigger_is_idempotent_and_temp_only_lines_dont_reset():
    d = ManDownDetector()
    lie_still(d, 0, 10)
    d.on_reading(11, tilt=None, activity=None)     # a TEMP-only line says nothing about movement
    assert d.still_since == 0
    d.trigger(12, "demo")
    assert d.trigger(13, "demo") == []
