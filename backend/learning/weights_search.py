"""
Find the most productive SAFE workday: search the planner's soft weights (B's PlannerWeights).

What we tune: how the optimizer trades work against heat margin and fairness, task priority
values, storm/rain costs, a bonus for extra shade breaks in heat, and how far under the limit
it plans (budget_margin_c, never below 0). See backend/planner/weights.py.
What we never touch: the heat limit, break rules, lightning / NWS stop-work, the new-hire rule.
Those are in backend/config.py and the rule checker, and PlannerWeights.validate() refuses
anything that would cross into them.

How:
  1. Random search: N random weight settings (plus B's defaults) on a few training days.
  2. Successive halving: the best third goes on to all training days, then the best of those
     gets a local "refine" round (small nudges around the leader). Hand-written, no library.
  3. Constraint (hard): zero rule-checker violations on every day. Constraint (soft): worker-
     minutes above the yellow line no worse than B's defaults on the same days.
     Goal: most tasks finished per shift (then priority-weighted work as the tie-break).
  4. The Pareto front (tasks per shift vs heat minutes vs fairness) is reported, not just one point.
  5. The pick is made on TRAINING days only. B's held-out test set is scored once, at the end,
     next to the defaults and the optimizer-only baseline.

Scoring is B's (backend/planner/benchmark.py score): optimizer with these weights, rule checker,
then B's simulated true shift (workers hotter than planned, a hotter day). We call the same
pieces with a shared heat-model cache, because the weights don't change the heat tables.

No Claude calls: $0. CPU: LEARNING_WORKERS processes x 1 solver thread.

Run:  python -m backend.learning.weights_search                 (search + held-out report)
"""

import argparse
import hashlib
import json
import time

import numpy as np

from backend.learning import settings
from backend.learning.parallel import pmap
from backend.planner.benchmark import score_plans
from backend.planner.heat_model import PHSHeatModel
from backend.planner.optimizer import optimize
from backend.planner.proactive_eval import run_arm
from backend.planner.weights import PlannerWeights

OUT = settings.DATA_DIR / "weights_search.json"
BEST_PATH = settings.HERE / "best_weights.json"
TIME_LIMIT_S = 2.0          # CP-SAT deterministic budget per solve inside the search (B's score uses 5)


class CachedHeatModel(PHSHeatModel):
    """Same heat model, but remembers results. Keyed by the scenario's CONTENT and the plan, so it can't mix days up."""

    def __init__(self):
        super().__init__()
        self._costs, self._sims = {}, {}

    @staticmethod
    def _key(scenario) -> str:
        return hashlib.sha1(json.dumps(scenario.to_dict(), sort_keys=True).encode()).hexdigest()

    def slot_costs(self, scenario):
        k = self._key(scenario)
        if k not in self._costs:
            self._costs[k] = super().slot_costs(scenario)
        return self._costs[k]

    def simulate(self, scenario, plans):
        k = self._key(scenario)
        out, todo = [None] * len(plans), []
        for i, p in enumerate(plans):
            pk = (k, json.dumps(p.assignments, sort_keys=True))
            if pk in self._sims:
                out[i] = self._sims[pk]
            else:
                todo.append((i, pk))
        if todo:
            for (i, pk), trace in zip(todo, super().simulate(scenario, [plans[i] for i, _ in todo])):
                self._sims[pk] = out[i] = trace
        return out


_MODEL = None            # one cache per worker process


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = CachedHeatModel()
    return _MODEL


def evaluate(weights: dict, scenarios: list, time_limit_s: float = TIME_LIMIT_S) -> dict:
    """B's score(), plan-only arm, with the cache. Returns averages plus per-day rows."""
    w = PlannerWeights.from_dict(weights)            # validate(): refuses anything that crosses into safety
    hm = _model()
    rows = []
    for sc in scenarios:
        plan = optimize(sc, "throughput", hm, deterministic=True, weights=w, time_limit_s=time_limit_s).plan
        s = score_plans(sc, [plan], hm)[0]
        sim = run_arm(sc, plan, "plan_only", hm, w)
        rows.append({"scenario": sc.id, "tasks_completed": sim["tasks_completed"], "throughput": sim["throughput"],
                     "minutes_above_yellow": sim["minutes_above_yellow"], "minutes_above_red": sim["minutes_above_red"],
                     "peak_p90_c": s["peak_p90"], "fairness_spread": s["fairness"],
                     "safety_violations": s["n_safety"], "invalid": s["n_invalid"]})
    mean = lambda k: float(np.mean([r[k] for r in rows if r[k] is not None]))
    return {"weights": w.to_dict(), "n": len(rows), "tasks_per_shift": round(mean("tasks_completed"), 3),
            "throughput": round(mean("throughput"), 4), "minutes_above_yellow": round(mean("minutes_above_yellow"), 2),
            "minutes_above_red": round(mean("minutes_above_red"), 2), "peak_p90_c": round(mean("peak_p90_c"), 3),
            "fairness_spread": round(mean("fairness_spread"), 1),
            "safety_violations": int(sum(r["safety_violations"] for r in rows)),
            "invalid": int(sum(r["invalid"] for r in rows)), "rows": rows}


def _job(args):
    weights, scenarios = args
    return evaluate(weights, scenarios)


# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------
def default_weights() -> dict:
    return PlannerWeights().to_dict()


def random_weights(rng) -> dict:
    p1 = int(rng.integers(6, 21))
    p2 = int(rng.integers(2, p1))
    p3 = int(rng.integers(1, p2 + 1))
    return {**default_weights(),
            "throughput": 10, "margin": int(rng.integers(0, 5)), "fairness": int(rng.integers(0, 5)),
            "budget_margin_c": round(float(rng.uniform(0.0, 0.25)), 3),
            "priority_values": {1: p1, 2: p2, 3: p3},
            "storm": int(rng.integers(0, 41)), "rain": int(rng.integers(0, 21)),
            "hot_break_bonus": int(rng.integers(0, 11))}


def nudge(w: dict, rng) -> dict:
    """A small step around a good setting (the refine round)."""
    n = json.loads(json.dumps(w))
    n["priority_values"] = {int(k): v for k, v in n["priority_values"].items()}
    knob = rng.choice(["margin", "fairness", "budget_margin_c", "priority", "storm", "rain", "hot_break_bonus"])
    if knob == "budget_margin_c":
        n[knob] = round(float(np.clip(n[knob] + rng.normal(0, 0.04), 0.0, 0.3)), 3)
    elif knob == "priority":
        p = n["priority_values"]
        p[1] = int(np.clip(p[1] + rng.integers(-3, 4), max(p[2], 2), 25))
        p[2] = int(np.clip(p[2] + rng.integers(-2, 3), p[3], p[1]))
    else:
        hi = {"margin": 6, "fairness": 6, "storm": 50, "rain": 30, "hot_break_bonus": 15}[knob]
        n[knob] = int(np.clip(n[knob] + rng.integers(-3, 4), 0, hi))
    return n


# ---------------------------------------------------------------------------
# Ranking: hard constraint first, then the heat limit vs defaults, then work
# ---------------------------------------------------------------------------
def feasible(r: dict, ref: dict) -> bool:
    return r["safety_violations"] == 0 and r["invalid"] == 0 and r["minutes_above_yellow"] <= ref["minutes_above_yellow"] + 1e-9


def rank_key(r: dict, ref: dict):
    return (feasible(r, ref), r["tasks_per_shift"], r["throughput"], -r["minutes_above_yellow"])


def pareto(results: list[dict]) -> list[dict]:
    """Safe settings that no other safe setting beats on all three: more tasks, fewer heat minutes, fairer."""
    safe = [r for r in results if r["safety_violations"] == 0 and r["invalid"] == 0]
    better = lambda a, b: (a["tasks_per_shift"] >= b["tasks_per_shift"] and a["minutes_above_yellow"] <= b["minutes_above_yellow"]
                           and a["fairness_spread"] <= b["fairness_spread"]) and (
        a["tasks_per_shift"] > b["tasks_per_shift"] or a["minutes_above_yellow"] < b["minutes_above_yellow"]
        or a["fairness_spread"] < b["fairness_spread"])
    return [r for r in safe if not any(better(o, r) for o in safe if o is not r)]


def search(train: list, n_random: int = 24, first_rung: int = 6, keep: int = 8, n_refine: int = 6,
           seed: int = 0, verbose: bool = True) -> dict:
    t0 = time.time()
    rng = np.random.default_rng(seed)
    cands = [default_weights()] + [random_weights(rng) for _ in range(n_random)]
    # Rung 1: everyone on a few training days
    small = train[:first_rung]
    r1 = pmap(_job, [(c, small) for c in cands])
    ref1 = r1[0]
    order = sorted(range(len(cands)), key=lambda i: rank_key(r1[i], ref1), reverse=True)
    if verbose:
        print(f"  rung 1: {len(cands)} settings x {len(small)} days. defaults: {ref1['tasks_per_shift']} tasks, "
              f"{ref1['minutes_above_yellow']} min above yellow. best: {r1[order[0]]['tasks_per_shift']} tasks, "
              f"{r1[order[0]]['minutes_above_yellow']} min", flush=True)
    # Rung 2: the best `keep` (always with the defaults) on every training day
    finalists = [0] + [i for i in order if i != 0][:keep - 1]
    r2 = pmap(_job, [(cands[i], train) for i in finalists])
    ref = r2[0]
    best = max(r2, key=lambda r: rank_key(r, ref))
    if verbose:
        print(f"  rung 2: {len(finalists)} settings x {len(train)} days. defaults: {ref['tasks_per_shift']} tasks, "
              f"{ref['minutes_above_yellow']} min. best: {best['tasks_per_shift']} tasks, {best['minutes_above_yellow']} min", flush=True)
    # Refine: small nudges around the leader, on every training day
    nudges = [nudge(best["weights"], rng) for _ in range(n_refine)]
    r3 = pmap(_job, [(c, train) for c in nudges])
    full = r2 + r3
    best = max(full, key=lambda r: rank_key(r, ref))
    if verbose:
        print(f"  refine: {len(nudges)} nudges. best: {best['tasks_per_shift']} tasks, {best['minutes_above_yellow']} min, "
              f"feasible {feasible(best, ref)}", flush=True)
    strip = lambda r: {k: v for k, v in r.items() if k != "rows"}
    return {"defaults_train": strip(ref), "best_train": strip(best), "best_is_default": best["weights"] == ref["weights"],
            "rung1": [strip(r) for r in r1], "full_train": [strip(r) for r in full],
            "pareto_train": [strip(r) for r in pareto(full)], "n_train_days": len(train),
            "seconds": round(time.time() - t0, 1), "claude_cost_usd": 0.0}


def held_out_report(best_weights: dict, test: list) -> dict:
    """Scored ONCE, after the pick: defaults vs tuned, plus B's optimizer-only baseline (= defaults, plan only)."""
    res = pmap(_job, [(default_weights(), test), (best_weights, test)])
    strip = lambda r: {k: v for k, v in r.items() if k != "rows"}
    return {"defaults": strip(res[0]), "tuned": strip(res[1]), "n_test_days": len(test),
            "note": "B's 12 held-out test scenarios, scored once after the pick. 'defaults' = B's optimizer-only "
                    "baseline (default PlannerWeights, plan only)."}


if __name__ == "__main__":
    from backend.learning.curriculum import bank_scenarios, load_bank
    from backend.planner.generate import load_test_set
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-random", type=int, default=24)
    ap.add_argument("--first-rung", type=int, default=6)
    ap.add_argument("--keep", type=int, default=8)
    ap.add_argument("--refine", type=int, default=6)
    ap.add_argument("--no-test", action="store_true", help="skip the held-out report")
    args = ap.parse_args()
    train = bank_scenarios(load_bank(split="train"))
    print(f"Weight search on {len(train)} training days (bank train split). $0: no Claude calls.")
    res = search(train, args.n_random, args.first_rung, args.keep, args.refine)
    if not args.no_test:
        print("Held-out test set, scored once:")
        res["held_out"] = held_out_report(res["best_train"]["weights"], load_test_set())
        for k in ("defaults", "tuned"):
            r = res["held_out"][k]
            print(f"  {k:8s} {r['tasks_per_shift']} tasks/shift, {r['minutes_above_yellow']} min above yellow, "
                  f"{r['safety_violations']} safety violations, throughput {r['throughput']}")
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    BEST_PATH.write_text(json.dumps({"weights": res["best_train"]["weights"], "train": res["best_train"],
                                     "held_out": res.get("held_out", {}).get("tuned")}, indent=1, default=str))
    print(f"Saved {OUT} and {BEST_PATH} in {res['seconds']:.0f}s (+ held-out)")
