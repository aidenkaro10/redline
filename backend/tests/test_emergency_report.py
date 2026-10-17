"""The call script and follow-up answers come only from the event data."""

from backend.emergency.events import MandownConfirmed
from backend.emergency.report import answer_question, build_report, clock, crew_script, emergency_script

T0 = 1_760_000_000.0


def event(**overrides):
    data = dict(worker_id="worker-1", worker_name="Alex Rivera", site_name="Deerhaven Generating Station",
                lat=29.7581, lon=-82.3898, started_at=T0, confirmed_at=T0 + 105,
                last_movement_at=T0, core_temp_c=39.12, hr_bpm=141.6)
    data.update(overrides)
    return MandownConfirmed(**data)


def test_emergency_script_has_every_fact():
    r = build_report(event(), now=T0 + 90)
    s = emergency_script(r)
    for part in ["Alex Rivera", "Deerhaven Generating Station", "29.75810 north, 82.38980 west",
                 "collapsed, no movement for 90 seconds", "39.1 degrees Celsius", "142 beats per minute",
                 clock(T0), "90 seconds ago"]:
        assert part in s, part


def test_crew_script_says_how_to_cancel():
    s = crew_script(build_report(event(), now=T0 + 90), cancel_window_s=30)
    assert "30 seconds" in s and "cancel" in s


def test_missing_data_is_said_as_unknown_not_invented():
    r = build_report(event(worker_name=None, site_name=None, lat=None, lon=None,
                           core_temp_c=None, hr_bpm=None), now=T0 + 90)
    s = emergency_script(r)
    assert "worker ID worker-1" in s
    assert "temperature unknown" in s and "heart rate unknown" in s
    assert "GPS Unknown" in s
    assert answer_question("What's his temperature?", r).startswith("Unknown")
    assert answer_question("What's his pulse?", r).startswith("Unknown")


def test_follow_up_answers():
    r = build_report(event(), now=T0 + 200)
    assert answer_question("Is he breathing?", r) == f"Unknown. Last movement was at {clock(T0)}."
    assert "GPS 29.75810 north" in answer_question("Where exactly is he?", r)
    assert "39.1" in answer_question("What's his temperature?", r)
    assert "142" in answer_question("What's his heart rate?", r)
    assert "3 minutes ago" in answer_question("How long ago did it happen?", r)
    assert answer_question("What medication is he on?", r).startswith("Unknown")


def test_heat_trigger_wording():
    r = build_report(event(trigger="heat_red_unacked"), now=T0 + 70)
    assert "heat danger alert" in r.what_happened


def test_clock_format():
    assert clock(None) == "Unknown"
    assert clock(T0).endswith(("am", "pm"))
