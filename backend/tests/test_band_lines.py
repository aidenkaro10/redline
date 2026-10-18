"""The band's new lines (firmware contract 2b): PPG, ACC, AIRT, HUM, BARO. Parse right, never crash."""

import random

import pytest

from backend.app.schemas import BandSample, Reading, events_from_line, parse_line

GOOD_PPG = "PPG:512,515,520,518,511,507,509,514,519,516"


def test_parses_the_new_lines():
    assert parse_line(GOOD_PPG) == {"ppg": (512, 515, 520, 518, 511, 507, 509, 514, 519, 516)}
    assert parse_line("AIRT:31.42") == {"air_temp": 31.42}
    assert parse_line("HUM:58.2") == {"humidity": 58.2}
    assert parse_line("BARO:1012.35") == {"pressure": 1012.35}
    assert parse_line("PPG:0,4095,0,4095,0,4095,0,4095,0,4095")["ppg"][1] == 4095     # range ends are fine


@pytest.mark.parametrize("bad", [
    "PPG:1,2,3,4,5,6,7,8,9",                  # 9 values (a cut-off line)
    "PPG:1,2,3,4,5,6,7,8,9,10,11",            # 11 values (two lines merged)
    "PPG:1,2,3,4,5,6,7,8,9,4096",             # above 12 bits
    "PPG:-1,2,3,4,5,6,7,8,9,10",              # negative
    "PPG:1.5,2,3,4,5,6,7,8,9,10",             # not a whole number
    "PPG:a,2,3,4,5,6,7,8,9,10",
    "PPG:", "PPG:,,,,,,,,,", "PPG:nan,1,2,3,4,5,6,7,8,9",
    "AIRT:nan", "AIRT:inf", "AIRT:99", "AIRT:-60", "AIRT:", "AIRT:hot",
    "HUM:-5", "HUM:101", "HUM:nan",
    "BARO:5", "BARO:2000", "BARO:inf", "BARO:1e9",
])
def test_bad_values_never_become_data(bad):
    assert parse_line(bad) is None, bad
    assert events_from_line(bad, "w", 1.0) == [], bad


def test_fast_lines_become_band_samples_not_readings():
    (ev,) = events_from_line(GOOD_PPG, "w", 5.0)
    assert isinstance(ev, BandSample) and ev.ppg[0] == 512 and ev.timestamp == 5.0
    (ev,) = events_from_line("ACC:0.98,0.02,0.11", "w", 5.0)
    assert isinstance(ev, BandSample) and ev.accel == (0.98, 0.02, 0.11)
    (ev,) = events_from_line("BARO:1012.35", "w", 5.0)
    assert isinstance(ev, BandSample) and ev.pressure == 1012.35
    # A mixed line splits: the slow part stays a Reading.
    evs = events_from_line("TEMP:34.1,AIRT:30.5", "w", 5.0)
    assert {type(e) for e in evs} == {BandSample, Reading}


def test_fuzzed_band_lines_never_crash():
    rng = random.Random(7)
    keys = ["PPG", "ACC", "AIRT", "HUM", "BARO", "TEMP", "TILT", "ppg", "PP G", ""]
    tokens = ["512", "4095", "4096", "-1", "1.5", "nan", "inf", "1e999", "", " ", "x", "0x10", "1_0", "٣", "\x00"]
    for _ in range(20_000):
        n = rng.randrange(0, 14)
        line = rng.choice(keys) + rng.choice([":", "", "::"]) + ",".join(rng.choice(tokens) for _ in range(n))
        for ev in events_from_line(line, "w", 1.0):            # must never raise
            if isinstance(ev, BandSample) and ev.ppg is not None:
                assert len(ev.ppg) == 10 and all(0 <= v <= 4095 for v in ev.ppg)
    for _ in range(5_000):                                      # random bytes, PPG-shaped prefix
        raw = "PPG:" + bytes(rng.randrange(256) for _ in range(rng.randrange(80))).decode("utf-8", errors="replace")
        events_from_line(raw, "w", 1.0)


def test_mutated_good_ppg_lines_are_all_or_nothing():
    """Change one value of a good PPG line: either still 10 good ints, or the whole line is dropped."""
    rng = random.Random(3)
    base = GOOD_PPG.split(":")[1].split(",")
    for _ in range(3_000):
        vals = list(base)
        vals[rng.randrange(10)] = rng.choice(["", "-3", "5000", "1.0", "abc", "7", "4095", "0"])
        out = parse_line("PPG:" + ",".join(vals))
        if out is not None:
            assert len(out["ppg"]) == 10 and all(0 <= v <= 4095 for v in out["ppg"])
