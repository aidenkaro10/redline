"""Small tests for ml/figures. Run:  pytest ml/figures"""

from pathlib import Path

import numpy as np

from ml.figures.make_figures import Run, pairs_at_horizon
from ml.data import Session


def _run(n=60):
    truth = np.linspace(37, 38, n)
    s = Session("p", "p-1", "test", "heat", {"hr": np.full(n, 100.0), "core_temp": truth})
    fc = np.zeros((n, 30, 3))                       # forecast = Kalman (flat 37.0) for every quantile
    return Run(s, np.full(n, 37.0), np.zeros(n), fc)


def test_pairs_line_up_forecast_with_the_later_truth():
    r = _run()
    y, f = pairs_at_horizon([r], 20)
    t = np.arange(10, 40)
    assert np.allclose(y, r.truth[t + 20])
    assert f.shape == (len(t), 3) and np.allclose(f, 37.0)


def test_committed_figures_are_small():
    figs = list(Path("docs/figures").glob("*.png"))
    assert figs, "run: python -m ml.figures.make_figures"
    assert all(f.stat().st_size < 300 * 1024 for f in figs)
