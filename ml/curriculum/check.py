"""
Did the curriculum fix what it was aimed at? (Simulator check, NOT a gate.)

The gate only looks at real Mendeley val people, who are 2-hour lab sessions with heart
rate only. Most of what the proposer finds (pulse-sensor artifacts, watch dying,
storms, coveralls) never shows up there. So this script also scores models on:

    hard:     the scenarios one round's proposer found hard (solve rate < 1), played
              again with a NEW random seed (fresh shifts nobody trained on)
    everyday: ordinary shifts (default knobs, small nudges), to catch the model
              getting worse at normal days

Run:
    python -m ml.curriculum.check --archive <root>/ml/runs/curriculum/small/archive_r1.json \
        --model <root>/ml/checkpoints/cur_small_r0.pt --model <root>/ml/checkpoints/cur_small_best.pt
"""

import argparse
import json

import numpy as np
import torch

from backend.models.forecaster import ForecasterPredictor
from ml.curriculum.proposer import evaluate_scenarios
from ml.curriculum.scenario import Scenario, mutate

CHECK_SEED = 987_654      # never used for training shifts


def scenario_sets(archive_path: str, n_everyday: int = 40) -> dict[str, list[Scenario]]:
    archive = json.loads(open(archive_path).read())
    hard = [Scenario(**a["scenario"]) for a in archive if a["solve_rate"] < 1.0]
    rng = np.random.default_rng(CHECK_SEED)
    everyday = [mutate(Scenario(), rng, scale=0.05) for _ in range(n_everyday)]
    return {"hard": hard, "everyday": everyday}


def score(model_path: str, sets: dict, shifts_each: int = 6) -> dict:
    pred = ForecasterPredictor.load(model_path, calibrated=False)
    out = {}
    for name, scns in sets.items():
        res = evaluate_scenarios(pred, scns, shifts_each, seed=CHECK_SEED)
        shifts = [s for r in res for s in r.shifts]
        out[name] = {"scenarios": len(scns), "solve_rate": float(np.mean([s.solved for s in shifts])),
                     "rmse20": float(np.mean([s.rmse_median for s in shifts])),
                     "cov_p90_20": float(np.mean([s.coverage_p90 for s in shifts]))}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    torch.set_num_threads(args.threads)
    sets = scenario_sets(args.archive)
    print(f"{'model':<40}{'set':<10}{'solved':>8}{'RMSE+20':>9}{'cov p90':>9}")
    for m in args.model:
        for name, v in score(m, sets).items():
            print(f"{m.split('/')[-1]:<40}{name:<10}{v['solve_rate']:>8.0%}{v['rmse20']:>9.3f}{v['cov_p90_20']:>9.2f}")


if __name__ == "__main__":
    main()
