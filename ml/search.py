"""
Hyperparameter random search for the forecaster. Long-running (hours), CPU only.

Each trial picks random settings (model size, depth, dropout, learning rate, loss
weights, which simulation to pretrain on), trains a single model (pretrain + fine-tune),
and scores it on the Mendeley VALIDATION people with the harness. Results append to
ml/runs/search/results.csv after every trial, so the run can be stopped any time.

Objective (lower is better): +20 min quantile loss + 0.5 * NOW RMSE
Test people are never touched here.

Run:  python -m ml.search --trials 30
Then: python -m ml.search --best      (prints the best settings so far)
"""

import argparse
import csv
import json
import random
import time
from pathlib import Path

import torch

import ml.train as T
from backend.models.forecaster import ForecasterPredictor, HeatForecaster
from ml.data import load_mendeley, load_split, make_person_split, sessions_in
from ml.evaluate import evaluate
from ml.simulate import load_sim

OUT = Path("ml/runs/search/results.csv")
SPACE = {
    "hidden": [48, 64, 96, 128],
    "layers": [1, 2, 3],
    "dropout": [0.0, 0.1, 0.2],
    "lr": [5e-4, 1e-3, 2e-3],
    "w_nll": [0.1, 0.3, 1.0],
    "w_safety": [0.25, 0.5, 1.0],
    "w_smooth": [0.3, 1.0, 3.0],
    "sim": ["data/sim/sim.npz", "data/sim/sim_v2.npz"],
}


def sample(rng: random.Random) -> dict:
    return {k: rng.choice(v) for k, v in SPACE.items()}


def run_trial(cfg: dict, cache: dict, seed: int) -> dict:
    T.W_NLL, T.W_SAFETY, T.W_SMOOTH = cfg["w_nll"], cfg["w_safety"], cfg["w_smooth"]
    if cfg["sim"] not in cache:
        sim = load_sim(Path(cfg["sim"]))
        split = make_person_split([s.person_id for s in sim], n_val=len({s.person_id for s in sim}) // 10, n_calib=0, n_test=0)
        cache[cfg["sim"]] = ([T.to_tensors(s) for s in sessions_in(sim, split["train"])],
                             [T.to_tensors(s) for s in sessions_in(sim, split["val"])], split["train"])
    sim_train, sim_val, sim_people = cache[cfg["sim"]]
    real, real_train, real_val, val_sessions, split = cache["real"]

    torch.manual_seed(seed)
    args = {"hidden": cfg["hidden"], "layers": cfg["layers"], "dropout": cfg["dropout"] if cfg["layers"] > 1 else 0.0}
    model = HeatForecaster(**args)
    log = Path("ml/runs/search/trials.csv")
    T.train_stage(model, sim_train, sim_val, lr=cfg["lr"], max_epochs=10, patience=2, batch_size=64, k_times=16,
                  seed=seed, log_path=log, stage="search-pretrain")
    T.train_stage(model, real_train, real_val, lr=cfg["lr"] * 0.3, max_epochs=60, patience=8, batch_size=16, k_times=32,
                  seed=seed, log_path=log, stage="search-finetune")
    pred = ForecasterPredictor([model], set(sim_people) | set(split["train"]), "trial")
    r = evaluate("trial", pred, val_sessions, trained_on=pred.trained_on)
    return {"now_rmse": r.estimation["rmse"], "q20": r.forecast[20]["quantile_loss"],
            "rmse20": r.forecast[20]["rmse_median"], "cov20": r.forecast[20]["coverage_p90"],
            "red_caught": r.alerts["red"]["caught_pct"], "red_fa": r.alerts["red"]["false_alarms_per_hr"],
            "objective": r.forecast[20]["quantile_loss"] + 0.5 * r.estimation["rmse"], "params": sum(p.numel() for p in model.parameters())}


def best(n: int = 5) -> None:
    rows = list(csv.DictReader(OUT.open()))
    rows.sort(key=lambda r: float(r["objective"]))
    for r in rows[:n]:
        print({k: r[k] for k in ("objective", "now_rmse", "rmse20", "q20", "cov20", "red_caught", "config")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--best", action="store_true")
    args = ap.parse_args()
    if args.best:
        return best()
    torch.set_num_threads(6)
    rng = random.Random(args.seed)
    real = load_mendeley()
    split = load_split("mendeley")
    cache = {"real": (real, [T.to_tensors(s) for s in sessions_in(real, split["train"])],
                      [T.to_tensors(s) for s in sessions_in(real, split["val"])], sessions_in(real, split["val"]), split)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    new = not OUT.exists()
    with OUT.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["trial", "objective", "now_rmse", "rmse20", "q20", "cov20", "red_caught", "red_fa", "params", "minutes", "config"])
        for i in range(args.trials):
            cfg = sample(rng)
            t0 = time.time()
            res = run_trial(cfg, cache, seed=i)
            w.writerow([i, f"{res['objective']:.5f}", f"{res['now_rmse']:.4f}", f"{res['rmse20']:.4f}", f"{res['q20']:.5f}",
                        f"{res['cov20']:.3f}", f"{res['red_caught']:.1f}", f"{res['red_fa']:.3f}", res["params"],
                        f"{(time.time() - t0) / 60:.1f}", json.dumps(cfg)])
            f.flush()
            print(f"trial {i}: objective {res['objective']:.4f} (NOW {res['now_rmse']:.3f}, +20 {res['rmse20']:.3f}) "
                  f"{(time.time() - t0) / 60:.1f} min  {cfg}", flush=True)


if __name__ == "__main__":
    main()
