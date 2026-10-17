"""
The ONE function that turns raw 1-minute signals into model inputs.

Training (ml/) and the live server (backend/) both call this, so the model
always sees inputs computed exactly the same way. If these two ever drift
apart, the model silently breaks in the demo. That's the most common ML bug.

Rules:
- Causal: row t only uses data from minutes 0..t. Never the future.
- Fixed scaling constants (no statistics fitted on data), so there is no
  separate "scaler file" to keep in sync.
- Every input that can go missing gets a 0/1 "missing" flag, and its value is
  set to 0. The model learns to work without it (e.g. if the diode fails).
"""

import numpy as np

from backend import config
from backend.models.kalman import run_kalman

# (signal, center, scale): value is fed to the model as (x - center) / scale
_SCALES = {
    "hr": (100.0, 30.0),            # bpm
    "skin_temp": (34.0, 1.5),       # °C
    "activity": (5.0, 5.0),         # tilt flickers per second
    "wbgt": (28.0, 5.0),            # °C
    "workload": (250.0, 150.0),     # W/m²
    "kalman_ct": (37.5, 0.5),       # °C
}
TREND_MIN = 5          # heart rate change over the last 5 minutes
KALMAN_SLOPE_MIN = 10  # Kalman trend over the last 10 minutes

# Inputs available at every past minute (encoder).
PAST_FEATURES = (
    "hr", "hr_missing", "hr_delta5",
    "skin_temp", "skin_temp_missing",
    "activity", "activity_missing",
    "wbgt", "wbgt_missing",
    "workload", "workload_missing",
    "kalman_ct", "kalman_sd", "kalman_slope",
    "time_hr",
)
# Inputs known ahead of time for future minutes (decoder): forecast weather, planned work.
FUTURE_FEATURES = ("wbgt", "wbgt_missing", "workload", "workload_missing")


def _scaled(name: str, x: np.ndarray) -> np.ndarray:
    center, scale = _SCALES[name]
    return (x - center) / scale


def _forward_fill(x: np.ndarray) -> np.ndarray:
    """Carry the last seen value forward over gaps (past only, so still causal)."""
    out = x.copy()
    last = np.nan
    for i, v in enumerate(out):
        if np.isnan(v):
            out[i] = last
        else:
            last = v
    return out


def _with_flag(name: str, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Scaled, forward-filled signal plus a missing flag (1 = not measured this minute)."""
    missing = np.isnan(x).astype(np.float32)
    filled = _forward_fill(x)
    value = np.where(np.isnan(filled), 0.0, _scaled(name, np.nan_to_num(filled, nan=_SCALES[name][0])))
    return value, missing


def _causal_slope(x: np.ndarray, window: int) -> np.ndarray:
    """Least-squares slope per minute over the last `window` minutes (shorter at the start)."""
    out = np.zeros_like(x)
    for t in range(1, len(x)):
        seg = x[max(0, t - window + 1):t + 1]
        k = np.arange(len(seg)) - (len(seg) - 1) / 2
        out[t] = np.dot(k, seg) / np.dot(k, k)
    return out


def build_features(signals: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """
    Build model inputs from 1-minute signals.

    Args:
        signals: dict with any of "hr", "skin_temp", "activity", "wbgt", "workload"
                 (arrays of equal length, NaN = not measured). Missing keys = all missing.
    Returns:
        past:   float32 array (n_minutes, len(PAST_FEATURES))
        future: float32 array (n_minutes, len(FUTURE_FEATURES)). Row t describes
                minute t; the model's decoder reads rows t+1..t+30 as "known future".
    """
    n = len(next(iter(signals.values())))
    get = lambda k: np.asarray(signals.get(k, np.full(n, np.nan)), dtype=float)

    hr_raw = get("hr")
    hr, hr_missing = _with_flag("hr", hr_raw)
    hr_filled = _forward_fill(hr_raw)
    hr_delta = np.zeros(n)
    for t in range(n):
        past = hr_filled[max(0, t - TREND_MIN)]
        if not np.isnan(past) and not np.isnan(hr_filled[t]):
            hr_delta[t] = (hr_filled[t] - past) / _SCALES["hr"][1]

    ct, v = run_kalman(hr_raw)
    cols = {
        "hr": hr, "hr_missing": hr_missing, "hr_delta5": hr_delta,
        "kalman_ct": _scaled("kalman_ct", ct),
        "kalman_sd": np.sqrt(v) / 0.1,                    # ~1 once the filter settles
        "kalman_slope": _causal_slope(ct, KALMAN_SLOPE_MIN) * 30,   # °C per 30 min
        "time_hr": np.arange(n) / 60.0,
    }
    for name in ("skin_temp", "activity", "wbgt", "workload"):
        cols[name], cols[f"{name}_missing"] = _with_flag(name, get(name))

    past = np.stack([cols[f] for f in PAST_FEATURES], axis=1).astype(np.float32)
    future = np.stack([cols[f] for f in FUTURE_FEATURES], axis=1).astype(np.float32)
    return past, future


def kalman_baseline(signals: dict[str, np.ndarray]) -> np.ndarray:
    """The Kalman estimate in °C. The neural net predicts the change from this."""
    ct, _ = run_kalman(np.asarray(signals["hr"], dtype=float))
    return ct


HORIZON_MAX = max(config.FORECAST_HORIZONS_MIN)
