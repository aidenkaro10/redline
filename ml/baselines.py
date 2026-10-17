"""
The baselines every model must beat. All three start from the Army's ECTemp Kalman filter.

    kalman_flat:   "your core temp now, assumed to stay flat"
    kalman_trend:  "your core temp now, plus its recent trend"
    kalman_now:    the raw Kalman with no learned uncertainty (estimation only;
                   its forecasts are flat and its range is the Kalman's own variance)

To give the forecast baselines a fair uncertainty range, they learn from the
training people how wrong they usually are at each horizon, and add those
error percentiles to their point forecast. That's the honest version of
"Kalman + error bars".
"""

import numpy as np

from backend.models.kalman import run_kalman
from ml.data import Session
from ml.evaluate import HORIZONS, QUANTILES, Prediction

TREND_WINDOW_MIN = 10   # trend = slope of the Kalman estimate over the last 10 minutes


def kalman_estimate(s: Session) -> tuple[np.ndarray, np.ndarray]:
    """ECTemp run over a session's 1-minute heart rate (NaN = missing minute)."""
    return run_kalman(s.signals["hr"])


def trend_slope(ct: np.ndarray, window: int = TREND_WINDOW_MIN) -> np.ndarray:
    """Least-squares slope (°C per minute) of the last `window` minutes, at every minute."""
    slope = np.zeros_like(ct)
    x = np.arange(window) - (window - 1) / 2
    for t in range(window - 1, len(ct)):
        slope[t] = np.dot(x, ct[t - window + 1:t + 1]) / np.dot(x, x)
    return slope


class _ResidualQuantileBaseline:
    """Point forecast from a rule, plus error percentiles learned on training people."""

    name = "base"

    def point_forecast(self, ct: np.ndarray, h: int) -> np.ndarray:
        raise NotImplementedError

    def fit(self, sessions: list[Session]) -> None:
        # For each horizon, collect (truth at t+h) - (point forecast made at t).
        self.offsets = {}
        for h in HORIZONS:
            res = []
            for s in sessions:
                ct, _ = kalman_estimate(s)
                pf = self.point_forecast(ct, h)
                t = np.arange(0, s.n_minutes - h)
                ok = s.core_temp_measured[t + h]
                res.append(s.signals["core_temp"][t[ok] + h] - pf[t[ok]])
            res = np.concatenate(res)
            self.offsets[h] = {q: float(np.quantile(res, q)) for q in QUANTILES}

    def predict(self, s: Session) -> Prediction:
        ct, v = kalman_estimate(s)
        forecast = {h: {q: self.point_forecast(ct, h) + self.offsets[h][q] for q in QUANTILES} for h in HORIZONS}
        return Prediction(now=ct, forecast=forecast, now_std=np.sqrt(v))


class KalmanFlat(_ResidualQuantileBaseline):
    name = "kalman_flat"

    def point_forecast(self, ct, h):
        return ct


class KalmanTrend(_ResidualQuantileBaseline):
    name = "kalman_trend"

    def point_forecast(self, ct, h):
        return ct + trend_slope(ct) * h


class KalmanRaw:
    """The filter exactly as published: flat forecast, its own (tiny) variance as the range."""

    name = "kalman_raw"

    def fit(self, sessions):
        pass

    def predict(self, s: Session) -> Prediction:
        ct, v = kalman_estimate(s)
        sd = np.sqrt(v)
        z = {0.1: -1.2816, 0.5: 0.0, 0.9: 1.2816}     # normal-distribution percentiles
        forecast = {h: {q: ct + z[q] * sd for q in QUANTILES} for h in HORIZONS}
        return Prediction(now=ct, forecast=forecast, now_std=sd)


BASELINES = {"kalman_raw": KalmanRaw, "kalman_flat": KalmanFlat, "kalman_trend": KalmanTrend}
