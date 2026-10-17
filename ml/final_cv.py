"""
The final forecaster, built the most data-efficient honest way: cross-fitting.

The 10 TEST people are locked away and scored exactly once at the end. The other 55
real people (train + val + calib) are split into 5 folds of 11:

    member i  = the i-th best setting from the hyperparameter search
                pretrained on the simulator, then fine-tuned on the 44 people NOT in fold i
                (5 of those 44 are held out inside for early stopping)
    fold i    = scored ONLY by member i, which never saw those people

So every real person helps train 4 of the 5 members, and every real person's forecasts
are checked by a member that never saw them ("out-of-fold"). Those out-of-fold misses,
from all 55 people instead of 10, set the conformal calibration (CV+ / cross-conformal,
Barber et al. 2021). Using single-member misses to calibrate the 5-member average is
slightly conservative: the ranges come out a little wide rather than too narrow.

Run:   python -m ml.final_cv
Score: python -m ml.evaluate --on test --model ml/checkpoints/final_cv.pt     (ONCE)
"""

import argparse
import csv
import json
import os
import random
from pathlib import Path

import numpy as np
import torch

import ml.train as T
from backend.models.forecaster import HORIZON, ForecasterPredictor, HeatForecaster
from ml.calibrate import collect_misses, conformal_shift
from ml.data import load_mendeley, load_split, make_person_split, sessions_in
from ml.search import OUT
from ml.simulate import load_sim

N_FOLDS = 5
INNER_EARLY_STOP_PEOPLE = 5
SEED = 2026


def folds_of(people: list[str], n: int, seed: int) -> list[list[str]]:
    people = sorted(people)
    random.Random(seed).shuffle(people)
    return [sorted(people[i::n]) for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="final_cv")
    ap.add_argument("--sim-limit", type=int, default=0, help="smoke test: use only this many sim shifts")
    ap.add_argument("--pretrain-epochs", type=int, default=15)
    ap.add_argument("--finetune-epochs", type=int, default=100)
    args = ap.parse_args()
    torch.set_num_threads(os.cpu_count() or 6)

    rows = sorted(csv.DictReader(OUT.open()), key=lambda r: float(r["objective"]))
    configs = [json.loads(r["config"]) for r in rows[:N_FOLDS]]
    split = load_split("mendeley")
    pool = split["train"] + split["val"] + split["calib"]
    assert not set(pool) & set(split["test"]), "test people must never be in the pool"
    folds = folds_of(pool, N_FOLDS, SEED)
    real = load_mendeley()

    cache, states, args_list, trained_on = {}, [], [], set()
    misses = [[[] for _ in range(3)] for _ in range(HORIZON)]
    now_z = []
    log = T.RUN_DIR / args.name / "log.csv"
    for i, (cfg, fold) in enumerate(zip(configs, folds)):
        print(f"member {i + 1}/{N_FOLDS}: fold of {len(fold)} people held out; {cfg}", flush=True)
        if cfg["sim"] not in cache:
            sim = load_sim(Path(cfg["sim"]))
            if args.sim_limit:
                sim = sim[:args.sim_limit]
            sp = make_person_split([s.person_id for s in sim], n_val=max(1, len({s.person_id for s in sim}) // 10),
                                   n_calib=0, n_test=0)
            cache[cfg["sim"]] = ([T.to_tensors(s) for s in sessions_in(sim, sp["train"])],
                                 [T.to_tensors(s) for s in sessions_in(sim, sp["val"])], sp["train"])
        sim_train, sim_val, sim_people = cache[cfg["sim"]]

        others = [p for p in pool if p not in fold]
        rng = random.Random(SEED + i)
        inner = sorted(rng.sample(others, INNER_EARLY_STOP_PEOPLE))
        fit_people = [p for p in others if p not in inner]
        real_fit = [T.to_tensors(s) for s in sessions_in(real, fit_people)]
        real_inner = [T.to_tensors(s) for s in sessions_in(real, inner)]

        T.W_NLL, T.W_SAFETY, T.W_SMOOTH = cfg["w_nll"], cfg["w_safety"], cfg["w_smooth"]
        model_args = {"hidden": cfg["hidden"], "layers": cfg["layers"], "dropout": cfg["dropout"] if cfg["layers"] > 1 else 0.0}
        torch.manual_seed(SEED + i)
        model = HeatForecaster(**model_args)
        T.train_stage(model, sim_train, sim_val, lr=cfg["lr"], max_epochs=args.pretrain_epochs, patience=3,
                      batch_size=64, k_times=16, seed=SEED + i, log_path=log, stage=f"cv-pretrain-{i}")
        T.train_stage(model, real_fit, real_inner, lr=cfg["lr"] * 0.3, max_epochs=args.finetune_epochs, patience=10,
                      batch_size=16, k_times=32, seed=SEED + i, log_path=log, stage=f"cv-finetune-{i}")

        # Out-of-fold: this member scores only the people it never saw.
        member = ForecasterPredictor([model])
        m, z = collect_misses(member, sessions_in(real, fold))
        for k in range(HORIZON):
            for q in range(3):
                misses[k][q].append(m[k][q])
        now_z.append(z)

        states.append(model.state_dict())
        args_list.append(model_args)
        trained_on |= set(sim_people) | set(others)

    misses = [[np.concatenate(m_q) for m_q in row] for row in misses]
    offsets = np.zeros((HORIZON, 3))
    for k in range(HORIZON):
        offsets[k, 0] = conformal_shift(misses[k][0], 0.1)
        offsets[k, 2] = conformal_shift(misses[k][2], 0.9)
    now_scale = float(np.quantile(np.abs(np.concatenate(now_z)), 0.9) / 1.645)
    cal = {"offsets": offsets.tolist(), "now_scale": now_scale}
    oof_cov = {k: float(np.mean(misses[k - 1][2] <= offsets[k - 1, 2])) for k in (10, 20, 30)}
    raw_cov = {k: float(np.mean(misses[k - 1][2] <= 0)) for k in (10, 20, 30)}

    path = T.CKPT_DIR / f"{args.name}.pt"
    torch.save({"models": states, "model_args": args_list[0], "model_args_list": args_list, "name": args.name,
                "trained_on": sorted(trained_on), "calibration": cal, "calibrated_on": sorted(pool),
                "configs": configs, "folds": folds, "protocol": "cross-fit, CV+ calibration, test locked"}, path)
    print(f"Saved {path}", flush=True)
    print(f"Out-of-fold 90th pct coverage before / after calibration: "
          + "  ".join(f"+{k}m {raw_cov[k]:.2f} -> {oof_cov[k]:.2f}" for k in (10, 20, 30)), flush=True)
    print(f"NOW uncertainty scale x{now_scale:.2f}", flush=True)


if __name__ == "__main__":
    main()
