import numpy as np

from ml.fitness_check import FitnessCheckModel, check_features, protocol_met, simulate_check


def _person(rest=70, per_met=12, offset=0.0):
    return {"person_id": "p", "rest_hr": rest, "hr_per_met": per_met, "hr_offset": offset, "acclim_start": 0.0}


def test_protocol_is_four_minutes_at_15_second_samples():
    assert len(protocol_met()) == 16


def test_fitter_person_rises_less():
    rng = np.random.default_rng(0)
    fit = check_features(simulate_check(_person(rest=60, per_met=8), rng))
    unfit = check_features(simulate_check(_person(rest=80, per_met=18), rng))
    assert fit[0] < unfit[0]          # lower resting HR
    assert fit[2] < unfit[2]          # smaller rise


def test_features_tolerate_missing_readings():
    hr = simulate_check(_person(), np.random.default_rng(1))
    hr[3] = hr[9] = np.nan
    assert np.isfinite(check_features(hr)).all()


def test_ridge_recovers_a_linear_relationship():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(500, 5))
    W = rng.normal(size=(5, 16))
    Y = X @ W + 0.01 * rng.normal(size=(500, 16))
    m = FitnessCheckModel.fit(X, Y, lam=1e-3)
    assert np.abs(m.predict(X[:10]) - Y[:10]).max() < 0.1
