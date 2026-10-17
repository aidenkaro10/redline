"""
The scoring harness. Every model goes through this, always next to the baselines.

A model ("predictor") has two methods:
    fit(train_sessions)        learn whatever it needs (baselines learn their error spread)
    predict(session) -> Prediction

A Prediction has, for every minute t of the session:
    now[t]                    estimated core temp at minute t
    forecast[h][q][t]         the q-th percentile of core temp at minute t + h,
                              predicted using only data up to minute t

What we score (only on minutes where core temp was really measured):
    Estimation:  RMSE, bias, Bland-Altman 95% limits of agreement
    Forecasts:   RMSE of the median, quantile (pinball) loss, coverage of the
                 90th percentile and of the 10-90 range
    Alerts:      share of danger episodes caught, warning lead time, false
                 alarms per hour (alert rules from backend/config.py)
    Per person:  RMSE for each person, plus a 95% confidence interval from
                 resampling people (so one weird person can't fool us)

Run:  python -m ml.evaluate            (baselines on the validation people)
"""

from dataclasses import dataclass, field

import numpy as np

from backend import config
from ml.data import Session, load_mendeley, load_split, sessions_in

HORIZONS = config.FORECAST_HORIZONS_MIN          # (10, 20, 30)
QUANTILES = config.FORECAST_QUANTILES            # (0.1, 0.5, 0.9)
EVAL_START_MIN = 10        # score from minute 10 on: every model gets 10 min of warm-up
LEAD_WINDOW_MIN = 30       # an alert counts as "true" if the threshold is crossed within 30 min
SUSPICIOUSLY_GOOD_RMSE = 0.20   # published HR-based estimates are ~0.3 °C or worse; below this, suspect a bug


@dataclass
class Prediction:
    now: np.ndarray                                   # (n,)
    forecast: dict[int, dict[float, np.ndarray]]      # forecast[h][q] -> (n,)
    now_std: np.ndarray | None = None


# ---------------------------------------------------------------------------
# Leakage guard
# ---------------------------------------------------------------------------
def assert_no_overlap(trained_on: set[str], evaluated_on: set[str]) -> None:
    """Refuse to score a model on anyone it was trained on."""
    overlap = trained_on & evaluated_on
    if overlap:
        raise ValueError(f"Leakage: model was trained on {len(overlap)} of the evaluation people, e.g. {sorted(overlap)[:3]}")


# ---------------------------------------------------------------------------
# Small metric helpers
# ---------------------------------------------------------------------------
def pinball(y: np.ndarray, yhat: np.ndarray, q: float) -> float:
    """Quantile loss: L_q = max(q * (y - yhat), (q - 1) * (y - yhat)), averaged."""
    d = y - yhat
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def bootstrap_ci(per_person_values: list[float], n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% confidence interval for the mean of a per-person metric, by resampling people."""
    vals = np.array(per_person_values)
    rng = np.random.default_rng(seed)
    means = [vals[rng.integers(0, len(vals), len(vals))].mean() for _ in range(n_boot)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _onsets(active: np.ndarray) -> list[int]:
    """Minutes where an alert switches on (false -> true)."""
    prev = np.concatenate([[False], active[:-1]])
    return list(np.flatnonzero(active & ~prev))


# ---------------------------------------------------------------------------
# Alert rules (same as the live system, from backend/config.py)
# ---------------------------------------------------------------------------
def alert_series(pred: Prediction, level: str) -> np.ndarray:
    """
    Which minutes the alert would be on.
      red:    current estimate or 10-min 90th percentile >= HEAT_DANGER_C
      yellow: 20-min 90th percentile >= HEAT_WARNING_C
    """
    q = config.HEAT_ALERT_QUANTILE
    if level == "red":
        f10 = pred.forecast[config.HEAT_DANGER_HORIZON_MIN][q]
        return (pred.now >= config.HEAT_DANGER_C) | (f10 >= config.HEAT_DANGER_C)
    f20 = pred.forecast[config.HEAT_WARNING_HORIZON_MIN][q]
    return f20 >= config.HEAT_WARNING_C


def score_alerts(sessions: list[Session], preds: list[Prediction], level: str) -> dict:
    """
    Episode = the first measured minute a session's true core temp reaches the threshold.
    Caught  = an alert is on at that minute (it switched on at or before the crossing).
    Lead    = crossing minute minus the minute that alert switched on.
    False alarm = an alert switches on and the true temp does NOT reach the threshold
                  within the next 30 minutes (and isn't already above it).
    """
    threshold = config.HEAT_DANGER_C if level == "red" else config.HEAT_WARNING_C
    episodes = caught = 0
    leads, false_alarms, minutes = [], 0, 0
    for s, p in zip(sessions, preds):
        n = s.n_minutes
        ct = np.where(s.core_temp_measured, s.signals["core_temp"], np.nan)
        active = alert_series(p, level)
        active[:EVAL_START_MIN] = False
        minutes += n - EVAL_START_MIN
        above = np.flatnonzero(ct >= threshold)
        if len(above):
            episodes += 1
            t_cross = int(above[0])
            if active[t_cross]:
                caught += 1
                start = t_cross
                while start > 0 and active[start - 1]:
                    start -= 1
                leads.append(t_cross - start)
        for t in _onsets(active):
            window = ct[t:min(n, t + LEAD_WINDOW_MIN + 1)]
            if not np.any(window >= threshold):
                false_alarms += 1
    return {
        "threshold": threshold,
        "episodes": episodes,
        "caught_pct": 100 * caught / episodes if episodes else float("nan"),
        "mean_lead_min": float(np.mean(leads)) if leads else 0.0,
        "false_alarms_per_hr": false_alarms / (minutes / 60) if minutes else float("nan"),
    }


# ---------------------------------------------------------------------------
# The main report
# ---------------------------------------------------------------------------
@dataclass
class Report:
    name: str
    estimation: dict
    forecast: dict
    alerts: dict
    per_person_rmse: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def evaluate(name: str, predictor, sessions: list[Session], trained_on: set[str] | None = None) -> Report:
    """Score one predictor on a list of sessions."""
    if trained_on is not None:
        assert_no_overlap(trained_on, {s.person_id for s in sessions})
    preds = [predictor.predict(s) for s in sessions]

    # --- Estimation: how close is "now" to the measured core temp?
    errs, per_person = [], {}
    for s, p in zip(sessions, preds):
        m = s.core_temp_measured.copy()
        m[:EVAL_START_MIN] = False
        m &= ~np.isnan(p.now)
        e = p.now[m] - s.signals["core_temp"][m]
        errs.append(e)
        per_person.setdefault(s.person_id, []).append(e)
    errs = np.concatenate(errs)
    rmse = float(np.sqrt(np.mean(errs**2)))
    bias = float(errs.mean())
    person_rmse = {pid: float(np.sqrt(np.mean(np.concatenate(es) ** 2))) for pid, es in per_person.items()}
    estimation = {
        "rmse": rmse,
        "rmse_ci95": bootstrap_ci(list(person_rmse.values())),
        "bias": bias,
        "loa95": (bias - 1.96 * errs.std(), bias + 1.96 * errs.std()),
        "n_minutes": int(len(errs)),
    }

    # --- Forecasts: for each horizon, compare the prediction made at t with the truth at t + h.
    forecast = {}
    for h in HORIZONS:
        ys, qs = [], {q: [] for q in QUANTILES}
        for s, p in zip(sessions, preds):
            n = s.n_minutes
            if n <= h + EVAL_START_MIN:
                continue
            t = np.arange(EVAL_START_MIN, n - h)
            ok = s.core_temp_measured[t + h]
            for q in QUANTILES:
                ok &= ~np.isnan(p.forecast[h][q][t])
            t = t[ok]
            ys.append(s.signals["core_temp"][t + h])
            for q in QUANTILES:
                qs[q].append(p.forecast[h][q][t])
        y = np.concatenate(ys)
        qv = {q: np.concatenate(v) for q, v in qs.items()}
        forecast[h] = {
            "rmse_median": float(np.sqrt(np.mean((qv[0.5] - y) ** 2))),
            "quantile_loss": float(np.mean([pinball(y, qv[q], q) for q in QUANTILES])),
            "coverage_p90": float(np.mean(y <= qv[0.9])),          # target: 0.90
            "coverage_10_90": float(np.mean((y >= qv[0.1]) & (y <= qv[0.9]))),   # target: 0.80
            "n": int(len(y)),
        }

    alerts = {lvl: score_alerts(sessions, preds, lvl) for lvl in ("yellow", "red")}

    warnings = []
    if rmse < SUSPICIOUSLY_GOOD_RMSE:
        warnings.append(f"Estimation RMSE {rmse:.3f} °C is better than published research. Check for leakage or bugs.")
    for h, f in forecast.items():
        if f["coverage_p90"] < 0.8:
            warnings.append(f"+{h} min: only {f['coverage_p90']:.0%} of truths fall under the 90th percentile (should be ~90%).")

    return Report(name, estimation, forecast, alerts, person_rmse, warnings)


def print_reports(reports: list[Report]) -> None:
    """A side-by-side table, one column per model."""
    col = 22
    names = [r.name for r in reports]
    line = lambda label, vals: print(f"{label:<34}" + "".join(f"{v:>{col}}" for v in vals))
    print()
    line("", names)
    print("-" * (34 + col * len(reports)))
    e = [r.estimation for r in reports]
    line("NOW  RMSE °C (95% CI by person)", [f"{x['rmse']:.3f} ({x['rmse_ci95'][0]:.2f}-{x['rmse_ci95'][1]:.2f})" for x in e])
    line("NOW  bias °C", [f"{x['bias']:+.3f}" for x in e])
    line("NOW  95% limits of agreement", [f"{x['loa95'][0]:+.2f} to {x['loa95'][1]:+.2f}" for x in e])
    for h in HORIZONS:
        f = [r.forecast[h] for r in reports]
        line(f"+{h}m RMSE of median °C", [f"{x['rmse_median']:.3f}" for x in f])
        line(f"+{h}m quantile loss", [f"{x['quantile_loss']:.4f}" for x in f])
        line(f"+{h}m coverage p90 (want 0.90)", [f"{x['coverage_p90']:.2f}" for x in f])
        line(f"+{h}m coverage 10-90 (want 0.80)", [f"{x['coverage_10_90']:.2f}" for x in f])
    for lvl in ("yellow", "red"):
        a = [r.alerts[lvl] for r in reports]
        line(f"{lvl.upper()} episodes caught %", [f"{x['caught_pct']:.0f}% of {x['episodes']}" for x in a])
        line(f"{lvl.upper()} mean lead time (min)", [f"{x['mean_lead_min']:.1f}" for x in a])
        line(f"{lvl.upper()} false alarms / hour", [f"{x['false_alarms_per_hr']:.2f}" for x in a])
    for r in reports:
        for w in r.warnings:
            print(f"WARNING [{r.name}]: {w}")


if __name__ == "__main__":
    import argparse

    from ml.baselines import BASELINES

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", default=[],
                    help="checkpoint(s) to score next to the baselines; add :raw to skip calibration")
    ap.add_argument("--on", default="val", choices=["val", "test"], help="which people to score on")
    args = ap.parse_args()
    if args.on == "test":
        print("WARNING: scoring on TEST people. Do this once, for final numbers only.")

    data = load_mendeley()
    split = load_split("mendeley")
    train, people = sessions_in(data, split["train"]), split[args.on]
    sessions = sessions_in(data, people)
    reports = []
    for name, make in BASELINES.items():
        model = make()
        model.fit(train)
        reports.append(evaluate(name, model, sessions, trained_on=set(split["train"])))
    for path in args.model:
        from backend.models.forecaster import ForecasterPredictor
        raw = path.endswith(":raw")          # "ml/checkpoints/v1.pt:raw" scores the uncalibrated model
        model = ForecasterPredictor.load(path.removesuffix(":raw"), calibrated=not raw)
        reports.append(evaluate(model.name, model, sessions, trained_on=model.trained_on))
    print(f"Scored on {args.on.upper()} people: {len(people)}, sessions: {len(sessions)}")
    print_reports(reports)
