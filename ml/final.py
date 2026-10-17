"""
The final forecaster: a MIXED ensemble of the best different settings from the search.

Different designs make different mistakes, so averaging them is stronger than five
copies of one design (the course's "keep generators diverse" lesson). Each member is
retrained from scratch (new seed) with the real data now carrying the study's heat
conditions (ml/data.py), then saved as one checkpoint the live system can load.

Run:  python -m ml.final --top 5
Then: python -m ml.evaluate --model ml/checkpoints/final.pt:raw     (val people)
"""

import argparse
import csv
import json
import os
from pathlib import Path

import torch

import ml.train as T
from backend.models.forecaster import HeatForecaster
from ml.data import load_mendeley, load_split, make_person_split, sessions_in
from ml.search import OUT
from ml.simulate import load_sim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--name", default="final")
    args = ap.parse_args()
    torch.set_num_threads(os.cpu_count() or 6)
    rows = sorted(csv.DictReader(OUT.open()), key=lambda r: float(r["objective"]))
    configs = [json.loads(r["config"]) for r in rows[:args.top]]

    real = load_mendeley()
    split = load_split("mendeley")
    real_train = [T.to_tensors(s) for s in sessions_in(real, split["train"])]
    real_val = [T.to_tensors(s) for s in sessions_in(real, split["val"])]
    cache, states, args_list, trained_on = {}, [], [], set(split["train"])
    log = T.RUN_DIR / args.name / "log.csv"
    for i, cfg in enumerate(configs):
        print(f"member {i + 1}/{len(configs)}: {cfg}", flush=True)
        if cfg["sim"] not in cache:
            sim = load_sim(Path(cfg["sim"]))
            sp = make_person_split([s.person_id for s in sim], n_val=len({s.person_id for s in sim}) // 10, n_calib=0, n_test=0)
            cache[cfg["sim"]] = ([T.to_tensors(s) for s in sessions_in(sim, sp["train"])],
                                 [T.to_tensors(s) for s in sessions_in(sim, sp["val"])], sp["train"])
        sim_train, sim_val, sim_people = cache[cfg["sim"]]
        trained_on |= set(sim_people)
        T.W_NLL, T.W_SAFETY, T.W_SMOOTH = cfg["w_nll"], cfg["w_safety"], cfg["w_smooth"]
        model_args = {"hidden": cfg["hidden"], "layers": cfg["layers"], "dropout": cfg["dropout"] if cfg["layers"] > 1 else 0.0}
        torch.manual_seed(100 + i)
        model = HeatForecaster(**model_args)
        T.train_stage(model, sim_train, sim_val, lr=cfg["lr"], max_epochs=15, patience=3, batch_size=64, k_times=16,
                      seed=100 + i, log_path=log, stage=f"final-pretrain-{i}")
        T.train_stage(model, real_train, real_val, lr=cfg["lr"] * 0.3, max_epochs=100, patience=10, batch_size=16,
                      k_times=32, seed=100 + i, log_path=log, stage=f"final-finetune-{i}")
        states.append(model.state_dict())
        args_list.append(model_args)
        torch.save({"models": states, "model_args": args_list[0], "model_args_list": args_list,
                    "trained_on": sorted(trained_on), "name": args.name, "configs": configs[:len(states)]},
                   T.CKPT_DIR / f"{args.name}.pt")
        print(f"  saved {len(states)} member(s)", flush=True)


if __name__ == "__main__":
    main()
