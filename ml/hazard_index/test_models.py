import datetime as dt

import numpy as np
import pandas as pd

from ml.hazard_index import models as M


def toy(n=4000, seed=0):
    """Rare events driven by feature 0, an interaction of 1 and 2, and extremes of 3."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 5))
    X[rng.random((n, 5)) < 0.05] = np.nan                 # some missing values
    x = np.nan_to_num(X)
    z = -4.0 + 1.5 * x[:, 0] + 2.5 * (x[:, 1] > 0) * (x[:, 2] > 0) + 2.0 * (np.abs(x[:, 3]) > 1.5)
    y = (rng.random(n) < M.sigmoid(z)).astype(float)
    days = [dt.date(2020, 1, 1) + dt.timedelta(days=i % 365) for i in range(n)]
    meta = pd.DataFrame(dict(site_id=["a", "b"] * (n // 2), date=days, lat=29.0, lon=-82.0))
    return X, y, meta


def test_pr_auc_perfect_and_random():
    y = np.array([0, 0, 1, 0, 1])
    assert M.pr_auc(y, np.array([0.1, 0.2, 0.9, 0.3, 0.8])) == 1.0
    rng = np.random.default_rng(0)
    yr = (rng.random(20000) < 0.1).astype(float)
    assert abs(M.pr_auc(yr, rng.random(20000)) - 0.1) < 0.02     # random ~= base rate


def test_models_beat_climatology_and_stay_calibrated():
    X, y, meta = toy()
    Xt, yt, mt = toy(seed=1)
    clim = M.Climatology().fit(X, y, meta)
    lr = M.Logistic().fit(X, y)
    gb = M.BoostedTrees(n_trees=150).fit(X, y)
    ap = {m.name: M.pr_auc(yt, m.predict(Xt, mt)) for m in (clim, lr, gb)}
    assert ap["logistic"] > ap["climatology"] + 0.1
    assert ap["boosted_trees"] > ap["logistic"]            # it finds the interaction and extremes
    # class weights were undone: average prediction close to the true rate
    assert abs(gb.predict(Xt).mean() - yt.mean()) < 0.03
    assert abs(lr.predict(Xt).mean() - yt.mean()) < 0.03


def test_boosted_trees_save_load_and_contributions():
    X, y, meta = toy(n=2000)
    gb = M.BoostedTrees(n_trees=30).fit(X, y)
    again = M.BoostedTrees.from_dict(gb.to_dict())
    assert np.allclose(gb.predict(X[:50]), again.predict(X[:50]))
    row = np.array([3.0, 0.0, 0.0, 0.0, 0.0])
    c, bias = again.contributions(row)
    assert np.isclose(again.base + bias + c.sum(), again.raw_score(row[None, :])[0])
    assert np.argmax(c) == 0                                 # the big feature 0 explains the high risk
