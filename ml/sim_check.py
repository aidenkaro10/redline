"""
Realism check: does the simulated world look like real people?

Compares simulated shifts against the real Mendeley data on the things that
matter for our models: how hot people get, heart rate ranges, and, most
importantly, how wrong the Kalman filter is. If the Kalman behaves very
differently on simulated data than on real people, the simulator is teaching
the network a fake world.

Run:  python -m ml.sim_check                 (uses data/sim/sim.npz)
"""

import argparse
import warnings
from pathlib import Path

import numpy as np

from backend.models.kalman import run_kalman
from ml.data import Session, load_mendeley
from ml.simulate import SIM_PATH, load_sim


def realism_stats(sessions: list[Session]) -> dict:
    peaks = np.array([np.nanmax(s.signals["core_temp"]) for s in sessions])
    errs = []
    for s in sessions:
        ct, _ = run_kalman(s.signals["hr"])
        m = s.core_temp_measured.copy()
        m[:10] = False
        errs.append(ct[m] - s.signals["core_temp"][m])
    e = np.concatenate(errs)
    hr = np.concatenate([s.signals["hr"] for s in sessions])
    ct = np.concatenate([s.signals["core_temp"] for s in sessions])
    ok = ~np.isnan(hr) & ~np.isnan(ct)
    return {
        "sessions": len(sessions),
        "peak core temp, median °C": np.median(peaks),
        "share reaching 38.0 °C": np.mean(peaks >= 38.0),
        "share reaching 38.5 °C": np.mean(peaks >= 38.5),
        "share reaching 39.0 °C": np.mean(peaks >= 39.0),
        "HR median bpm": np.nanmedian(hr),
        "HR 95th pct bpm": np.nanpercentile(hr, 95),
        "corr(HR, core temp)": np.corrcoef(hr[ok], ct[ok])[0, 1],
        "Kalman RMSE °C": np.sqrt(np.mean(e**2)),
        "Kalman bias °C": e.mean(),
        "Kalman 95% limits ±°C": 1.96 * e.std(),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", type=Path, default=SIM_PATH)
    ap.add_argument("--sample", type=int, default=2000, help="random shifts to check (Kalman in Python is slow)")
    args = ap.parse_args()
    warnings.simplefilter("ignore")
    sim_sessions = load_sim(args.sim)
    rng = np.random.default_rng(0)
    pick = rng.choice(len(sim_sessions), min(args.sample, len(sim_sessions)), replace=False)
    real, sim = realism_stats(load_mendeley()), realism_stats([sim_sessions[i] for i in pick])
    print(f"{'':32}{'real (Mendeley)':>18}{'simulated':>14}")
    for k in real:
        fmt = (lambda v: f"{v:.0%}") if k.startswith("share") else (lambda v: f"{v:.3f}" if abs(v) < 10 else f"{v:.0f}")
        print(f"{k:32}{fmt(real[k]):>18}{fmt(sim[k]):>14}")
