import numpy as np
import pytest

from ml.baselines import KalmanFlat, trend_slope
from ml.data import Session
from ml.evaluate import HORIZONS, QUANTILES, Prediction, assert_no_overlap, evaluate, pinball, score_alerts


def _session(ct, hr=None, pid="p1"):
    ct = np.asarray(ct, dtype=float)
    hr = np.full(len(ct), 120.0) if hr is None else np.asarray(hr, dtype=float)
    return Session(pid, f"{pid}-s", "sim", "x", {"hr": hr, "core_temp": ct})


class Oracle:
    """Knows the truth. Used to check the harness scores a perfect model as perfect."""

    def __init__(self, s, spread=0.0):
        self.s, self.spread = s, spread

    def fit(self, sessions):
        pass

    def predict(self, s):
        ct = s.signals["core_temp"]
        n = len(ct)
        fc = {}
        for h in HORIZONS:
            future = np.full(n, np.nan)
            future[: n - h] = ct[h:]
            fc[h] = {0.1: future - self.spread, 0.5: future, 0.9: future + self.spread}
        return Prediction(now=ct.copy(), forecast=fc)


def test_pinball_matches_the_formula():
    y, yhat = np.array([1.0]), np.array([0.0])
    assert pinball(y, yhat, 0.9) == pytest.approx(0.9)    # under-predicting at q=0.9 costs 0.9 per unit
    assert pinball(yhat, y, 0.9) == pytest.approx(0.1)    # over-predicting costs 0.1 per unit


def test_leakage_guard_refuses_overlap():
    with pytest.raises(ValueError):
        assert_no_overlap({"a", "b"}, {"b", "c"})
    assert_no_overlap({"a"}, {"c"})


def test_a_perfect_model_scores_perfectly():
    s = _session(np.linspace(37.0, 39.0, 120))
    r = evaluate("oracle", Oracle(s, spread=0.05), [s])
    assert r.estimation["rmse"] == pytest.approx(0.0)
    for h in HORIZONS:
        assert r.forecast[h]["rmse_median"] == pytest.approx(0.0)
        assert r.forecast[h]["coverage_10_90"] == 1.0
    assert r.alerts["red"]["caught_pct"] == 100
    assert any("better than published" in w for w in r.warnings)   # too good = suspicious


def test_alert_lead_time_and_false_alarms():
    n = 120
    ct = np.full(n, 37.5)
    ct[60:] = 38.6                         # truth crosses 38.5 at minute 60
    s = _session(ct)
    now = np.full(n, 37.5)
    fc = {h: {q: np.full(n, 37.5) for q in QUANTILES} for h in HORIZONS}
    fc[10][0.9][45:] = 38.6                # 10-min 90th percentile warns from minute 45
    p = Prediction(now=now, forecast=fc)
    red = score_alerts([s], [p], "red")
    assert red["episodes"] == 1 and red["caught_pct"] == 100
    assert red["mean_lead_min"] == 15
    assert red["false_alarms_per_hr"] == 0

    fc[10][0.9][15:20] = 38.6              # a blip at minute 15, 45 min before the crossing
    red = score_alerts([s], [p], "red")
    assert red["false_alarms_per_hr"] > 0


def test_trend_slope_of_a_straight_line():
    ct = 37.0 + 0.02 * np.arange(30)
    assert trend_slope(ct)[20] == pytest.approx(0.02)


def test_learned_baseline_ranges_are_ordered():
    rng = np.random.default_rng(0)
    sessions = [_session(37 + 0.01 * np.arange(120) + rng.normal(0, 0.1, 120), pid=f"p{i}") for i in range(5)]
    m = KalmanFlat()
    m.fit(sessions)
    for h in HORIZONS:
        assert m.offsets[h][0.1] < m.offsets[h][0.5] < m.offsets[h][0.9]
