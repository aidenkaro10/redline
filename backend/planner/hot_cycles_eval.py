"""
Hot-hour work/rest pattern: 20 min work + 10 min shade inside each hot 30-min slot ("cycles")
vs the older rule ("blocks": whole slots, max 2 hot work slots then a 30-min break).

Evidence for cycles: IJERPH 2024 21(3):371 (see config.PLAN_HOT_CYCLE_WORK_MIN).

For each scenario, plan both ways with the deterministic optimizer, check with the rule checker, then
run the simulated true shift (proactive_eval: workers hotter than planned, a hotter day, no loop).

Metrics: worker-minutes of true core temp >= 38.0 / 38.5 °C, planned peak 90th pct, true peak,
tasks completed per shift, priority-weighted work done, safety violations (must be 0).

Run:  python -m backend.planner.hot_cycles_eval --set train   (sanity check, as often as you like)
      python -m backend.planner.hot_cycles_eval --set test    (the held-out comparison: run ONCE)
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from backend.planner.benchmark import score_plans
from backend.planner.generate import load_test_set, train_scenarios
from backend.planner.heat_model import PHSHeatModel
from backend.planner.optimizer import optimize
from backend.planner.proactive_eval import run_arm
from backend.planner.scenario import Scenario

PATTERNS = ("blocks", "cycles")


def evaluate(scenarios: list[Scenario], verbose: bool = True) -> dict:
    model = PHSHeatModel()
    rows = []
    for sc0 in scenarios:
        row = {"scenario": sc0.id, "tags": sc0.tags}
        for pat in PATTERNS:
            sc = Scenario.from_dict(sc0.to_dict())
            sc.hot_pattern = pat
            t0 = time.time()
            r = optimize(sc, "throughput", model, deterministic=True)
            s = score_plans(sc, [r.plan], model)[0]
            sim = run_arm(sc, r.plan, "plan_only", model)
            row[pat] = {"verified": r.verified, "safety_violations": s["n_safety"], "invalid": s["n_invalid"],
                        "planned_peak_p90": s["peak_p90"], "true_peak": sim["true_peak_c"],
                        "minutes_above_yellow": sim["minutes_above_yellow"],
                        "minutes_above_red": sim["minutes_above_red"],
                        "tasks_completed": sim["tasks_completed"], "throughput": sim["throughput"],
                        "cycled_slots": len(sc.cycled_slots()), "solve_rounds": r.iterations,
                        "seconds": round(time.time() - t0, 1)}
        rows.append(row)
        if verbose:
            print(f"  {sc0.id:10s} " + "  ".join(
                f"{p}: {row[p]['minutes_above_yellow']:4d} min>yellow, peak {row[p]['planned_peak_p90']:.2f}, "
                f"tasks {row[p]['tasks_completed']}, work {row[p]['throughput']:.2f}" for p in PATTERNS))
    summary = {}
    for p in PATTERNS:
        get = lambda k: [r[p][k] for r in rows]
        summary[p] = {
            "minutes_above_yellow": int(sum(get("minutes_above_yellow"))),
            "minutes_above_red": int(sum(get("minutes_above_red"))),
            "planned_peak_p90_mean": round(float(np.mean(get("planned_peak_p90"))), 3),
            "planned_peak_p90_max": round(float(np.max(get("planned_peak_p90"))), 3),
            "true_peak_mean": round(float(np.mean(get("true_peak"))), 3),
            "tasks_per_shift": round(float(np.mean(get("tasks_completed"))), 2),
            "throughput": round(float(np.mean(get("throughput"))), 4),
            "safety_violations": int(sum(get("safety_violations"))),
            "verified": int(sum(get("verified"))),
            "scenarios_with_hot_slots": int(sum(c > 0 for c in [r["cycles"]["cycled_slots"] for r in rows])),
        }
    return {"rows": rows, "summary": summary}


def table(res: dict) -> str:
    s, n = res["summary"], len(res["rows"])
    lines = ["| Hot-hour pattern | Worker-min ≥ 38.0 °C | Worker-min ≥ 38.5 °C | Planned peak p90 (mean / max) "
             "| True peak (mean) | Tasks per shift | Work done | Safety violations | Verified |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for p in PATTERNS:
        a = s[p]
        lines.append(f"| {p} | {a['minutes_above_yellow']} | {a['minutes_above_red']} "
                     f"| {a['planned_peak_p90_mean']:.2f} / {a['planned_peak_p90_max']:.2f} | {a['true_peak_mean']:.2f} "
                     f"| {a['tasks_per_shift']:.2f} | {a['throughput']:.1%} | {a['safety_violations']} | {a['verified']}/{n} |")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=("train", "test"), default="train")
    ap.add_argument("--n", type=int, default=12, help="how many training scenarios")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    scen = load_test_set() if args.set == "test" else train_scenarios(args.n)
    t0 = time.time()
    res = evaluate(scen)
    out = args.out or Path(f"data/planner/hot_cycles_{args.set}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1))
    print()
    print(table(res))
    print(f"\n{args.set} set, {len(scen)} scenarios, simulation. Saved {out} in {time.time() - t0:.0f}s")
