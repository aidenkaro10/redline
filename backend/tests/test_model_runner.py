"""predict_at must give exactly what predict_signals gives for that minute."""

from pathlib import Path

import numpy as np
import pytest

from backend.app.model_runner import load_best_predictor, predict_at

ENS5, V1 = Path("ml/checkpoints/ens5.pt"), Path("ml/checkpoints/v1.pt")


def signals(n=90):
    rng = np.random.default_rng(0)
    hr = 100 + 50 * np.clip(np.arange(n) / 60, 0, 1) + rng.normal(0, 3, n)
    hr[10:13] = np.nan
    sig = {"hr": hr, "skin_temp": 33.5 + np.arange(n) / 100, "activity": np.full(n, 5.0),
           "wbgt": np.full(n, 29.0), "workload": np.full(n, 220.0)}
    return sig


@pytest.mark.parametrize("path", [ENS5, V1])
def test_predict_at_matches_predict_signals(path):
    if not path.exists():
        pytest.skip(f"{path} not in this checkout")
    p = load_best_predictor(explicit=str(path))
    s = signals()
    kal, nd, nv, fc = p.predict_signals(s)
    for i in (0, 37, 59):                       # also with "known future" rows after i
        k_i, nd_i, nv_i, fc_i = predict_at(p, s, i)
        assert k_i == pytest.approx(kal[i], abs=1e-9)
        assert nd_i == pytest.approx(nd[i], abs=1e-5)
        assert nv_i == pytest.approx(nv[i], rel=1e-4)
        assert np.allclose(fc_i, fc[i], atol=1e-5)


def test_missing_checkpoint_means_kalman_only(tmp_path):
    assert load_best_predictor(paths=(tmp_path / "nope.pt",)) is None
