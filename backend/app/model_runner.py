"""
Running the forecaster live, fast.

ForecasterPredictor.predict_signals() (backend/models, ML session) forecasts EVERY
minute of the shift each time it's called, which is right for scoring but wasteful
live: we only need the newest minute. With the 5-model ensemble that's ~0.4 s per
call late in a shift, per worker, per minute. predict_at() does the same maths for
one minute only (the encoder still reads the whole history, it's cheap).

It mirrors predict_signals line for line. backend/tests/test_model_runner.py checks
that both give the same numbers, so if the ML session changes predict_signals, that
test fails instead of the live system silently drifting.
"""

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Best model first. A final, calibrated ensemble will replace ens5.pt with the same interface.
CHECKPOINTS = (Path("ml/checkpoints/ens5.pt"), Path("ml/checkpoints/v1.pt"))


def load_best_predictor(paths=CHECKPOINTS, explicit: str | None = None):
    """The first checkpoint that loads, or None (the pipeline then runs Kalman-only)."""
    candidates = [Path(explicit)] if explicit else list(paths)
    for path in candidates:
        if not path.exists():
            continue
        try:
            from backend.models.forecaster import ForecasterPredictor
            predictor = ForecasterPredictor.load(path)
            log.info("Loaded forecaster %s (%d model%s) from %s", predictor.name, len(predictor.models),
                     "s" if len(predictor.models) > 1 else "", path)
            return predictor
        except Exception as e:
            log.warning("Could not load %s (%s)", path, e)
    log.warning("No forecaster checkpoint found (%s): running Kalman-only alerts",
                ", ".join(str(p) for p in candidates))
    return None


def predict_at(predictor, signals: dict[str, np.ndarray], i: int):
    """
    Same as predictor.predict_signals(signals) but only for minute i.
    Returns (kalman_i, now_delta_i, now_var_i, forecast_i) with forecast_i shaped (30, 3)
    as a change from the Kalman estimate at the 10th / 50th / 90th percentile.
    """
    import torch

    from backend.models.features import build_features, kalman_baseline
    from backend.models.forecaster import future_windows

    with torch.no_grad():
        past, future = build_features(signals)
        kal = kalman_baseline(signals)
        p = torch.from_numpy(past).unsqueeze(0)
        f = torch.from_numpy(future).unsqueeze(0)
        t = torch.tensor([[i]])
        window = future_windows(f, t)[0]                         # (1, 30, n_future)
        nows, vars_, fcs = [], [], []
        for m in predictor.models:
            h = m.encode(p[:, :i + 1])                           # causal: rows after i can't matter
            d, logv = m.now(h)
            fc = m.forecast(h[0, -1:], window)                   # (1, 30, 3)
            nows.append(float(d[0, -1]))
            vars_.append(float(np.exp(float(logv[0, -1]))))
            fcs.append(fc[0].numpy())
    now_delta = float(np.mean(nows))
    now_var = float(np.mean(vars_) + np.var(nows))
    fc = np.stack(fcs)                                           # (models, 30, 3)
    median = fc[..., 1].mean(axis=0)
    spread = fc[..., 1].std(axis=0)
    low = fc[..., 0].mean(axis=0) - spread
    high = fc[..., 2].mean(axis=0) + spread
    if predictor.calibration is not None:
        off = np.asarray(predictor.calibration["offsets"])
        low, median, high = low + off[:, 0], median + off[:, 1], high + off[:, 2]
        low, high = np.minimum(low, median), np.maximum(high, median)
        now_var = now_var * predictor.calibration["now_scale"] ** 2
    return float(kal[i]), now_delta, now_var, np.stack([low, median, high], axis=-1)
