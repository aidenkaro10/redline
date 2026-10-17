"""
Slide-ready result figures, validation people only (never test people).

Run from the repo root:   python -m ml.figures.make_figures
Writes five PNGs into docs/figures/. Needs the gitignored data and checkpoints from the
main checkout (paths in MAIN below).

Every chart: plain-word headline that says the finding, labeled axes in plain units,
one colour per model (same colour on every figure), thin marks, recessive grid.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

from backend import config
from backend.models.forecaster import ForecasterPredictor
from ml.data import load_mendeley, load_split, sessions_in
from ml.evaluate import EVAL_START_MIN, Prediction, alert_series

torch.set_num_threads(2)

MAIN = Path("/Users/Annabelle/Documents/projects/planit-control")
OUT = Path("docs/figures")
HORIZONS = range(1, 31)

# One colour per model, the same on every figure (validated default palette, light mode).
BLUE, VIOLET, ORANGE = "#2a78d6", "#6250d6", "#eb6834"
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
COLORS = {"ens5": BLUE, "v1": VIOLET, "kalman": ORANGE}
NAMES = {"ens5": "New model (5-network ensemble)", "v1": "Earlier model (single network)", "kalman": "Army Kalman filter + error bars"}

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "sans-serif", "font.size": 11, "text.color": INK, "axes.labelcolor": MUTED,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.edgecolor": GRID, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "legend.frameon": False,
})


# ---------------------------------------------------------------------------
# Predictions for every minute of every val session
# ---------------------------------------------------------------------------
class Run:
    """All model outputs for one session: now, and low/median/high for horizons 1..30."""

    def __init__(self, session, kal, now_delta, fc):
        self.s = session
        self.kal, self.now = kal, kal + now_delta
        self.fc = kal[:, None, None] + fc                  # (n, 30, 3) absolute core temp
        self.truth = np.where(session.core_temp_measured, session.signals["core_temp"], np.nan)


def run_model(predictor, sessions):
    runs = []
    for s in sessions:
        kal, now_delta, _, fc = predictor.predict_signals(s.signals)
        runs.append(Run(s, kal, now_delta, fc))
    return runs


def kalman_runs(sessions, train):
    """
    The Kalman estimate held flat, with error percentiles learned on training people (the fair
    baseline: "kalman_flat" in ml/baselines.py, the better of the two Kalman baselines at 20 and 30 min).
    """
    from ml.baselines import kalman_estimate
    res = {h: [] for h in HORIZONS}
    for s in train:
        ct, _ = kalman_estimate(s)
        for h in HORIZONS:
            t = np.arange(0, s.n_minutes - h)
            ok = s.core_temp_measured[t + h]
            res[h].append(s.signals["core_temp"][t[ok] + h] - ct[t[ok]])
    off = {h: np.quantile(np.concatenate(res[h]), [0.1, 0.5, 0.9]) for h in HORIZONS}
    runs = []
    for s in sessions:
        ct, _ = kalman_estimate(s)
        fc = np.stack([np.stack([ct + off[h][i] for i in range(3)], axis=-1) for h in HORIZONS], axis=1)
        runs.append(Run(s, ct, np.zeros_like(ct), fc - ct[:, None, None]))
    return runs


def pairs_at_horizon(runs, h):
    """(truth at t+h, forecast made at t) for every scored minute."""
    ys, fs = [], []
    for r in runs:
        n = r.s.n_minutes
        t = np.arange(EVAL_START_MIN, n - h)
        ok = ~np.isnan(r.truth[t + h])
        ys.append(r.truth[t[ok] + h])
        fs.append(r.fc[t[ok], h - 1])
    return np.concatenate(ys), np.concatenate(fs)


def as_prediction(r):
    forecast = {h: {q: r.fc[:, h - 1, i] for i, q in enumerate(config.FORECAST_QUANTILES)} for h in config.FORECAST_HORIZONS_MIN}
    return Prediction(now=r.now, forecast=forecast)


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=140)
    plt.close(fig)
    print("wrote", OUT / name, f"{(OUT / name).stat().st_size / 1024:.0f} KB")


def headline(fig, title, sub):
    fig.text(0.02, 0.965, title, fontsize=15, fontweight="bold", va="top", ha="left")
    fig.text(0.02, 0.905, sub, fontsize=10.5, color=MUTED, va="top", ha="left")


# ---------------------------------------------------------------------------
# 1. Predicted vs measured, three people
# ---------------------------------------------------------------------------
def fig_examples(ens, kal, peak):
    heat = sorted((r for r in ens if r.s.condition == "heat"), key=lambda r: peak[r.s.session_id])
    hot = [r for r in heat if peak[r.s.session_id] >= config.HEAT_DANGER_C][-1]
    picks = [heat[0], heat[len(heat) // 2], hot]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6), sharey=True)
    for ax, r in zip(axes, picks):
        n = r.s.n_minutes
        t = np.arange(n)
        h = 20
        # the forecast made at minute t-20 is a claim about minute t: line it up under the truth
        x = t[h:]
        lo, med, hi = (r.fc[:n - h, h - 1, i] for i in range(3))
        ax.fill_between(x, lo, hi, color=BLUE, alpha=0.18, lw=0, label="10th to 90th percentile range")
        ax.plot(x, med, color=BLUE, lw=2, label="New model, made 20 min earlier")
        ax.plot(t, r.truth, color=INK, lw=0, marker="o", ms=3, label="Measured core temp (pill)")
        ax.axhline(config.HEAT_DANGER_C, color=MUTED, lw=1, ls="--")
        ax.text(0.5, config.HEAT_DANGER_C + 0.03, "red alert level 38.5 °C", color=MUTED, fontsize=9)
        ax.set_title(f"{r.s.person_id.replace('mendeley-', 'Person ')}  (peak {peak[r.s.session_id]:.1f} °C)",
                     fontsize=11, loc="left")
        ax.set_xlabel("Minutes into the session")
        ax.set_ylim(36.9, 39.5)
    axes[0].set_ylabel("Core temperature (°C)")
    axes[0].legend(loc="lower right", fontsize=8.5)
    headline(fig, "20-minute-ahead forecasts follow the measured core temperature, and the range mostly holds it",
             "Held-out people (never trained on), heat sessions. Each blue point was predicted 20 minutes before the minute it sits on.")
    fig.subplots_adjust(top=0.78, bottom=0.14, left=0.06, right=0.99, wspace=0.08)
    save(fig, "01_predicted_vs_measured.png")


# ---------------------------------------------------------------------------
# 2. Bland-Altman
# ---------------------------------------------------------------------------
def fig_bland_altman(ens, kal):
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.9), sharey=True, sharex=True)
    for ax, runs, key in ((axes[0], kal, "kalman"), (axes[1], ens, "ens5")):
        est, truth = [], []
        for r in runs:
            ok = ~np.isnan(r.truth)
            ok[:EVAL_START_MIN] = False
            est.append(r.now[ok])
            truth.append(r.truth[ok])
        est, truth = np.concatenate(est), np.concatenate(truth)
        diff, avg = est - truth, (est + truth) / 2
        bias, sd = diff.mean(), diff.std()
        ax.scatter(avg, diff, s=7, color=COLORS[key], alpha=0.22, lw=0)
        ax.axhline(0, color=INK, lw=1)
        for y in (bias - 1.96 * sd, bias + 1.96 * sd):
            ax.axhline(y, color=MUTED, lw=1, ls="--")
            ax.text(38.95, y + 0.03, f"{y:+.2f}", ha="right", fontsize=9, color=MUTED)
        ax.set_title(f"{'Army Kalman filter' if key == 'kalman' else 'New model'}: "
                     f"typical error {np.sqrt(np.mean(diff ** 2)):.2f} °C", fontsize=11.5, loc="left")
        ax.set_xlabel("Average of estimate and measured core temp (°C)")
        ax.text(36.95, -1.62, "dashed lines: where 95% of errors fall", fontsize=9, color=MUTED)
    axes[0].set_ylabel("Estimate minus measured (°C)")
    axes[0].set_ylim(-1.8, 1.8)
    axes[0].set_xlim(36.9, 39.0)
    headline(fig, "The new model's estimate is much closer to the truth: 0.24 °C typical error vs 0.41 °C",
             "Bland-Altman plot, held-out people. Each dot is one minute. Closer to the zero line is better.")
    fig.subplots_adjust(top=0.80, bottom=0.13, left=0.07, right=0.99, wspace=0.06)
    save(fig, "02_bland_altman.png")


# ---------------------------------------------------------------------------
# 3. Coverage by horizon, 4. RMSE by horizon
# ---------------------------------------------------------------------------
def fig_coverage(sets):
    fig, ax = plt.subplots(figsize=(8.5, 4.9))
    for key, runs in sets.items():
        cov = []
        for h in HORIZONS:
            y, f = pairs_at_horizon(runs, h)
            cov.append(np.mean(y <= f[:, 2]) * 100)
        ax.plot(list(HORIZONS), cov, color=COLORS[key], lw=2.2, label=NAMES[key])
    ax.axhline(90, color=INK, lw=1, ls="--")
    ax.text(30, 90.6, "target: 90%", ha="right", fontsize=9.5)
    ax.set_ylim(70, 100)
    ax.set_xlim(1, 30)
    ax.set_xlabel("How many minutes ahead the forecast looks")
    ax.set_ylabel("Share of real temperatures\nbelow the 90th percentile (%)")
    ax.legend(loc="lower left", fontsize=9.5)
    headline(fig, "The new model's 90th percentile line errs cautious",
             "Share of real temperatures under the line. Target is 90% (dashed); below means overconfident.")
    fig.subplots_adjust(top=0.82, bottom=0.14, left=0.12, right=0.97)
    save(fig, "03_coverage_by_horizon.png")


def fig_rmse(sets):
    fig, ax = plt.subplots(figsize=(8.5, 4.9))
    for key, runs in sets.items():
        rm = []
        for h in HORIZONS:
            y, f = pairs_at_horizon(runs, h)
            rm.append(np.sqrt(np.mean((f[:, 1] - y) ** 2)))
        ax.plot(list(HORIZONS), rm, color=COLORS[key], lw=2.2, label=NAMES[key])
        ax.text(30.4, rm[-1], f"{rm[-1]:.2f}", color=COLORS[key], fontsize=10, va="center", fontweight="bold")
    ax.set_xlim(1, 31.5)
    ax.set_ylim(0, 0.55)
    ax.set_xlabel("How many minutes ahead the forecast looks")
    ax.set_ylabel("Typical forecast error (°C)")
    ax.legend(loc="lower left", fontsize=9.5)
    headline(fig, "30 minutes ahead is still about twice as accurate as the Kalman filter",
             "Typical error (RMSE) of the median forecast, held-out people. Lower is better.")
    fig.subplots_adjust(top=0.82, bottom=0.14, left=0.11, right=0.95)
    save(fig, "04_rmse_by_horizon.png")


# ---------------------------------------------------------------------------
# 5. Alert timeline
# ---------------------------------------------------------------------------
def fig_alert_timeline(ens, kal):
    thr = config.HEAT_DANGER_C
    rows = []
    for e, k in zip(ens, kal):
        above = np.flatnonzero(e.truth >= thr)
        if not len(above):
            continue
        tc = int(above[0])
        act = alert_series(as_prediction(e), "red")
        act[:EVAL_START_MIN] = False
        if not act[tc]:
            continue
        start = tc
        while start > 0 and act[start - 1]:
            start -= 1
        rows.append((tc - start, e, k, tc, start))
    rows.sort(key=lambda r: r[0])
    lead, e, k, tc, start = rows[len(rows) // 2]       # the MEDIAN-lead caught crossing, not the prettiest
    ka = alert_series(as_prediction(k), "red")
    ka[:EVAL_START_MIN] = False
    k_start = None
    if ka[tc]:
        k_start = tc
        while k_start > 0 and ka[k_start - 1]:
            k_start -= 1
    n = e.s.n_minutes
    t = np.arange(n)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(t, e.truth, color=INK, lw=0, marker="o", ms=3.2, label="Measured core temp (pill)")
    ax.plot(t, e.now, color=BLUE, lw=2, label="New model's estimate")
    ax.plot(t, k.now, color=ORANGE, lw=2, label="Kalman filter's estimate")
    ax.axhline(thr, color=MUTED, lw=1, ls="--")
    ax.text(1, thr + 0.03, "red level 38.5 °C", color=MUTED, fontsize=9.5)
    ax.axvspan(start, tc, color=BLUE, alpha=0.12, lw=0)
    ax.axvline(start, color=BLUE, lw=1.4)
    ax.axvline(tc, color=INK, lw=1.4)
    ax.annotate("", xy=(start, 36.95), xytext=(tc, 36.95), arrowprops=dict(arrowstyle="<->", color=BLUE, lw=1.6))
    ax.text((start + tc) / 2, 37.03, f"{lead} minutes of warning", ha="center", color=BLUE, fontsize=11, fontweight="bold")
    ax.text(start - 1, thr + 0.6, "Red alert fires\n(new model)", ha="right", fontsize=9.5, color=BLUE)
    ax.text(tc + 1, 37.55, "Real temperature\nreaches 38.5 °C", ha="left", fontsize=9.5)
    if k_start is not None and k_start > start:
        ax.axvline(k_start, color=ORANGE, lw=1.2, ls=":")
        ax.text(k_start + 0.6, thr + 0.6, f"Kalman alert\n({tc - k_start} min)", ha="left", fontsize=9, color=ORANGE)
    ax.set_xlabel("Minutes into the session")
    ax.set_ylabel("Core temperature (°C)")
    ax.set_ylim(36.9, 39.6)
    ax.legend(loc="upper left", fontsize=9.5)
    pid = e.s.person_id.replace("mendeley-", "Person ")
    headline(fig, f"The red alert fired {lead} minutes before this worker really hit 38.5 °C",
             f"{pid}, held-out. The caught crossing with the median warning time among the held-out people (not hand-picked).")
    fig.subplots_adjust(top=0.82, bottom=0.12, left=0.08, right=0.98)
    save(fig, "05_alert_timeline.png")
    print(f"  alert timeline: {e.s.session_id}, lead {lead} min; all leads {[r[0] for r in rows]}")


def main():
    data = load_mendeley(MAIN / "data/raw/mendeley_buono/data.csv")
    split = load_split("mendeley")
    val, train = sessions_in(data, split["val"]), sessions_in(data, split["train"])
    ens = run_model(ForecasterPredictor.load(MAIN / "ml/checkpoints/ens5.pt", calibrated=False), val)
    v1 = run_model(ForecasterPredictor.load(MAIN / "ml/checkpoints/v1.pt", calibrated=False), val)
    kal = kalman_runs(val, train)
    peak = {r.s.session_id: np.nanmax(r.truth) for r in ens}
    fig_examples(ens, kal, peak)
    fig_bland_altman(ens, kal)
    sets = {"ens5": ens, "v1": v1, "kalman": kal}
    fig_coverage(sets)
    fig_rmse(sets)
    fig_alert_timeline(ens, kal)


if __name__ == "__main__":
    main()
