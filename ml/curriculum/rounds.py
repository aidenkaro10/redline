"""
Self-improving training rounds for the population forecaster (Loop 4, adversarial curriculum).

One round:
    1. PROPOSE: the proposer attacks the current best model in the simulator (proposer.py).
    2. FILTER:  keep scenarios it solves sometimes but not always (learnability.py).
    3. TRAIN:   simulate fresh shifts for the kept scenarios, add them to the simulated
                training mix, retrain from scratch (pretrain on sim, fine-tune on real),
                using ml/train.py's own functions.
    4. GATE:    score on Mendeley VAL people (ml/evaluate.py, next to the Kalman baselines).
                Keep the new model only if quantile loss improves AND RMSE (now and forecast)
                and red-alert catch rate don't get worse. Otherwise roll back: the new shifts
                are thrown away and the old model stays.
Every round is logged to <root>/ml/runs/curriculum/<name>/rounds.jsonl.

Round 0 trains on the base simulator only: that's the starting model every round must beat.

Run (small model, proves the loop):
    python -m ml.curriculum.rounds --name small --small --rounds 2 --root /path/to/planit-control
Run (full single model, ~15 min per round on 4 threads):
    python -m ml.curriculum.rounds --name full --rounds 3 --root /path/to/planit-control
"""

import argparse
import dataclasses
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
import torch

from backend.models.forecaster import ForecasterPredictor, HeatForecaster
from ml.baselines import BASELINES
from ml.curriculum.learnability import filter_learnable, learnability_reward, summarize
from ml.curriculum.proposer import propose
from ml.curriculum.scenario import Scenario, simulate_scenarios
from ml.data import load_mendeley, load_split, make_person_split, sessions_in
from ml.evaluate import HORIZONS, evaluate
from ml.simulate import load_sim
from ml.train import to_tensors, train_stage

RMSE_TOLERANCE_C = 0.005   # "not worse" allows this much noise in RMSE (°C); quantile loss must truly improve

FULL = {"hidden": 64, "layers": 2, "pretrain_epochs": 15, "finetune_epochs": 100, "sim_limit": 0,
        "population": 32, "generations": 3, "shifts_each": 6}
SMALL = {"hidden": 32, "layers": 2, "pretrain_epochs": 4, "finetune_epochs": 40, "sim_limit": 2000,
         "population": 24, "generations": 2, "shifts_each": 6}


# ---------------------------------------------------------------------------
# Scoring on validation people
# ---------------------------------------------------------------------------
def summary(report) -> dict:
    """The few numbers the gate looks at (plus a couple for the log)."""
    f = report.forecast
    return {
        "now_rmse": report.estimation["rmse"],
        "fc_rmse": float(np.mean([f[h]["rmse_median"] for h in HORIZONS])),
        "fc20_rmse": f[20]["rmse_median"],
        "qloss": float(np.mean([f[h]["quantile_loss"] for h in HORIZONS])),
        "qloss20": f[20]["quantile_loss"],
        "cov_p90_20": f[20]["coverage_p90"],
        "red_caught_pct": report.alerts["red"]["caught_pct"],
        "red_false_per_hr": report.alerts["red"]["false_alarms_per_hr"],
        "yellow_caught_pct": report.alerts["yellow"]["caught_pct"],
    }


def gate(new: dict, best: dict) -> tuple[bool, str]:
    """Keep the new model only if it's better on quantile loss and not worse on RMSE or red catches."""
    reasons = []
    if not new["qloss"] < best["qloss"]:
        reasons.append(f"quantile loss {new['qloss']:.4f} not below {best['qloss']:.4f}")
    if new["now_rmse"] > best["now_rmse"] + RMSE_TOLERANCE_C:
        reasons.append(f"NOW RMSE worse ({new['now_rmse']:.3f} vs {best['now_rmse']:.3f})")
    if new["fc_rmse"] > best["fc_rmse"] + RMSE_TOLERANCE_C:
        reasons.append(f"forecast RMSE worse ({new['fc_rmse']:.3f} vs {best['fc_rmse']:.3f})")
    if new["red_caught_pct"] < best["red_caught_pct"]:
        reasons.append(f"red catch worse ({new['red_caught_pct']:.0f}% vs {best['red_caught_pct']:.0f}%)")
    return (not reasons), ("; ".join(reasons) if reasons else "better quantile loss, RMSE and red catch held")


# ---------------------------------------------------------------------------
# Training (wraps ml/train.py's own functions, no changes there)
# ---------------------------------------------------------------------------
def train_model(sim_train, sim_val, real_train, real_val, cfg: dict, seed: int, log_path: Path) -> HeatForecaster:
    """Pretrain on simulated shifts, then fine-tune on real people. Same settings as ml/train.py main()."""
    torch.manual_seed(seed)
    model = HeatForecaster(hidden=cfg["hidden"], layers=cfg["layers"], dropout=0.1)
    train_stage(model, sim_train, sim_val, lr=1e-3, max_epochs=cfg["pretrain_epochs"], patience=3,
                batch_size=64, k_times=16, seed=seed, log_path=log_path, stage="pretrain")
    train_stage(model, real_train, real_val, lr=3e-4, max_epochs=cfg["finetune_epochs"], patience=10,
                batch_size=16, k_times=32, seed=seed, log_path=log_path, stage="finetune")
    return model


def save_checkpoint(model: HeatForecaster, cfg: dict, trained_on: set[str], name: str, path: Path) -> None:
    """Same format as ml/train.py, so `python -m ml.evaluate --model <path>` works on it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"models": [model.state_dict()],
                "model_args": {"hidden": cfg["hidden"], "layers": cfg["layers"], "dropout": 0.1,
                               "twin_dim": 0, "hr_head": False},
                "trained_on": sorted(trained_on), "name": name, "twins": [{}]}, path)


# ---------------------------------------------------------------------------
# The curriculum mix: which scenarios, how many shifts, which seed (enough to rebuild it exactly)
# ---------------------------------------------------------------------------
def build_mix_sessions(mix: list[dict]) -> list:
    """Rebuild the curriculum shifts from a mix list [{"round", "seed", "shifts_each", "scenarios"}, ...]."""
    out = []
    for part in mix:
        scns = [Scenario(**s) for s in part["scenarios"]]
        for shifts in simulate_scenarios(scns, part["shifts_each"], seed=part["seed"], prefix=f"cur-r{part['round']}"):
            out += shifts
    return out


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def run(args):
    torch.set_num_threads(args.threads)
    cfg = dict(SMALL if args.small else FULL)
    root = Path(args.root)
    out_dir = root / "ml/runs/curriculum" / args.name
    ckpt_dir = root / "ml/checkpoints"
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "rounds.jsonl"
    t_start = time.time()

    def write(entry):
        with log.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    # --- Data that never changes between rounds (built once).
    print("Loading data...")
    sim = load_sim(root / args.sim)
    if cfg["sim_limit"]:
        sim = sim[:cfg["sim_limit"]]
    sim_split = make_person_split([s.person_id for s in sim], n_val=len({s.person_id for s in sim}) // 10,
                                  n_calib=0, n_test=0)
    base_train = [to_tensors(s) for s in sessions_in(sim, sim_split["train"])]
    sim_val = [to_tensors(s) for s in sessions_in(sim, sim_split["val"])]   # fixed: curriculum shifts never go here
    real = load_mendeley(root / "data/raw/mendeley_buono/data.csv")
    split = load_split("mendeley")
    real_train = [to_tensors(s) for s in sessions_in(real, split["train"])]
    real_val = [to_tensors(s) for s in sessions_in(real, split["val"])]
    val_sessions = sessions_in(real, split["val"])
    n_add = args.add_shifts or max(60, len(base_train) // 10)        # new hard shifts per round (~10% of base)
    print(f"base sim: {len(base_train)} train / {len(sim_val)} val shifts; adding ~{n_add} hard shifts per round")

    # --- Kalman baselines on the same val people (for the log and the table).
    baselines = {}
    for bname, make in BASELINES.items():
        b = make()
        b.fit(sessions_in(real, split["train"]))
        baselines[bname] = summary(evaluate(bname, b, val_sessions, trained_on=set(split["train"])))
    write({"event": "baselines", "val": baselines, "cfg": cfg, "args": vars(args)})

    def train_and_score(round_num, mix, tag):
        cur = build_mix_sessions(mix)
        cur_t = [to_tensors(s) for s in cur]
        name = f"cur_{args.name}_r{round_num}{tag}"
        t0 = time.time()
        model = train_model(base_train + cur_t, sim_val, real_train, real_val, cfg, seed=args.seed,
                            log_path=out_dir / f"train_r{round_num}{tag}.csv")
        trained_on = set(sim_split["train"]) | {s.person_id for s in cur} | set(split["train"])
        path = ckpt_dir / f"{name}.pt"
        save_checkpoint(model, cfg, trained_on, name, path)
        pred = ForecasterPredictor([model], trained_on, name)
        val = summary(evaluate(name, pred, val_sessions, trained_on=trained_on))
        return pred, path, val, len(cur), time.time() - t0

    # --- Round 0: base simulator only.
    accepted_mix: list[dict] = []
    print("\n=== Round 0: base mix only ===")
    best_pred, best_path, best_val, _, secs = train_and_score(0, accepted_mix, "")
    shutil.copy(best_path, ckpt_dir / f"cur_{args.name}_best.pt")
    write({"event": "round", "round": 0, "decision": "start", "val": best_val, "curriculum_shifts": 0,
           "train_seconds": secs})
    print(f"round 0 val: {fmt(best_val)}  ({secs:.0f}s)")

    if args.noise_check:
        # Same data, different seed: how much do the numbers move by chance alone?
        print("\n=== Noise check: round 0 again with another seed ===")
        args.seed += 1
        _, _, noise_val, _, secs = train_and_score(0, accepted_mix, "_seed2")
        args.seed -= 1
        write({"event": "noise_check", "val": noise_val, "train_seconds": secs})
        print(f"noise check val: {fmt(noise_val)}")

    for r in range(1, args.rounds + 1):
        print(f"\n=== Round {r} ===")
        t0 = time.time()
        archive = propose(best_pred, generations=cfg["generations"], population=cfg["population"],
                          shifts_each=cfg["shifts_each"], seed=args.seed * 100 + r)
        kept = filter_learnable(archive, max_keep=args.max_keep)
        prop_secs = time.time() - t0
        (out_dir / f"archive_r{r}.json").write_text(json.dumps([a.as_dict() for a in archive]))
        stats = summarize(archive)
        print(f"  filter: {stats}, kept {len(kept)} (after removing near-duplicates)")
        if not kept:
            write({"event": "round", "round": r, "decision": "skip", "reason": "no learnable scenarios",
                   "proposer": stats})
            continue
        part = {"round": r, "seed": 10_000 + r, "shifts_each": max(1, n_add // len(kept)),
                "scenarios": [dataclasses.asdict(k.scenario) for k in kept]}
        pred, path, val, n_cur, secs = train_and_score(r, accepted_mix + [part], "")
        keep, reason = gate(val, best_val)
        print(f"  round {r} val: {fmt(val)}\n  best so far: {fmt(best_val)}\n  -> {'KEEP' if keep else 'ROLL BACK'}: {reason}")
        write({"event": "round", "round": r, "decision": "keep" if keep else "rollback", "reason": reason,
               "val": val, "best_before": best_val, "proposer": stats, "kept_scenarios": len(kept),
               "mean_reward": float(np.mean([learnability_reward(k.solve_rate) for k in kept])),
               "top_scenarios": [k.scenario.short() for k in kept[:5]],
               "curriculum_shifts": n_cur, "proposer_seconds": prop_secs, "train_seconds": secs})
        if keep:
            accepted_mix.append(part)
            best_pred, best_val = pred, val
            shutil.copy(path, ckpt_dir / f"cur_{args.name}_best.pt")
        (out_dir / "mix.json").write_text(json.dumps(accepted_mix, indent=1))

    write({"event": "done", "total_seconds": time.time() - t_start, "best_val": best_val,
           "accepted_rounds": [p["round"] for p in accepted_mix]})
    print(f"\nDone in {(time.time() - t_start) / 60:.0f} min. Accepted rounds: {[p['round'] for p in accepted_mix]}")


def fmt(v: dict) -> str:
    return (f"NOW {v['now_rmse']:.3f} | fc RMSE {v['fc_rmse']:.3f} (+20 {v['fc20_rmse']:.3f}) | "
            f"qloss {v['qloss']:.4f} | p90 cov +20 {v['cov_p90_20']:.2f} | red {v['red_caught_pct']:.0f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="small")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--small", action="store_true", help="small fast model, to prove the loop works")
    ap.add_argument("--root", default=".", help="repo checkout that holds data/ (and gets checkpoints + runs)")
    ap.add_argument("--sim", default="data/sim/sim.npz", help="base simulator file, relative to --root")
    ap.add_argument("--add-shifts", type=int, default=0, help="hard shifts added per round (0 = 10%% of base)")
    ap.add_argument("--max-keep", type=int, default=40, help="max scenarios kept per round")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--noise-check", action="store_true", help="also train round 0 with a 2nd seed")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
