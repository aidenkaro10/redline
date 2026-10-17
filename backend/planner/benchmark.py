"""
The scenario benchmark: one command scores every planner on the held-out test scenarios.

Metrics per plan (all from the code rule checker + the full heat model):
  violations     safety violations (must be 0) and validity errors
  peak_p90       highest 90th pct core temp of anyone on the crew, °C
  throughput     priority-weighted share of the work done (0..1)
  fairness       spread of heat exposure across the crew: std of each worker's
                 degree-minutes above 37 °C (median). Lower = fairer.
  latency, cost  seconds and US dollars per plan
  reliability    each planner runs each scenario 5 times. pass@1 = share of runs that
                 succeed; pass^5 = share of scenarios where ALL 5 runs succeed
                 (METR's lesson: average isn't enough). Success = a valid plan with
                 zero violations of any kind.
  failure modes  count of violations by rule (heat, breaks, lightning, ...)

Run:  python -m backend.planner.benchmark                       (all baselines, test set)
      python -m backend.planner.benchmark --planners fixed,optimizer --runs 5
Results go to data/planner/benchmark.json and print as a table.
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np

from backend.planner.baselines import BASELINES, DETERMINISTIC, WEIGHTED
from backend.planner.generate import load_test_set
from backend.planner.heat_model import HeatModel, default_heat_model
from backend.planner.llm import llm_available
from backend.planner.optimizer import PRIORITY_WEIGHT
from backend.planner.rules import check_plans
from backend.planner.scenario import Plan, Scenario

OUT_PATH = Path("data/planner/benchmark.json")
DOSE_BASE_C = 37.0


def throughput(scenario: Scenario, plan: Plan) -> float:
    done = plan.units_done(scenario)
    total = sum(PRIORITY_WEIGHT.get(t.priority, 1) * t.units for t in scenario.tasks)
    got = sum(PRIORITY_WEIGHT.get(t.priority, 1) * done[t.id] for t in scenario.tasks)
    return got / total if total else 1.0


def score_plans(scenario: Scenario, plans: list[Plan | None], heat_model: HeatModel | None = None) -> list[dict]:
    """Score many plans on one scenario. A None plan (planner gave nothing usable) is a failure."""
    real = [p for p in plans if p is not None]
    checks = iter(check_plans(scenario, real, heat_model) if real else [])
    out = []
    for p in plans:
        if p is None:
            out.append({"success": False, "valid": False, "n_safety": 0, "n_invalid": 1, "rules": {"no_plan": 1},
                        "peak_p90": None, "throughput": 0.0, "fairness": None})
            continue
        c = next(checks)
        n_safety = c.n_safety
        n_invalid = len(c.violations) - n_safety
        on_site = [w for w in scenario.crew if w.available != []]
        if c.trace is not None:
            doses = [float(np.clip(c.trace[w.id][:, 1] - DOSE_BASE_C, 0, None).sum()) for w in on_site]
            fairness = float(np.std(doses)) if doses else 0.0
            peak = max(c.peak_p90[w.id] for w in on_site) if on_site else None
        else:
            fairness, peak = None, None
        out.append({"success": c.ok, "valid": n_invalid == 0, "n_safety": n_safety, "n_invalid": n_invalid,
                    "rules": dict(Counter(v.rule for v in c.violations)), "peak_p90": peak,
                    "throughput": throughput(scenario, p) if n_invalid == 0 else 0.0, "fairness": fairness})
    return out


def run_benchmark(planners: list[str], scenarios: list[Scenario], runs: int = 5,
                  heat_model: HeatModel | None = None, verbose: bool = True, weights=None) -> dict:
    """weights: PlannerWeights for the optimizer and agent planners (None = defaults)."""
    heat_model = heat_model or default_heat_model()
    results = {}
    for name in planners:
        fn = BASELINES[name]
        deterministic = name in DETERMINISTIC or (name.startswith("agent") and not llm_available())
        rows, skipped = [], None
        for sc in scenarios:
            plans, infos = [], []
            for run in range(1 if deterministic else runs):
                plan, info = fn(sc, run, weights=weights) if weights is not None and name in WEIGHTED else fn(sc, run)
                if plan is None and str(info.get("notes", "")).startswith("skipped"):
                    skipped = info["notes"]
                    break
                plans.append(plan)
                infos.append(info)
            if skipped:
                break
            scores = score_plans(sc, plans, heat_model)
            if deterministic:
                scores, infos = scores * runs, infos * runs
            for run, (s, i) in enumerate(zip(scores, infos)):
                rows.append({"scenario": sc.id, "tags": sc.tags, "run": run, **s,
                             "latency_s": i["latency_s"], "cost_usd": i["cost_usd"]})
            if verbose:
                ok = sum(s["success"] for s in scores)
                print(f"  {name:12s} {sc.id:10s} {ok}/{len(scores)} ok, "
                      f"safety violations {sum(s['n_safety'] for s in scores)}, "
                      f"throughput {np.mean([s['throughput'] for s in scores]):.2f}")
        results[name] = {"skipped": skipped} if skipped else {"rows": rows, "summary": summarize(rows)}
    return results


def summarize(rows: list[dict]) -> dict:
    by_sc = {}
    for r in rows:
        by_sc.setdefault(r["scenario"], []).append(r["success"])
    peaks = [r["peak_p90"] for r in rows if r["peak_p90"] is not None]
    fair = [r["fairness"] for r in rows if r["fairness"] is not None]
    modes = Counter()
    for r in rows:
        modes.update(r["rules"])
    return {
        "n_scenarios": len(by_sc), "n_runs": len(rows),
        "pass_at_1": float(np.mean([r["success"] for r in rows])),
        "pass_all_runs": float(np.mean([all(v) for v in by_sc.values()])),
        "safety_violations_total": int(sum(r["n_safety"] for r in rows)),
        "runs_with_safety_violations": float(np.mean([r["n_safety"] > 0 for r in rows])),
        "invalid_runs": float(np.mean([not r["valid"] for r in rows])),
        "peak_p90_mean": float(np.mean(peaks)) if peaks else None,
        "peak_p90_max": float(np.max(peaks)) if peaks else None,
        "throughput_mean": float(np.mean([r["throughput"] for r in rows])),
        "fairness_std_mean": float(np.mean(fair)) if fair else None,
        "latency_s_mean": float(np.mean([r["latency_s"] for r in rows])),
        "cost_usd_total": float(sum(r["cost_usd"] for r in rows)),
        "failure_modes": dict(modes),
    }


def table(results: dict) -> str:
    """Markdown table for docs and slides."""
    lines = ["| Planner | pass@1 | pass^5 | Runs with safety violations | Safety violations per run | Peak p90 (°C) | Throughput | Heat spread | Latency (s) | Cost ($) | Failure modes |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, r in results.items():
        if "skipped" in r:
            lines.append(f"| {name} | {r['skipped']} |  |  |  |  |  |  |  |  |  |")
            continue
        s = r["summary"]
        fmt = lambda v, f: "-" if v is None else format(v, f)
        modes = ", ".join(f"{k} {v}" for k, v in sorted(s["failure_modes"].items(), key=lambda kv: -kv[1])) or "none"
        lines.append(f"| {name} | {s['pass_at_1']:.2f} | {s['pass_all_runs']:.2f} | {s['runs_with_safety_violations']:.0%} "
                     f"| {s['safety_violations_total'] / s['n_runs']:.1f} "
                     f"| {fmt(s['peak_p90_max'], '.2f')} | {s['throughput_mean']:.2f} | {fmt(s['fairness_std_mean'], '.0f')} "
                     f"| {s['latency_s_mean']:.1f} | {s['cost_usd_total']:.2f} | {modes} |")
    return "\n".join(lines)



# ---------------------------------------------------------------------------
# score(weights, scenarios): one call for session F's weight search
# ---------------------------------------------------------------------------
def score(weights=None, scenarios: list[Scenario] | None = None, act_on_forecasts: bool = False,
          heat_model: HeatModel | None = None) -> dict:
    """
    Plan every scenario with the optimizer under these PlannerWeights (deterministic solver), check it,
    then run the simulated true shift (proactive_eval: workers hotter than planned, a hotter day).
    act_on_forecasts=True also runs the act-on-forecast loop (safer-only changes) during the shift.

    Returns averages over scenarios:
      tasks_completed_per_shift   tasks fully finished
      throughput                  priority-weighted share of work done (0-1)
      minutes_above_yellow        worker-minutes of TRUE core temp >= 38.0 °C per shift (simulation)
      minutes_above_red           same at 38.5 °C
      peak_p90_c                  highest planned 90th pct core temp
      fairness_spread             std of planned heat dose across the crew (lower = fairer)
      safety_violations           total, must be 0 (any > 0 = these weights are unusable)
      invalid                     plans the rule checker rejected for validity
    Search on generate.train_scenarios(n); report final numbers on load_test_set() only once.
    """
    from backend.planner.optimizer import optimize
    from backend.planner.proactive_eval import run_arm
    from backend.planner.weights import PlannerWeights

    weights = (weights or PlannerWeights()).validate()
    heat_model = heat_model or default_heat_model()
    rows = []
    for sc in scenarios or []:
        plan = optimize(sc, "throughput", heat_model, deterministic=True, weights=weights).plan
        s = score_plans(sc, [plan], heat_model)[0]
        sim = run_arm(sc, plan, "auto" if act_on_forecasts else "plan_only", heat_model, weights)
        rows.append({"tasks_completed": sim["tasks_completed"],
                     "throughput": sim["throughput"], "minutes_above_yellow": sim["minutes_above_yellow"],
                     "minutes_above_red": sim["minutes_above_red"], "peak_p90_c": s["peak_p90"],
                     "fairness_spread": s["fairness"], "safety_violations": s["n_safety"],
                     "invalid": s["n_invalid"], "changes": sim["changes"]})
    if not rows:
        return {"n_scenarios": 0}
    mean = lambda k: float(np.mean([r[k] for r in rows if r[k] is not None])) if any(r[k] is not None for r in rows) else None
    return {"n_scenarios": len(rows), "weights": weights.to_dict(), "act_on_forecasts": act_on_forecasts,
            "tasks_completed_per_shift": mean("tasks_completed"), "throughput": mean("throughput"),
            "minutes_above_yellow": mean("minutes_above_yellow"), "minutes_above_red": mean("minutes_above_red"),
            "peak_p90_c": mean("peak_p90_c"), "fairness_spread": mean("fairness_spread"),
            "safety_violations": int(sum(r["safety_violations"] for r in rows)),
            "invalid": int(sum(r["invalid"] for r in rows)), "changes": int(sum(r["changes"] for r in rows)),
            "per_scenario": rows}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--planners", default=",".join(BASELINES))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    t0 = time.time()
    scenarios = load_test_set()
    print(f"Benchmark: {len(scenarios)} held-out test scenarios, {args.runs} runs each. "
          f"LLM: {'on' if llm_available() else 'off (no ANTHROPIC_API_KEY)'}")
    res = run_benchmark(args.planners.split(","), scenarios, args.runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1))
    print()
    print(table(res))
    print(f"\nSaved {args.out} in {time.time() - t0:.0f}s")
