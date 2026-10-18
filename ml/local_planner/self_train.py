"""
Self-training for the local planner (expert iteration / ReST / STaR), $0: no Claude calls.

One round:
  1. SAMPLE  K schedules per TRAINING day from the current adapter (B's llm_only prompt, temp 0.8).
             A different random subset of days each round. Never test days (same leakage guard).
  2. GRADE   each sample with B's rule checker + simulated shift (score_plans), in 3 processes.
  3. KEEP    a sample only if it passes EVERY rule AND does real work: at least the optimizer-only
             plan's work on that day minus WORK_TOL. Then only the best kept sample per day (by B's
             plan score). "Everyone rests" can never be kept: it does far less work than the optimizer.
  4. RETRAIN starting from the current adapter, on the kept samples + a replay of Claude's examples
             (all of Claude's plan examples and a slice of its ideas/explain ones), so it can't drift.
  5. CHECK   on the validation days only (never test): valid rate, pass rate, "good pass" rate
             (passes AND does real work), work done when passing. The round is kept only if its
             good-pass rate beats the adapter it started from.

The original adapter (ml/checkpoints/local_planner/adapter) is never overwritten: rounds save to
adapter_st1, adapter_st2, ...

Run:  python -m ml.local_planner.self_train --check-only            (validation check of the current adapter)
      python -m ml.local_planner.self_train --round 1                (one full round from the current adapter)
      python -m ml.local_planner.self_train --round 2 --start adapter_st1
Out:  data/local_planner/self_train/round<N>/  (samples, grades, dataset, val check)
"""

import argparse
import json
import random
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from ml.local_planner.common import BASE_MODEL_DIR, CKPT_DIR, DATA_DIR, out_dir

from backend.planner.baselines import LLM_SYSTEM, llm_prompt
from backend.planner.generate import assert_not_test
from backend.planner.scenario import Scenario
from ml.local_planner.dataset import SFT_DIR, compact
from ml.local_planner.evaluate import parse_plan
from ml.local_planner.scenarios import is_safe_to_learn_from
from ml.local_planner.teacher import RUNS_DIR

ST_DIR = DATA_DIR / "self_train"
K = 8                    # samples per training day
DAYS_PER_ROUND = 100
VAL_K = 4                # samples per validation day for the check
TEMPERATURE = 0.8
WORK_TOL = 0.05          # a kept sample may do at most 5 points less work than the optimizer-only plan
GRADE_WORKERS = 3        # CPU processes for the rule checker (session F also uses the CPU)
REPLAY_IDEAS = 100       # how many of Claude's ideas / explain examples to replay each round
REPLAY_EXPLAIN = 100
LR = 5e-5


# ---------------------------------------------------------------------------
# Days
# ---------------------------------------------------------------------------
def load_days(split: str) -> list[dict]:
    """Teacher runs (scenario + the optimizer-only plan's work) for training or validation days."""
    days = []
    for path in sorted(RUNS_DIR.glob("*.json")):
        run = json.loads(path.read_text())
        if run["split"] != split:
            continue
        sc = Scenario.from_dict(run["scenario"])
        assert_not_test(sc)
        if not is_safe_to_learn_from(sc):          # content guard: never a copy of a held-out day
            continue
        opt = next((c for c in run["candidates"] if c["name"] == "optimizer:throughput" and c["ok"]), None)
        if opt is None:
            continue
        days.append({"scenario": run["scenario"], "opt_throughput": opt["score"]["throughput"]})
    return days


# ---------------------------------------------------------------------------
# 1. Sample (GPU)
# ---------------------------------------------------------------------------
def sample_and_grade(adapter_dir: Path | None, days: list[dict], k: int, temperature: float) -> list[dict]:
    """Sample on the GPU; each finished day goes straight to a grading process on the CPU."""
    with ProcessPoolExecutor(max_workers=GRADE_WORKERS) as ex:
        futures = [ex.submit(grade_day, item) for item in sample(adapter_dir, days, k, temperature)]
        return [f.result() for f in futures]


def sample(adapter_dir: Path | None, days: list[dict], k: int, temperature: float):
    """Yields one day at a time: the day plus its k sampled answers."""
    from mlx_lm import batch_generate
    from mlx_lm.sample_utils import make_sampler
    from ml.local_planner.client import load_model
    (model, tok), adapter = load_model(BASE_MODEL_DIR, adapter_dir)
    sampler = make_sampler(temp=temperature, top_p=0.95)
    t0 = time.time()
    for i, day in enumerate(days):
        sc = Scenario.from_dict(day["scenario"])
        msgs = [{"role": "system", "content": LLM_SYSTEM}, {"role": "user", "content": llm_prompt(sc)}]
        ids = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, enable_thinking=False,
                                      return_dict=False)
        r = batch_generate(model, tok, [ids] * k, max_tokens=900, sampler=sampler)
        yield {**day, "texts": list(r.texts)}
        if (i + 1) % 10 == 0:
            print(f"    sampled {i + 1}/{len(days)} days ({time.time() - t0:.0f}s)", flush=True)


# ---------------------------------------------------------------------------
# 2. Grade (CPU, separate processes)
# ---------------------------------------------------------------------------
def grade_day(item: dict) -> dict:
    """Rule checker + simulated shift for every sample of one day. Top-level so processes can run it."""
    from backend.planner.archon import AgentSettings, score
    from backend.planner.benchmark import score_plans
    from backend.planner.rules import check_plans
    sc = Scenario.from_dict(item["scenario"])
    plans = [parse_plan(t, "local:self") for t in item["texts"]]
    scores = score_plans(sc, plans)
    ok_plans = [p for p, s in zip(plans, scores) if s["success"]]
    checks = iter(check_plans(sc, ok_plans) if ok_plans else [])
    graded = []
    for p, s in zip(plans, scores):
        rank = score(sc, p, next(checks), AgentSettings())["rank"] if s["success"] else None
        graded.append({"success": s["success"], "valid": s["valid"], "n_safety": s["n_safety"],
                       "rules": s["rules"], "throughput": s["throughput"], "peak_p90": s["peak_p90"],
                       "rank": rank, "assignments": p.assignments, "notes": p.notes})
    return {"scenario_id": sc.id, "opt_throughput": item["opt_throughput"], "samples": graded}


def is_good(s: dict, opt_throughput: float, tol: float = WORK_TOL) -> bool:
    """Passes every rule AND does real work (close to the optimizer-only plan). Resting everyone never counts."""
    return bool(s["success"]) and s["throughput"] >= opt_throughput - tol and s["throughput"] > 0


def keep_best(graded: list[dict], tol: float = WORK_TOL) -> list[dict]:
    """At most one sample per day: the good one with the highest plan score."""
    kept = []
    for g in graded:
        good = [s for s in g["samples"] if is_good(s, g["opt_throughput"], tol)]
        if good:
            kept.append({"scenario_id": g["scenario_id"], **max(good, key=lambda s: s["rank"])})
    return kept


def stats(graded: list[dict], tol: float = WORK_TOL) -> dict:
    s = [x for g in graded for x in g["samples"]]
    passed = [x for x in s if x["success"]]
    good = [x for g in graded for x in g["samples"] if is_good(x, g["opt_throughput"], tol)]
    return {"days": len(graded), "samples": len(s),
            "valid": float(np.mean([x["valid"] for x in s])), "pass": float(np.mean([x["success"] for x in s])),
            "good_pass": len(good) / len(s),
            "work_when_pass": float(np.mean([x["throughput"] for x in passed])) if passed else None,
            "work_all": float(np.mean([x["throughput"] for x in s])),
            "days_with_good": sum(any(is_good(x, g["opt_throughput"], tol) for x in g["samples"]) for g in graded),
            "safety_violations_per_sample": float(np.mean([x["n_safety"] for x in s]))}


# ---------------------------------------------------------------------------
# 3-4. Dataset + retrain
# ---------------------------------------------------------------------------
def build_round_dataset(kept: list[dict], days: dict, round_dir: Path, seed: int) -> dict:
    """Kept self-samples + replay of Claude's examples (from the original training set)."""
    rng = random.Random(seed)
    claude = [json.loads(l) for l in (SFT_DIR / "train.jsonl").read_text().splitlines()]
    plan = [e for e in claude if e["messages"][0]["content"] == LLM_SYSTEM]
    ideas = [e for e in claude if e not in plan and e["messages"][0]["role"] == "system"]
    explain = [e for e in claude if e not in plan and e not in ideas]
    replay = plan + rng.sample(ideas, min(REPLAY_IDEAS, len(ideas))) + rng.sample(explain, min(REPLAY_EXPLAIN, len(explain)))
    mine = []
    for k in kept:
        sc = Scenario.from_dict(days[k["scenario_id"]]["scenario"])
        assert_not_test(sc)
        answer = {"assignments": k["assignments"], "notes": k["notes"]}
        mine.append({"messages": [{"role": "system", "content": LLM_SYSTEM},
                                  {"role": "user", "content": llm_prompt(sc)},
                                  {"role": "assistant", "content": compact(answer)}]})
    rows = replay + mine
    rng.shuffle(rows)
    sft = out_dir(round_dir / "sft")
    (sft / "train.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (sft / "valid.jsonl").write_text((SFT_DIR / "valid.jsonl").read_text())   # same valid set as before
    return {"self_samples": len(mine), "claude_replay": len(replay), "total": len(rows)}


def retrain(start_adapter: Path, new_adapter: Path, data_dir: Path, n_examples: int) -> None:
    """One epoch, starting from the current adapter (same LoRA shape), lower learning rate."""
    from ml.local_planner.train import SETTINGS
    updates = max(1, n_examples // SETTINGS["grad_accumulation_steps"])
    cfg = {**SETTINGS, "model": str(BASE_MODEL_DIR), "train": True, "data": str(data_dir),
           "adapter_path": str(out_dir(new_adapter)), "iters": n_examples, "learning_rate": LR,
           "resume_adapter_file": str(start_adapter / "adapters.safetensors"),
           "max_seq_length": 4096, "steps_per_eval": 10_000, "save_every": 10_000,
           "lr_schedule": {"name": "cosine_decay", "warmup": 5, "arguments": [LR, updates, LR / 10]}}
    cfg_path = data_dir.parent / "lora_config.yaml"
    cfg_path.write_text(json.dumps(cfg, indent=1))
    subprocess.run([sys.executable, "-m", "mlx_lm", "lora", "-c", str(cfg_path)], check=True)


# ---------------------------------------------------------------------------
# 5. Validation check
# ---------------------------------------------------------------------------
def val_check(adapter_dir: Path, out_path: Path) -> dict:
    days = load_days("val")
    graded = sample_and_grade(adapter_dir, days, VAL_K, 0.7)
    s = stats(graded)
    out_path.write_text(json.dumps({"adapter": str(adapter_dir), "stats": s, "graded": graded}))
    print(f"  VAL {adapter_dir.name}: {json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()})}",
          flush=True)
    return s


def run_round(r: int, start: Path) -> None:
    round_dir = out_dir(ST_DIR / f"round{r}")
    t0 = time.time()
    train_days = load_days("train")
    rng = random.Random(1000 + r)
    days = rng.sample(train_days, min(DAYS_PER_ROUND, len(train_days)))
    print(f"Round {r}: start adapter {start.name}, {len(days)} training days x {K} samples", flush=True)
    graded = sample_and_grade(start, days, K, TEMPERATURE)
    (round_dir / "graded.json").write_text(json.dumps(graded))
    st = stats(graded)
    print(f"  TRAIN-DAY samples from {start.name}: {json.dumps(st)}  ({time.time() - t0:.0f}s)", flush=True)
    kept = keep_best(graded)
    info = build_round_dataset(kept, {d["scenario"]["id"]: d for d in days}, round_dir, seed=r)
    print(f"  kept {len(kept)} days; dataset {info}", flush=True)
    new = CKPT_DIR / f"adapter_st{r}"
    retrain(start, new, round_dir / "sft", info["total"])
    print(f"  retrained -> {new.name} ({time.time() - t0:.0f}s)", flush=True)
    v = val_check(new, round_dir / "val.json")
    (round_dir / "summary.json").write_text(json.dumps({"round": r, "start": str(start), "train_day_stats": st,
                                                        "kept": len(kept), "dataset": info, "val": v,
                                                        "seconds": time.time() - t0}, indent=1))
    print(f"Round {r} done in {(time.time() - t0) / 60:.0f} min", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=0)
    ap.add_argument("--start", default="adapter", help="adapter folder name under ml/checkpoints/local_planner")
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    start = CKPT_DIR / args.start
    if args.check_only:
        val_check(start, out_dir(ST_DIR) / f"val_{args.start}.json")
    else:
        run_round(args.round, start)


if __name__ == "__main__":
    main()
