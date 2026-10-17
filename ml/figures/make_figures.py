"""
Slide-ready result figures for the FINAL model (final_cv) on the 10 locked TEST people,
next to the two Kalman baselines (raw ECTemp, and Kalman + trend).

Plotting only: no tuning, no choices made after seeing the plots. Examples are picked by
fixed rules (see each figure) and every headline number is computed from the data.

Run from the repo root:
    python -m ml.figures.make_figures                        (test people, final_cv)
    python -m ml.figures.make_figures --on val --model ens5  (the older validation plots)
Writes five PNGs into docs/figures/. Needs the gitignored data and checkpoints from the
main checkout (paths in MAIN below).
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch

from backend import config
from backend.models.forecaster import ForecasterPredictor
from ml.baselines import kalman_estimate, trend_slope
from ml.data import load_mendeley, load_split, sessions_in
from ml.evaluate import EVAL_START_MIN, Prediction, alert_series

torch.set_num_threads(2)

MAIN = Path("/Users/Annabelle/Documents/projects/planit-control")
OUT = Path("docs/figures")
HORIZONS = range(1, 31)
Z90 = 1.2816                      # 90th percentile of a normal distribution (raw Kalman's own range)

# One colour per model, the same on every figure (validated default palette, light mode).
BLUE, VIOLET, ORANGE = "#2a78d6", "#6250d6", "#eb6834"
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
COLORS = {"model": BLUE, "raw": ORANGE, "trend": VIOLET}
NAMES = {"model": "Our model (5-network ensemble)", "raw": "Army Kalman filter (raw)",
         "trend": "Kalman + trend (best baseline)"}
WHO = "10 unseen test people"     # shown on every figure; main() changes it for --on val

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "sans-serif", "font.size": 11, "text.color": INK, "axes.labelcolor": MUTED,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.edgecolor": GRID, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "legend.frameon": False,
})


# ---------------------------------------------------------------------------
# Predictions for every minute of every session
# ---------------------------------------------------------------------------
class Run:
    """One session's outputs: `now` and absolute low/median/high core temp for horizons 1..30."""

    def __init__(self, session, now, fc_abs):
        self.s = session
        self.now = now
        self.fc = fc_abs                                    # (n, 30, 3)
        self.truth = np.where(session.core_temp_measured, session.signals["core_temp"], np.nan)


def run_model(predictor, sessions):
    runs = []
    for s in sessions:
        kal, now_delta, _, fc = predictor.predict_signals(s.signals, s.meta.get("twins"))
        runs.append(Run(s, kal + now_delta, kal[:, None, None] + fc))
    return runs


def kalman_runs(sessions, train, with_trend: bool):
    """
    with_trend=False: raw ECTemp. Flat forecast, its own variance as the range (kalman_raw).
    with_trend=True:  Kalman + its recent trend, plus error percentiles learned on the TRAIN
                      people at each horizon (kalman_trend). Same recipe as ml/baselines.py.
    """
    off = {}
    if with_trend:
        res = {h: [] for h in HORIZONS}
        for s in train:
            ct, _ = kalman_estimate(s)
            slope = trend_slope(ct)
            for h in HORIZONS:
                t = np.arange(0, s.n_minutes - h)
                ok = s.core_temp_measured[t + h]
                res[h].append(s.signals["core_temp"][t[ok] + h] - (ct + slope * h)[t[ok]])
        off = {h: np.quantile(np.concatenate(res[h]), [0.1, 0.5, 0.9]) for h in HORIZONS}
    runs = []
    for s in sessions:
        ct, v = kalman_estimate(s)
        if with_trend:
            slope = trend_slope(ct)
            fc = np.stack([np.stack([ct + slope * h + off[h][i] for i in range(3)], axis=-1) for h in HORIZONS], axis=1)
        else:
            sd = np.sqrt(v)
            band = np.stack([ct - Z90 * sd, ct, ct + Z90 * sd], axis=-1)
            fc = np.repeat(band[:, None, :], len(HORIZONS), axis=1)
        runs.append(Run(s, ct, fc))
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


def rmse_at(runs, h):
    y, f = pairs_at_horizon(runs, h)
    return float(np.sqrt(np.mean((f[:, 1] - y) ** 2)))


def coverage_at(runs, h):
    y, f = pairs_at_horizon(runs, h)
    return float(np.mean(y <= f[:, 2]))


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=140)
    plt.close(fig)
    print("wrote", OUT / name, f"{(OUT / name).stat().st_size / 1024:.0f} KB")


def headline(fig, title, sub):
    """Bold headline + grey subtitle, wrapped to the figure width. Returns the y where the plot may start."""
    import textwrap
    w = fig.get_figwidth()
    t_lines = textwrap.wrap(title.replace(" °C", "\u00a0°C"), int(w * 7.6))
    s_lines = textwrap.wrap(f"{sub} Scored on {WHO}.".replace(" °C", "\u00a0°C"), int(w * 12.5))
    h = fig.get_figheight()
    y = 0.975
    fig.text(0.02, y, "\n".join(t_lines), fontsize=15, fontweight="bold", va="top", ha="left")
    y -= len(t_lines) * 0.062 * 4.6 / h + 0.012
    fig.text(0.02, y, "\n".join(s_lines), fontsize=10.5, color=MUTED, va="top", ha="left")
    y -= len(s_lines) * 0.04 * 4.6 / h + 0.035
    return y


# ---------------------------------------------------------------------------
# 1. Predicted vs measured, three people
# ---------------------------------------------------------------------------
def fig_examples(model, peak):
    """Rule: heat sessions sorted by peak temp; take the lowest, the middle, and the highest peak that crosses 38.5 °C."""
    heat = sorted((r for r in model if r.s.condition == "heat"), key=lambda r: peak[r.s.session_id])
    crossing = [r for r in heat if peak[r.s.session_id] >= config.HEAT_DANGER_C]
    picks = [heat[0], heat[len(heat) // 2], (crossing or heat)[-1]]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.6), sharey=True)
    for ax, r in zip(axes, picks):
        n = r.s.n_minutes
        t = np.arange(n)
        h = 20
        # the forecast made at minute t-20 is a claim about minute t: line it up under the truth
        x = t[h:]
        lo, med, hi = (r.fc[:n - h, h - 1, i] for i in range(3))
        ax.fill_between(x, lo, hi, color=BLUE, alpha=0.18, lw=0, label="10th to 90th percentile range")
        ax.plot(x, med, color=BLUE, lw=2, label="Our model, made 20 min earlier")
        ax.plot(t, r.truth, color=INK, lw=0, marker="o", ms=3, label="Measured core temp (pill)")
        ax.axhline(config.HEAT_DANGER_C, color=MUTED, lw=1, ls="--")
        ax.text(0.5, config.HEAT_DANGER_C + 0.03, "red alert level 38.5 °C", color=MUTED, fontsize=9)
        ax.set_title(f"{r.s.person_id.replace('mendeley-', 'Person ')}, {r.s.condition}  (peak {peak[r.s.session_id]:.1f} °C)",
                     fontsize=11, loc="left")
        ax.set_xlabel("Minutes into the session")
        ax.set_ylim(36.9, 39.6)
    axes[0].set_ylabel("Core temperature (°C)")
    axes[0].legend(loc="lower right", fontsize=8.5)
    top = headline(fig, "20-minute-ahead forecasts next to the measured core temperature",
             "Each blue point was predicted 20 minutes before the minute it sits on. Lowest peak, middle, and a session that crosses 38.5 °C.")
    fig.subplots_adjust(top=top, bottom=0.14, left=0.06, right=0.99, wspace=0.08)
    save(fig, "01_predicted_vs_measured.png")


# ---------------------------------------------------------------------------
# 2. Bland-Altman
# ---------------------------------------------------------------------------
def fig_bland_altman(model, raw):
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.9), sharey=True, sharex=True)
    stats = {}
    for ax, runs, key in ((axes[0], raw, "raw"), (axes[1], model, "model")):
        est, truth = [], []
        for r in runs:
            ok = ~np.isnan(r.truth)
            ok[:EVAL_START_MIN] = False
            est.append(r.now[ok])
            truth.append(r.truth[ok])
        est, truth = np.concatenate(est), np.concatenate(truth)
        diff, avg = est - truth, (est + truth) / 2
        bias, sd = diff.mean(), diff.std()
        stats[key] = float(np.sqrt(np.mean(diff ** 2)))
        ax.scatter(avg, diff, s=7, color=COLORS[key], alpha=0.22, lw=0)
        ax.axhline(0, color=INK, lw=1)
        for y in (bias - 1.96 * sd, bias + 1.96 * sd):
            ax.axhline(y, color=MUTED, lw=1, ls="--")
            ax.text(39.25, y + 0.03, f"{y:+.2f}", ha="right", fontsize=9, color=MUTED)
        ax.set_title(f"{'Army Kalman filter (raw)' if key == 'raw' else 'Our model'}: "
                     f"typical error {stats[key]:.2f} °C", fontsize=11.5, loc="left")
        ax.set_xlabel("Average of estimate and measured core temp (°C)")
        ax.text(36.95, -1.62, "dashed lines: where 95% of errors fall", fontsize=9, color=MUTED)
    axes[0].set_ylabel("Estimate minus measured (°C)")
    axes[0].set_ylim(-1.8, 1.8)
    axes[0].set_xlim(36.9, 39.5)
    top = headline(fig, f"Core temp right now: {stats['model']:.2f} °C typical error vs {stats['raw']:.2f} °C for the Kalman filter",
             "Bland-Altman plot. Each dot is one minute. Closer to the zero line is better.")
    fig.subplots_adjust(top=top, bottom=0.13, left=0.07, right=0.99, wspace=0.06)
    save(fig, "02_bland_altman.png")


# ---------------------------------------------------------------------------
# 3. Coverage by horizon, 4. RMSE by horizon
# ---------------------------------------------------------------------------
def fig_coverage(sets):
    fig, ax = plt.subplots(figsize=(8.5, 4.9))
    for key, runs in sets.items():
        ax.plot(list(HORIZONS), [coverage_at(runs, h) * 100 for h in HORIZONS], color=COLORS[key], lw=2.2, label=NAMES[key])
    ax.axhline(90, color=INK, lw=1, ls="--")
    ax.text(30, 90.8, "target: 90%", ha="right", fontsize=9.5)
    ax.set_ylim(40, 102)
    ax.set_xlim(1, 30)
    ax.set_xlabel("How many minutes ahead the forecast looks")
    ax.set_ylabel("Share of real temperatures\nbelow the 90th percentile (%)")
    ax.legend(loc="lower left", fontsize=9.5)
    c = {k: coverage_at(sets[k], 20) * 100 for k in sets}
    top = headline(fig, f"At +20 min, {c['model']:.0f}% of real values fall under our 90th percentile line",
                   f"Target is 90% (dashed). Raw Kalman {c['raw']:.0f}%, Kalman + trend {c['trend']:.0f}%. Below the target means overconfident.")
    fig.subplots_adjust(top=top, bottom=0.14, left=0.12, right=0.97)
    save(fig, "03_coverage_by_horizon.png")


def fig_rmse(sets):
    fig, ax = plt.subplots(figsize=(8.5, 4.9))
    for key, runs in sets.items():
        rm = [rmse_at(runs, h) for h in HORIZONS]
        ax.plot(list(HORIZONS), rm, color=COLORS[key], lw=2.2, label=NAMES[key])
        ax.text(30.4, rm[-1], f"{rm[-1]:.2f}", color=COLORS[key], fontsize=10, va="center", fontweight="bold")
    ax.set_xlim(1, 31.5)
    ax.set_ylim(0, 0.62)
    ax.set_xlabel("How many minutes ahead the forecast looks")
    ax.set_ylabel("Typical forecast error (°C)")
    ax.legend(loc="lower left", fontsize=9.5)
    r = {k: rmse_at(sets[k], 20) for k in sets}
    top = headline(fig, f"At +20 min our typical error is {r['model']:.2f} °C (raw Kalman {r['raw']:.2f}, Kalman + trend {r['trend']:.2f})",
                   "Typical error (RMSE) of the median forecast at every horizon. Lower is better.")
    fig.subplots_adjust(top=top, bottom=0.14, left=0.11, right=0.95)
    save(fig, "04_rmse_by_horizon.png")


# ---------------------------------------------------------------------------
# 5. Alert timeline
# ---------------------------------------------------------------------------
def _alert_start(active, tc):
    """First minute of the unbroken alert run that is still on at minute tc (None if off at tc)."""
    if not active[tc]:
        return None
    start = tc
    while start > 0 and active[start - 1]:
        start -= 1
    return start


def fig_alert_timeline(model, trend):
    """Rule: among crossings of 38.5 °C that our red alert caught, show the one with the MEDIAN lead time."""
    thr = config.HEAT_DANGER_C
    rows, n_cross = [], 0
    for e, k in zip(model, trend):
        above = np.flatnonzero(e.truth >= thr)
        if not len(above):
            continue
        n_cross += 1
        tc = int(above[0])
        act = alert_series(as_prediction(e), "red")
        act[:EVAL_START_MIN] = False
        start = _alert_start(act, tc)
        if start is not None:
            rows.append((tc - start, e, k, tc, start))
    rows.sort(key=lambda r: r[0])
    lead, e, k, tc, start = rows[len(rows) // 2]
    ka = alert_series(as_prediction(k), "red")
    ka[:EVAL_START_MIN] = False
    k_start = _alert_start(ka, tc)
    n = e.s.n_minutes
    t = np.arange(n)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(t, e.truth, color=INK, lw=0, marker="o", ms=3.2, label="Measured core temp (pill)")
    ax.plot(t, e.now, color=BLUE, lw=2, label="Our model's estimate")
    ax.plot(t, k.now, color=VIOLET, lw=2, label="Kalman filter's estimate")
    ax.axhline(thr, color=MUTED, lw=1, ls="--")
    ax.text(1, thr + 0.03, "red level 38.5 °C", color=MUTED, fontsize=9.5)
    ax.axvspan(start, tc, color=BLUE, alpha=0.12, lw=0)
    ax.axvline(start, color=BLUE, lw=1.4)
    ax.axvline(tc, color=INK, lw=1.4)
    ax.annotate("", xy=(start, 36.95), xytext=(tc, 36.95), arrowprops=dict(arrowstyle="<->", color=BLUE, lw=1.6))
    ax.text((start + tc) / 2 + 3, 37.1, f"{lead} minutes of warning", ha="center", color=BLUE, fontsize=11, fontweight="bold")
    ax.text(start - 1, thr + 0.6, "Our red alert fires", ha="right", fontsize=9.5, color=BLUE)
    ax.text(tc + 1, 37.55, "Real temperature\nreaches 38.5 °C", ha="left", fontsize=9.5)
    k_txt = "Kalman + trend: no alert in time"
    if k_start is not None:
        k_txt = f"Kalman + trend alert\n({tc - k_start} min ahead)"
        ax.axvline(k_start, color=VIOLET, lw=1.2, ls=":")
    ax.text(0.99, 0.97, k_txt, transform=ax.transAxes, ha="right", va="top", fontsize=9.5, color=VIOLET)
    ax.set_xlabel("Minutes into the session")
    ax.set_ylabel("Core temperature (°C)")
    ax.set_ylim(36.9, 39.6)
    ax.legend(loc="lower right", fontsize=9.5, bbox_to_anchor=(1.0, 0.06))
    pid = e.s.person_id.replace("mendeley-", "Person ")
    leads = [r[0] for r in rows]
    top = headline(fig, f"Red alert fired {lead} minutes before this worker really hit 38.5 °C",
                   f"{pid}: the caught crossing with the median lead time (not hand-picked). Our alert caught {len(rows)} of {n_cross} crossings.")
    fig.subplots_adjust(top=top, bottom=0.12, left=0.08, right=0.98)
    save(fig, "05_alert_timeline.png")
    print(f"  alert timeline: {e.s.session_id}, lead {lead} min; all caught leads {leads}; crossings {n_cross}")


def main():
    global WHO
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", default="test", choices=["test", "val"])
    ap.add_argument("--model", default="final_cv", help="checkpoint name in the main checkout's ml/checkpoints/")
    args = ap.parse_args()
    WHO = "10 unseen test people" if args.on == "test" else "10 validation people"

    data = load_mendeley(MAIN / "data/raw/mendeley_buono/data.csv")
    split = load_split("mendeley")
    people, train = sessions_in(data, split[args.on]), sessions_in(data, split["train"])
    predictor = ForecasterPredictor.load(MAIN / f"ml/checkpoints/{args.model}.pt")      # calibrated, as shipped
    overlap = predictor.trained_on & {s.person_id for s in people}
    assert not overlap, f"model saw these people: {sorted(overlap)[:3]}"
    model = run_model(predictor, people)
    raw = kalman_runs(people, train, with_trend=False)
    trend = kalman_runs(people, train, with_trend=True)
    print(f"{args.model} on {args.on}: {len(people)} sessions, {len({s.person_id for s in people})} people")
    peak = {r.s.session_id: np.nanmax(r.truth) for r in model}
    sets = {"model": model, "raw": raw, "trend": trend}
    fig_examples(model, peak)
    fig_bland_altman(model, raw)
    fig_coverage(sets)
    fig_rmse(sets)
    fig_alert_timeline(model, trend)
    for h in (10, 20, 30):          # cross-check against docs/results.md
        print(f"  +{h}: RMSE " + "  ".join(f"{k} {rmse_at(v, h):.3f}" for k, v in sets.items())
              + " | p90 coverage " + "  ".join(f"{k} {coverage_at(v, h):.2f}" for k, v in sets.items()))


if __name__ == "__main__":
    main()
