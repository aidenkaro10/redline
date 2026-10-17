"""Small tests for ml/explain/weaver.py. Run:  pytest ml/explain"""

import numpy as np
import pytest
import torch

from backend.models.forecaster import ForecasterPredictor, HeatForecaster
from ml.explain import weaver as W


@pytest.fixture(scope="module")
def tiny():
    torch.manual_seed(1)
    return ForecasterPredictor([HeatForecaster(hidden=16, layers=2) for _ in range(3)])


def _signals(n=90):
    return {"hr": np.linspace(70, 150, n) + np.random.default_rng(0).normal(0, 3, n)}


def test_equal_weights_reproduce_the_plain_ensemble(tiny):
    sig = _signals()
    kal, nows, vars_, fcs = W.member_outputs(tiny, sig)
    w = np.full((len(kal), 3), 1 / 3)
    _, now_delta, now_var, fc = W.combine(kal, nows, vars_, fcs, w)
    kal2, now_delta2, now_var2, fc2 = tiny.predict_signals(sig)
    assert np.allclose(now_delta, now_delta2, atol=1e-5)
    assert np.allclose(now_var, now_var2, atol=1e-5)
    assert np.allclose(fc, fc2, atol=1e-5)


def test_weights_sum_to_one_and_start_equal(tiny):
    kal, _, _, fcs = W.member_outputs(tiny, _signals())
    w = W.weaver_weights(kal, fcs)
    assert np.allclose(w.sum(axis=1), 1.0)
    assert np.allclose(w[:W.HORIZON], 1 / 3)            # no evidence yet: equal


def test_only_the_past_moves_the_weights(tiny):
    sig = _signals(100)
    kal, _, _, fcs = W.member_outputs(tiny, sig)
    w_full = W.weaver_weights(kal, fcs)
    sig2 = {"hr": sig["hr"][:70]}
    kal2, _, _, fcs2 = W.member_outputs(tiny, sig2)
    assert np.allclose(w_full[:70], W.weaver_weights(kal2, fcs2), atol=1e-6)


def test_better_member_gets_more_weight():
    n, M = 120, 3
    kal = np.full(n, 37.5)
    fcs = np.zeros((M, n, 30, 3))
    fcs[0, :, :, 1] = 0.0      # member 0 matches the Kalman perfectly
    fcs[1, :, :, 1] = 0.3
    fcs[2, :, :, 1] = -0.6
    w = W.weaver_weights(kal, fcs)
    assert w[-1, 0] > w[-1, 1] > w[-1, 2]
    assert w[25, 0] < w[-1, 0]                           # little data: closer to equal than later
