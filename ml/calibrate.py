"""
Conformal calibration: make "90%" really mean 90%.

A trained model's ranges are only its best guess at how unsure it is. Conformal
calibration checks them on people the model never trained on (the 10 calibration
people) and shifts them until they're honest:

  For each minute ahead k (1..30) and each edge of the range (10th and 90th percentile):
    1. Collect the misses: truth at t+k minus the predicted percentile, over every minute
       of every calibration session.
    2. The shift is the q-th percentile of those misses (with the standard small
       finite-sample adjustment). Adding it puts exactly ~q of the truths below the edge.

  For the NOW estimate: scale its uncertainty so ±1.645 sigma covers 90% of truths.

This is "split conformal" on top of quantile regression (Romano et al., 2019, CQR,
one-sided per percentile). Guarantee caveat: it assumes new people are like the
calibration people. Minutes within one session aren't independent, so we report
coverage on separate validation people too.

Run:  python -m ml.calibrate --model ml/checkpoints/v1.pt
      (writes the calibration INTO the checkpoint; load(calibrated=False) gives the raw model)
"""

import argparse
import math

import numpy as np
import torch

from backend.models.forecaster import HORIZON, ForecasterPredictor
from ml.data import Session, load_mendeley, load_split, sessions_in
from ml.evaluate import EVAL_START_MIN, assert_no_overlap


def conformal_shift(misses: np.ndarray, q: float) -> float:
    """
    Shift so that P(truth <= predicted_edge + shift) ~= q.
    Uses the conservative finite-sample rank: ceil((n+1) q) / n for the upper edge,
    floor((n+1) q) / n for the lower edge.
    """
    n = len(misses)
    if q >= 0.5:
        level = min(1.0, math.ceil((n + 1) * q) / n)
    else:
        level = max(0.0, math.floor((n + 1) * q) / n)
    return float(np.quantile(misses, level))


def collect_misses(predictor: ForecasterPredictor, sessions: list[Session]):
    """Misses for every minute ahead and percentile, plus standardized NOW errors."""
    misses = [[[] for _ in range(3)] for _ in range(HORIZON)]
    now_z = []
    for s in sessions:
        kal, now_delta, now_var, fc = predictor.predict_signals(s.signals)
        ct, measured = s.signals["core_temp"], s.core_temp_measured
        n = len(kal)
        m = measured.copy()
        m[:EVAL_START_MIN] = False
        now_z.append((ct[m] - (kal[m] + now_delta[m])) / np.sqrt(now_var[m]))
        for k in range(1, HORIZON + 1):
            t = np.arange(EVAL_START_MIN, n - k)
            t = t[measured[t + k]]
            truth = ct[t + k]
            for i in range(3):
                misses[k - 1][i].append(truth - (kal[t] + fc[t, k - 1, i]))
    misses = [[np.concatenate(m_i) for m_i in row] for row in misses]
    return misses, np.concatenate(now_z)


def fit_calibration(predictor: ForecasterPredictor, sessions: list[Session]) -> dict:
    misses, now_z = collect_misses(predictor, sessions)
    offsets = np.zeros((HORIZON, 3))
    for k in range(HORIZON):
        offsets[k, 0] = conformal_shift(misses[k][0], 0.1)
        offsets[k, 2] = conformal_shift(misses[k][2], 0.9)
        # median stays where the model put it (a shift here would trade accuracy for nothing)
    now_scale = float(np.quantile(np.abs(now_z), 0.9) / 1.645)
    return {"offsets": offsets.tolist(), "now_scale": now_scale}


def coverage(predictor: ForecasterPredictor, cal: dict, sessions: list[Session]) -> dict[int, float]:
    """Share of truths under the calibrated 90th percentile at +10/+20/+30 min."""
    misses, _ = collect_misses(predictor, sessions)
    off = np.array(cal["offsets"])
    return {k: float(np.mean(misses[k - 1][2] <= off[k - 1, 2])) for k in (10, 20, 30)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--people", default="calib", choices=["calib", "calib+val"],
                    help="who to calibrate on; calib+val (20 people) is steadier but uses up the val people")
    ap.add_argument("--swap-check", action="store_true",
                    help="calibrate on calib -> measure on val, and val -> calib, then report (writes nothing)")
    args = ap.parse_args()

    raw = ForecasterPredictor.load(args.model, calibrated=False)
    split = load_split("mendeley")
    data = load_mendeley()

    if args.swap_check:
        a, b = sessions_in(data, split["calib"]), sessions_in(data, split["val"])
        assert_no_overlap(raw.trained_on, set(split["calib"]) | set(split["val"]))
        for name_fit, fit_on, name_test, test_on in (("calib", a, "val", b), ("val", b, "calib", a)):
            cov = coverage(raw, fit_calibration(raw, fit_on), test_on)
            print(f"fit on {name_fit:5} -> 90th pct coverage on {name_test:5}: "
                  + "  ".join(f"+{k}m {v:.2f}" for k, v in cov.items()) + "   (target 0.90)")
        return

    people = split["calib"] + (split["val"] if args.people == "calib+val" else [])
    calib = sessions_in(data, people)
    assert_no_overlap(raw.trained_on, set(people))

    cal = fit_calibration(raw, calib)
    ckpt = torch.load(args.model, map_location="cpu", weights_only=False)
    ckpt["calibration"] = cal
    ckpt["calibrated_on"] = sorted(people)
    torch.save(ckpt, args.model)

    off = np.array(cal["offsets"])
    print(f"Calibrated on {len(people)} people ({len(calib)} sessions).")
    for k in (10, 20, 30):
        print(f"  +{k} min: 10th pct shift {off[k - 1, 0]:+.3f} °C, 90th pct shift {off[k - 1, 2]:+.3f} °C")
    print(f"  NOW uncertainty scale x{cal['now_scale']:.2f}")
    if args.people == "calib+val":
        print("  Note: val people are now 'seen'. Score on test people only from here (once).")


if __name__ == "__main__":
    main()
