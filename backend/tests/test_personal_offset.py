import numpy as np
import pytest

from backend.models.personal_offset import MAX_OFFSET_C, PersonalOffset


def test_new_worker_is_the_average_person():
    p = PersonalOffset("w1")
    assert p.offset_c == 0.0 and p.confidence == 0.0
    assert np.allclose(p.correct(np.array([38.0])), [38.0])


def test_offset_grows_more_confident_with_every_check():
    p = PersonalOffset("w1")
    offsets = []
    for _ in range(6):
        p.add_check(model_estimate_c=37.8, thermometer_c=37.5)   # model reads this worker 0.3 high
        offsets.append(p.offset_c)
    assert all(a < b for a, b in zip(offsets, offsets[1:]))       # steadily trusts their history more
    assert offsets[-1] == pytest.approx(0.3 * 6 / 8)
    assert p.correct(np.array([38.0]))[0] == pytest.approx(38.0 - offsets[-1])


def test_bad_readings_are_ignored_and_offset_is_capped():
    p = PersonalOffset("w1")
    assert not p.add_check(38.0, 35.0)                              # 3 °C off: a bad reading
    assert p.misses == []
    for _ in range(100):
        p.add_check(38.5, 37.1)                                     # consistently 1.4 high
    assert p.offset_c == MAX_OFFSET_C


def test_round_trip():
    p = PersonalOffset("w1")
    p.add_check(37.6, 37.4)
    q = PersonalOffset.from_dict(p.to_dict())
    assert q.offset_c == p.offset_c
