import pytest

from backend.app.schemas import ButtonPress, Reading, events_from_line, format_command, parse_line


def test_parses_every_line_in_the_protocol_table():
    assert parse_line("TEMP:34.62") == {"skin_temp": 34.62}
    assert parse_line("TILT:1,ACT:7") == {"tilt": 1, "activity": 7.0}
    assert parse_line("ACC:0.98,0.02,0.11") == {"accel": (0.98, 0.02, 0.11)}
    assert parse_line("BTN:1") == {"button": True}


def test_handles_mixed_lines_and_whitespace():
    assert parse_line("  TEMP:34.6, TILT:0 ,ACT:3\r\n") == {"skin_temp": 34.6, "tilt": 0, "activity": 3.0}
    assert parse_line("HR:82,TEMP:34.6") == {"hr": 82.0, "skin_temp": 34.6}


@pytest.mark.parametrize("junk", ["", "   ", "hello", "TEMP:abc", "ACC:1,2", "\x00\xff", "34.5,TEMP:1"])
def test_garbage_returns_none(junk):
    assert parse_line(junk) is None


def test_unknown_keys_are_ignored():
    assert parse_line("TEMP:34.0,BATT:87") == {"skin_temp": 34.0}


def test_events_from_line_splits_button_from_reading():
    events = events_from_line("BTN:1", "w1", 100.0)
    assert events == [ButtonPress(worker_id="w1", timestamp=100.0)]

    events = events_from_line("TEMP:34.62", "w1", 100.0)
    assert events == [Reading(worker_id="w1", timestamp=100.0, skin_temp=34.62)]


def test_format_command():
    assert format_command("HEAT_RED") == "BUZZ:HEAT_RED"
    with pytest.raises(ValueError):
        format_command("PARTY")


def test_reading_round_trips_through_json():
    r = Reading(worker_id="w1", timestamp=1.5, hr=90, accel=(0.1, 0.2, 0.3))
    assert Reading.model_validate_json(r.model_dump_json()) == r
