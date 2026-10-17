"""
Weaver-style ensemble weights: trust each of the 5 networks according to how well it
has done for THIS worker so far.

The idea (Weaver, CS329A lecture 3): several imperfect "verifiers" (here, the 5 ensemble
members). Instead of a plain average, give each a weight learned from how well it
agrees with something we can actually observe, and shrink toward equal weights when
there is little evidence.

Which observable signal? Core temp is not observable live, and heart rate is not an
output of ens5. What IS observable every minute is the Kalman estimate. So:

    score of member i at minute t  =  (its median forecast made at minute t-20)
                                      minus (the Kalman estimate at minute t)

    mse_i(t)    = mean of that squared error over all minutes up to t (this worker, this shift)
    raw weight  = 1 / mse_i(t)                       (classic inverse-variance weighting)
    final       = n/(n+K) * raw_normalised + K/(n+K) * equal     (n = minutes of evidence, K = 30)

Why this one and not the NOW-head residual: the forecast-vs-later-Kalman score uses the
same quantity we care about (the 20-minute forecast), and every minute gives a fresh,
fully-observed test. Weak spot (honest): the Kalman is itself wrong by about 0.4 °C, and
a member that has learned to CORRECT the Kalman looks "worse" by this score. So this
verifier can reward the wrong members. We score it and see.

Everything here is causal: the weights at minute t only use data up to minute t.

Run:  python -m ml.explain.weaver        (scores equal vs Weaver weights on the validation people)
"""

import numpy as np
import torch

from backend import config
from backend.models.features import build_features, kalman_baseline
from backend.models.forecaster import ForecasterPredictor, future_windows
from ml.evaluate import Prediction

torch.set_num_threads(2)

HORIZON = 20           # minutes ahead used for scoring members against the Kalman
SHRINK_K = 30.0        # minutes of evidence needed before Weaver counts as much as "equal"
QUANTILES = config.FORECAST_QUANTILES


@torch.no_grad()
def member_outputs(predictor: ForecasterPredictor, signals: dict):
    """Each ensemble member's own outputs. Returns kal (n,), nows (M,n), vars (M,n), fcs (M,n,30,3)."""
    past, future = build_features(signals)
    kal = kalman_baseline(signals)
    n = len(kal)
    p, f = torch.from_numpy(past).unsqueeze(0), torch.from_numpy(future).unsqueeze(0)
    t = torch.arange(n).unsqueeze(0)
    nows, vars_, fcs = [], [], []
    for m in predictor.models:
        h = m.encode(p)
        d, logv = m.now(h)
        fcs.append(m.forecast(h[0], future_windows(f, t)[0]).numpy())
        nows.append(d[0].numpy())
        vars_.append(np.exp(logv[0].numpy()))
    return kal, np.array(nows), np.array(vars_), np.array(fcs)


def weaver_weights(kal: np.ndarray, fcs: np.ndarray, k: float = SHRINK_K, horizon: int = HORIZON) -> np.ndarray:
    """(n, M) weights; row t uses only minutes <= t. Rows sum to 1."""
    M, n = fcs.shape[0], fcs.shape[1]
    # member i's median forecast for minute s+h, made at minute s, versus Kalman at s+h
    err2 = np.full((M, n), np.nan)
    pred = kal[None, :n - horizon] + fcs[:, :n - horizon, horizon - 1, 1]       # (M, n-h): made at s
    err2[:, horizon:] = (pred - kal[None, horizon:]) ** 2                       # known at minute s+h
    return _weights_from_err2(err2, k)


def _weights_from_err2(err2: np.ndarray, k: float) -> np.ndarray:
    M, n = err2.shape
    w = np.full((n, M), 1.0 / M)
    seen = np.cumsum(~np.isnan(err2[0]))
    cum = np.cumsum(np.nan_to_num(err2), axis=1)
    for t in range(n):
        if seen[t] == 0:
            continue
        mse = cum[:, t] / seen[t] + 1e-6
        raw = (1 / mse) / np.sum(1 / mse)
        lam = seen[t] / (seen[t] + k)
        w[t] = lam * raw + (1 - lam) / M
    return w


def combine(kal, nows, vars_, fcs, weights) -> tuple:
    """
    The ensemble recipe from ForecasterPredictor.predict_signals, with per-minute weights.
    weights (n, M) of all 1/M gives exactly the plain average.
    """
    w = weights.T                                                        # (M, n)
    now_delta = (w * nows).sum(0)
    now_var = (w * vars_).sum(0) + (w * (nows - now_delta) ** 2).sum(0)
    wf = w[:, :, None]
    median = (wf * fcs[..., 1]).sum(0)
    spread = np.sqrt((wf * (fcs[..., 1] - median) ** 2).sum(0))
    low = (wf * fcs[..., 0]).sum(0) - spread
    high = (wf * fcs[..., 2]).sum(0) + spread
    return kal, now_delta, now_var, np.stack([low, median, high], axis=-1)


class WeightedPredictor:
    """Same interface as ForecasterPredictor (predict(session)), with a choice of weights."""

    def __init__(self, base: ForecasterPredictor, mode: str = "weaver", name: str | None = None, k: float = SHRINK_K):
        assert mode in ("equal", "weaver", "oracle")
        self.base, self.mode, self.k = base, mode, k
        self.name = name or f"{base.name}+{mode}"
        self.trained_on = base.trained_on
        self.last_weights = {}          # session_id -> (n, M), kept for the report

    def fit(self, sessions):
        pass

    def weights_for(self, kal, fcs, truth=None, measured=None) -> np.ndarray:
        M, n = fcs.shape[0], fcs.shape[1]
        if self.mode == "equal":
            return np.full((n, M), 1.0 / M)
        if self.mode == "weaver":
            return weaver_weights(kal, fcs, self.k)
        # oracle: same recipe, but scored against the TRUE core temp. Not available live;
        # it only shows how much a perfect verifier could gain.
        err2 = np.full((M, n), np.nan)
        pred = kal[None, :n - HORIZON] + fcs[:, :n - HORIZON, HORIZON - 1, 1]
        e2 = (pred - truth[None, HORIZON:]) ** 2
        e2[:, ~measured[HORIZON:]] = np.nan
        err2[:, HORIZON:] = e2
        return _weights_from_err2(err2, self.k)

    def predict(self, session):
        kal, nows, vars_, fcs = member_outputs(self.base, session.signals)
        w = self.weights_for(kal, fcs, session.signals["core_temp"], session.core_temp_measured)
        self.last_weights[session.session_id] = w
        kal, now_delta, now_var, fc = combine(kal, nows, vars_, fcs, w)
        forecast = {h: {q: kal + fc[:, h - 1, i] for i, q in enumerate(QUANTILES)} for h in config.FORECAST_HORIZONS_MIN}
        return Prediction(now=kal + now_delta, forecast=forecast, now_std=np.sqrt(now_var))


# ---------------------------------------------------------------------------
# Scoring: equal weights vs Weaver weights, on validation people
# ---------------------------------------------------------------------------
MAIN = "/Users/Annabelle/Documents/projects/planit-control"


def per_person_rmse_20(sessions, preds) -> dict[str, float]:
    """RMSE of the +20 min median forecast, per person (measured minutes only)."""
    from ml.evaluate import EVAL_START_MIN
    errs = {}
    for s, p in zip(sessions, preds):
        t = np.arange(EVAL_START_MIN, s.n_minutes - HORIZON)
        t = t[s.core_temp_measured[t + HORIZON]]
        e = p.forecast[HORIZON][0.5][t] - s.signals["core_temp"][t + HORIZON]
        errs.setdefault(s.person_id, []).append(e)
    return {k: float(np.sqrt(np.mean(np.concatenate(v) ** 2))) for k, v in errs.items()}


def main():
    from pathlib import Path

    from ml.data import load_mendeley, load_split, sessions_in
    from ml.evaluate import bootstrap_ci, evaluate, print_reports

    base = ForecasterPredictor.load(f"{MAIN}/ml/checkpoints/ens5.pt", calibrated=False)
    split = load_split("mendeley")
    sessions = sessions_in(load_mendeley(Path(MAIN) / "data/raw/mendeley_buono/data.csv"), split["val"])
    print(f"Scoring on VAL people: {len(split['val'])}, sessions: {len(sessions)}")

    models = {m: WeightedPredictor(base, m, name={"equal": "ens5 equal", "weaver": "ens5 Weaver",
                                                  "oracle": "ens5 oracle*"}[m]) for m in ("equal", "weaver", "oracle")}
    reports, preds = [], {}
    for mode, wp in models.items():
        reports.append(evaluate(wp.name, wp, sessions, trained_on=wp.trained_on))
        preds[mode] = [wp.predict(s) for s in sessions]
    print_reports(reports)
    print("* oracle = weights scored against the TRUE core temp; not available live, shows the ceiling.")

    # paired by person: does Weaver beat equal for each person?
    eq, wv = per_person_rmse_20(sessions, preds["equal"]), per_person_rmse_20(sessions, preds["weaver"])
    diff = [wv[p] - eq[p] for p in eq]
    print(f"\n+20 min RMSE per person, Weaver minus equal (negative = Weaver better): mean {np.mean(diff):+.4f} °C, "
          f"95% CI {bootstrap_ci(diff)[0]:+.4f} to {bootstrap_ci(diff)[1]:+.4f}, better for {sum(d < 0 for d in diff)} of {len(diff)} people")

    # how far do the Weaver weights move away from equal?
    w_all = np.concatenate([w[40:] for w in models["weaver"].last_weights.values()])
    print(f"Weaver weights after minute 40: min {w_all.min():.3f}, max {w_all.max():.3f} (equal = {1 / w_all.shape[1]:.3f})")


if __name__ == "__main__":
    main()
