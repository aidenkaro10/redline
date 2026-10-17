import numpy as np

from backend.models.features import FUTURE_FEATURES, PAST_FEATURES, build_features


def _signals(n=60, seed=0):
    rng = np.random.default_rng(seed)
    return {
        "hr": 90 + np.arange(n) + rng.normal(0, 3, n),
        "skin_temp": np.full(n, 34.5),
        "wbgt": np.full(n, 30.0),
    }


def test_shapes_and_names():
    past, future = build_features(_signals())
    assert past.shape == (60, len(PAST_FEATURES))
    assert future.shape == (60, len(FUTURE_FEATURES))
    assert past.dtype == np.float32
    assert not np.isnan(past).any() and not np.isnan(future).any()


def test_features_are_causal():
    # Changing the future must not change any earlier row.
    a = _signals()
    b = {k: v.copy() for k, v in a.items()}
    b["hr"][40:] = 180
    b["skin_temp"][40:] = 37
    pa, _ = build_features(a)
    pb, _ = build_features(b)
    assert np.array_equal(pa[:40], pb[:40])
    assert not np.array_equal(pa[40:], pb[40:])


def test_missing_signals_get_flags_and_zero_values():
    past, _ = build_features({"hr": np.full(30, 100.0)})
    i = PAST_FEATURES.index
    assert (past[:, i("skin_temp_missing")] == 1).all()
    assert (past[:, i("skin_temp")] == 0).all()
    assert (past[:, i("hr_missing")] == 0).all()


def test_heart_rate_gap_is_forward_filled_and_flagged():
    hr = np.full(20, 120.0)
    hr[5:8] = np.nan
    past, _ = build_features({"hr": hr})
    i = PAST_FEATURES.index
    assert (past[5:8, i("hr_missing")] == 1).all()
    assert np.allclose(past[5:8, i("hr")], past[4, i("hr")])
