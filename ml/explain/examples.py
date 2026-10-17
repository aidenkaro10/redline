"""
Print why-lines for a few validation examples (and one simulated shift that has weather).

Run from the repo root:   python -m ml.explain.examples
Needs the gitignored data and checkpoint from the main checkout (paths below).
"""

import os
from pathlib import Path

import numpy as np

MAIN = Path("/Users/Annabelle/Documents/projects/planit-control")


def pick_examples(val_sessions):
    """3 val sessions: a neutral-weather one (stays cool), a middle heat session, one that crosses 38.5 °C."""
    peak = {s.session_id: np.nanmax(np.where(s.core_temp_measured, s.signals["core_temp"], np.nan)) for s in val_sessions}
    heat = sorted((s for s in val_sessions if s.condition == "heat"), key=lambda s: peak[s.session_id])
    cool = min((s for s in val_sessions if s.condition != "heat"), key=lambda s: peak[s.session_id])
    hot = [s for s in heat if peak[s.session_id] >= 38.5][-1]
    mid = heat[len(heat) // 2]
    return [(cool, "neutral weather, stays cool"), (mid, "heat, middle"), (hot, "heat, crosses 38.5 °C")], peak


def main():
    from backend.models.forecaster import ForecasterPredictor
    from ml.data import load_mendeley, load_split, sessions_in
    from ml.explain.explain import explain
    from ml.simulate import load_sim

    pred = ForecasterPredictor.load(MAIN / "ml/checkpoints/ens5.pt")
    os.chdir(MAIN)                       # data paths in ml/data.py are relative to the repo root
    split = load_split("mendeley")
    val = sessions_in(load_mendeley(), split["val"])
    examples, peak = pick_examples(val)
    for s, label in examples:
        ct = s.signals["core_temp"]
        for minute in sorted({min(m, s.n_minutes - 21) for m in (20, 40, 60)}):
            out = explain(s.signals, minute, pred)
            print(f"{s.session_id} ({label}, peak {peak[s.session_id]:.1f} °C) minute {minute}: "
                  f"est {out['now_c']:.1f} °C, 20-min p90 {out['forecast_p90_c']:.1f} °C, truth in 20 min {ct[minute + 20]:.1f} °C")
            print(f"    {out['why']}")
    sims = load_sim(MAIN / "data/sim/sim_v2.npz")
    s = sims[16]
    for minute in (250, 401):
        out = explain(s.signals, minute, pred)
        print(f"{s.session_id} (simulated, has weather) minute {minute}: est {out['now_c']:.1f} °C, "
              f"20-min p90 {out['forecast_p90_c']:.1f} °C\n    {out['why']}")


if __name__ == "__main__":
    main()
