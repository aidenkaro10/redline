"""
The 4-minute fitness check: an instant first guess at a new worker's twin.

A twin normally learns from hours of heart rate. A judge at our table gives us
4 minutes. So we use a short, standard protocol and a model trained to map it
straight to a twin ("amortized inference"):

    1 min standing still -> 2 min stepping in place -> 1 min standing still

How fast your heart rate climbs and how fast it recovers says a lot about your
fitness and heat tolerance. From the check's heart rate (one reading every 15 s,
like the live band/watch) we take 5 numbers:
    resting HR, peak HR, rise (peak - rest), how quickly it rose, how much it
    recovered in the last minute
and a ridge regression (plain linear algebra, no library) maps them to the twin.

Training data: every simulated person, with the SAME heart rate physiology the
simulator uses, and the twin the network learned for them during training.
Afterwards the twin keeps refining from real shift heart rate (ml/twins.py, gated).

Run:  python -m ml.fitness_check --model ml/checkpoints/twins16.pt
      (fits the regression, reports how well it predicts twins of unseen people,
       and saves it into the checkpoint)
"""

import argparse
import json

import numpy as np
import torch

from ml.simulate import HR_DAY_SD, HR_NOISE_SD, SIM_PATH

SAMPLE_S = 15
PROTOCOL = [(60, 1.2), (120, 4.5), (60, 1.2)]   # (seconds, metabolic rate in met): rest, stepping, rest
RIDGE_LAMBDA = 1.0


def protocol_met() -> np.ndarray:
    """Metabolic rate at each 15-second sample of the check."""
    return np.concatenate([np.full(sec // SAMPLE_S, met) for sec, met in PROTOCOL])


def simulate_check(person: dict, rng: np.random.Generator) -> np.ndarray:
    """Heart rate every 15 s during the check, using the simulator's heart rate model for this person."""
    met = protocol_met()
    shift = person.get("hr_offset", 0.0) + rng.normal(0, HR_DAY_SD)
    target = (person["rest_hr"] + shift + person["hr_per_met"] * (met - 1)
              - 8 * person.get("acclim_start", 0.0))
    alpha = 1 - np.exp(-(SAMPLE_S / 60) / 1.2)       # same ~1.2 min lag as the simulator
    hr = np.empty_like(target)
    h, noise = target[0], 0.0
    for i, tgt in enumerate(target):
        h = h + (tgt - h) * alpha
        noise = 0.6 * noise + rng.normal(0, HR_NOISE_SD)
        hr[i] = h + noise
    return hr


def check_features(hr: np.ndarray) -> np.ndarray:
    """The 5 numbers that summarize a fitness check (works with a few missing readings)."""
    n_rest = PROTOCOL[0][0] // SAMPLE_S
    n_step = PROTOCOL[1][0] // SAMPLE_S
    rest = np.nanmean(hr[:n_rest])
    step = hr[n_rest:n_rest + n_step]
    peak = np.nanmax(step)
    rise = peak - rest
    early = np.nanmean(step[:2])                       # first 30 s of stepping
    quickness = (early - rest) / rise if rise > 1 else 0.0
    recovery = peak - np.nanmean(hr[-2:])              # drop by the end of the last minute
    return np.array([rest, peak, rise, quickness, recovery])


class FitnessCheckModel:
    """Ridge regression from check features to a twin, with feature standardization."""

    def __init__(self, mean, scale, weights, bias):
        self.mean, self.scale = np.asarray(mean), np.asarray(scale)
        self.weights, self.bias = np.asarray(weights), np.asarray(bias)

    @classmethod
    def fit(cls, X: np.ndarray, Y: np.ndarray, lam: float = RIDGE_LAMBDA) -> "FitnessCheckModel":
        mean, scale = X.mean(0), X.std(0) + 1e-6
        Z = (X - mean) / scale
        bias = Y.mean(0)
        W = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (Y - bias))
        return cls(mean, scale, W, bias)

    def predict(self, features: np.ndarray) -> np.ndarray:
        return ((features - self.mean) / self.scale) @ self.weights + self.bias

    def twin_from_check(self, hr_15s: np.ndarray) -> np.ndarray:
        """Live use: heart rate every 15 s during the 4-minute check -> starting twin."""
        return self.predict(check_features(np.asarray(hr_15s, dtype=float)))

    def to_dict(self) -> dict:
        return {k: getattr(self, k).tolist() for k in ("mean", "scale", "weights", "bias")}


def build_dataset(twins: dict[str, list[float]], people: list[dict], rng) -> tuple[np.ndarray, np.ndarray, list[str]]:
    by_id = {p["person_id"]: p for p in people}
    X, Y, ids = [], [], []
    for pid, twin in twins.items():
        if pid in by_id:
            X.append(check_features(simulate_check(by_id[pid], rng)))
            Y.append(twin)
            ids.append(pid)
    return np.array(X), np.array(Y), ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    args = ap.parse_args()
    ckpt = torch.load(args.model, map_location="cpu", weights_only=False)
    twins = ckpt["twins"][0]                      # model 0's learned twins (sim + real training people)
    people = json.loads(SIM_PATH.with_suffix(".people.json").read_text())
    rng = np.random.default_rng(0)
    X, Y, ids = build_dataset(twins, people, rng)

    # Honest check: fit on 80% of people, score on the other 20%.
    order = rng.permutation(len(X))
    cut = int(0.8 * len(X))
    tr, te = order[:cut], order[cut:]
    m = FitnessCheckModel.fit(X[tr], Y[tr])
    pred = m.predict(X[te])
    ss_res = ((Y[te] - pred) ** 2).sum()
    ss_tot = ((Y[te] - Y[tr].mean(0)) ** 2).sum()
    print(f"{len(X)} simulated people. Held-out R^2 of predicted twins: {1 - ss_res / ss_tot:.2f} "
          f"(0 = no better than the average person, 1 = perfect)")

    final = FitnessCheckModel.fit(X, Y)
    ckpt["fitness_check"] = final.to_dict()
    torch.save(ckpt, args.model)
    print(f"Saved the fitness-check model into {args.model}")


if __name__ == "__main__":
    main()
