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
    a = make_person_split(people, n_val=10, n_calib=10, n_test=10, seed=0)
    b = make_person_split(people, n_val=10, n_calib=10, n_test=10, seed=0)
    assert a == b
    assert [len(a[k]) for k in ("train", "val", "calib", "test")] == [35, 10, 10, 10]
    groups = [set(v) for v in a.values()]
    assert set().union(*groups) == set(people)
    assert sum(len(g) for g in groups) == len(people)   # no one in two groups


@needs_mendeley
def test_mendeley_loads_as_expected():
    data = load_mendeley()
    assert len(data) == 129                      # 130 sessions minus the unusable one
    assert len({s.person_id for s in data}) == 65
    assert "mendeley-23-heat" not in {s.session_id for s in data}
    s = next(s for s in data if s.session_id == "mendeley-14-heat")
    assert not s.core_temp_measured.all()        # this session has filled-in dropouts
    assert sum(s.meta["stopped_early"] for s in data) == 36   # 31 heat + 5 neutral
    hot = next(s for s in data if s.condition == "heat")
    assert hot.signals["wbgt"][0] == 32.0 and not np.isnan(hot.signals["workload"]).any()


@needs_mendeley
def test_saved_split_covers_every_mendeley_person():
    split = load_split("mendeley")
    people = {s.person_id for s in load_mendeley()}
    assert set().union(*map(set, split.values())) == people
