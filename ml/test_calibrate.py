import numpy as np
import pytest

from ml.calibrate import conformal_shift


def test_shift_hits_the_target_coverage():
    rng = np.random.default_rng(0)
    misses = rng.normal(0.3, 0.2, 5000)          # model's 90th pct is too low: truth sits above it a lot
    shift = conformal_shift(misses, 0.9)
    fresh = rng.normal(0.3, 0.2, 5000)           # new people, same kind of misses
    assert np.mean(fresh <= shift) == pytest.approx(0.9, abs=0.02)


def test_lower_edge_shift_is_conservative_downward():
    rng = np.random.default_rng(1)
    misses = rng.normal(0, 1, 1000)
    assert conformal_shift(misses, 0.1) <= np.quantile(misses, 0.1) + 1e-9


def test_upper_edge_shift_is_conservative_upward():
    rng = np.random.default_rng(2)
    misses = rng.normal(0, 1, 1000)
    assert conformal_shift(misses, 0.9) >= np.quantile(misses, 0.9) - 1e-9
