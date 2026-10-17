import numpy as np
import pytest

from ml.data import MENDELEY_PATH, Session, load_mendeley, load_split, make_person_split

needs_mendeley = pytest.mark.skipif(not MENDELEY_PATH.exists(), reason="Mendeley dataset not downloaded")


def test_session_fills_missing_signals_with_nan():
    s = Session("p1", "s1", "sim", "x", {"hr": np.array([80.0, 90.0])})
    assert s.n_minutes == 2
    assert np.isnan(s.signals["skin_temp"]).all()
    assert not s.core_temp_measured.any()


def test_split_is_disjoint_complete_and_deterministic():
    people = [f"p{i}" for i in range(65)]
    a = make_person_split(people, n_calib=10, n_test=10, seed=0)
    b = make_person_split(people, n_calib=10, n_test=10, seed=0)
    assert a == b
    assert (len(a["train"]), len(a["calib"]), len(a["test"])) == (45, 10, 10)
    assert set(a["train"]) | set(a["calib"]) | set(a["test"]) == set(people)
    assert not (set(a["train"]) & set(a["test"]))
    assert not (set(a["calib"]) & set(a["test"]))


@needs_mendeley
def test_mendeley_loads_as_expected():
    data = load_mendeley()
    assert len(data) == 129                      # 130 sessions minus the unusable one
    assert len({s.person_id for s in data}) == 65
    assert "mendeley-23-heat" not in {s.session_id for s in data}
    s = next(s for s in data if s.session_id == "mendeley-14-heat")
    assert not s.core_temp_measured.all()        # this session has filled-in dropouts
    assert sum(s.meta["stopped_early"] for s in data) == 36   # 31 heat + 5 neutral


@needs_mendeley
def test_saved_split_covers_every_mendeley_person():
    split = load_split("mendeley")
    people = {s.person_id for s in load_mendeley()}
    assert set(split["train"]) | set(split["calib"]) | set(split["test"]) == people
