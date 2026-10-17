import numpy as np
import pytest

from ml.simulate import (SHIFT_MIN, add_sensor_realism, load_sim, save_sim, schedule_day,
                         sample_person, simulate, stull_wet_bulb)


@pytest.fixture(scope="module")
def sim():
    return add_sensor_realism(simulate(n_people=6, n_days=8, seed=3, verbose=False))


def test_shapes_and_weekends_off(sim):
    n = len(sim["person_index"])
    assert n == 6 * 6                       # 8 days minus 2 weekend days, per person
    for k in ("hr", "core_temp", "skin_temp", "activity", "wbgt", "workload"):
        assert sim[k].shape == (n, SHIFT_MIN)
    assert not np.isin(sim["day"], [5, 6]).any()


def test_physiology_stays_in_human_ranges(sim):
    ct = sim["core_temp"]
    assert 36.0 < ct.min() and ct.max() < 41.0
    hr = sim["hr"][~np.isnan(sim["hr"])]
    assert 40 <= hr.min() and hr.max() <= 200
    assert (sim["core_temp"][:, -1] > sim["core_temp"][:, 0]).mean() > 0.8   # people warm up over a shift


def test_same_seed_same_world():
    a = simulate(2, 2, seed=7, verbose=False)
    b = simulate(2, 2, seed=7, verbose=False)
    assert np.array_equal(a["core_temp"], b["core_temp"])
    assert np.array_equal(a["hr"], b["hr"])


def test_forced_rest_kicks_in_when_too_hot():
    s = simulate(30, 5, seed=11, verbose=False)
    rest_minutes = s["forced_rest"].astype(bool)
    assert rest_minutes.any()
    assert (s["met_actual"][rest_minutes] == 1.2).all()


def test_acclimatization_rises_over_working_days(sim):
    for p in range(6):
        a = sim["acclim"][sim["person_index"] == p]
        assert np.all(np.diff(a) >= -0.081)         # fades 0.04 per day off, so a weekend drops 0.08 at most


def test_sensor_realism_adds_gaps_and_missing_diodes(sim):
    assert np.isnan(sim["hr"]).any()
    assert np.isnan(sim["skin_temp"]).all(axis=1).any() or len(sim["hr"]) < 20


def test_schedule_covers_the_shift_with_a_lunch_break():
    rng = np.random.default_rng(0)
    met, shade = schedule_day(sample_person(0, rng), rng)
    assert met.shape == (SHIFT_MIN,) and not np.isnan(met).any()
    assert (met[255:330] == 1.2).sum() >= 30       # 30-min lunch somewhere in 11:15-12:30


def test_wet_bulb_sanity():
    assert stull_wet_bulb(np.array([20.0]), np.array([50.0]))[0] == pytest.approx(13.7, abs=0.2)


def test_save_and_load_round_trip(tmp_path, sim):
    path = tmp_path / "s.npz"
    save_sim(sim, path)
    sessions = load_sim(path)
    assert len(sessions) == len(sim["person_index"])
    assert np.allclose(sessions[0].signals["core_temp"], sim["core_temp"][0])
    assert sessions[0].person_id.startswith("sim-")


def test_pulse_sensor_artifacts_only_hit_band_shifts_and_get_worse_with_effort():
    from ml.simulate import add_ppg_motion_artifacts
    rng = np.random.default_rng(0)
    true_hr = np.full(400, 120.0)
    rest, work = np.full(400, 1.2), np.full(400, 4.5)
    err_rest = np.nanstd(add_ppg_motion_artifacts(true_hr, rest, rng) - true_hr)
    err_work = np.nanstd(add_ppg_motion_artifacts(true_hr, work, rng) - true_hr)
    assert err_work > 2 * err_rest
    s = add_sensor_realism(simulate(4, 2, seed=5, verbose=False), seed=2, ppg_share=0.0)
    assert not s["hr_source"].any()
