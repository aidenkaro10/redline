"""
Does acting on forecasts help? A simulated shift, three ways, on the 12 held-out test scenarios.

Set-up (simulation, said so in every result):
  - The PLAN is made the normal way: optimizer + rule checker, on the planning heat model.
  - The TRUE crew differs from the plan: each worker runs hotter or cooler than the model by a
    hidden amount (mean +0.15 °C, sd 0.2, seeded), and the day is hotter than forecast
    (+0.5 °C air temp on average, sd 0.8). Truth = the same PHS engine with those hidden numbers.
  - The FORECASTER is a stand-in: every 15 min it reports each worker's true core temp 20 min
    ahead plus the forecaster's typical 90th-pct margin (+0.13 °C). Real forecasts are noisier,
    so treat the numbers as "if the forecaster is about this good".

Arms:
  plan_only       the morning plan, no changes
  auto            the proactive loop, only safer-only changes (no lost work, nothing later)
  auto+approve    the loop, and the crew lead approves every proposal too

Metrics: worker-minutes at/above the yellow line (38.0 °C true core temp) and the red line (38.5),
priority-weighted work done, and how many changes were made.

Run:  python -m backend.planner.proactive_eval            (writes data/planner/proactive_eval.json)
"""

import argparse
import json
import time
import zlib
from pathlib import Path

import numpy as np

from backend import config
from backend.planner.generate import load_test_set
from backend.planner.heat_model import PHSHeatModel
from backend.planner.optimizer import optimize
from backend.planner.proactive import propose, weighted_done
from backend.planner.scenario import Scenario
from backend.planner.weights import PlannerWeights

OUT = Path("data/planner/proactive_eval.json")
TRUE_OFFSET_MEAN, TRUE_OFFSET_SD = 0.15, 0.2
WEATHER_ERR_MEAN, WEATHER_ERR_SD = 0.5, 0.8
FORECAST_MARGIN_C = 0.13
CHECK_EVERY_MIN = 15
ARMS = ("plan_only", "auto", "auto+approve")


def truth_scenario(sc: Scenario) -> Scenario:
    """The real crew and weather the plan didn't know about (seeded by scenario id)."""
    rng = np.random.default_rng(zlib.crc32(f"truth|{sc.id}".encode()))
    t = Scenario.from_dict(sc.to_dict())
    t.id = sc.id + "|truth"
    err = rng.normal(WEATHER_ERR_MEAN, WEATHER_ERR_SD)
    for w in t.weather:
        w.tdb += err
        w.wbgt += 0.7 * err
    for w in t.crew:
        w.core_offset_c = float(rng.normal(TRUE_OFFSET_MEAN, TRUE_OFFSET_SD))
        w.core_sd_c = 1e-3                 # one real person, not a spread
    return t


def true_core(truth: Scenario, plan, model) -> dict:
    """True core temp per worker per minute (median of the weather/work ensemble)."""
    return {wid: tr[:, 1] for wid, tr in model.simulate(truth, [plan])[0].items()}


def run_arm(sc: Scenario, plan0, arm: str, model, weights: PlannerWeights | None = None) -> dict:
    w8 = (weights or PlannerWeights()).validate()
    truth_sc = truth_scenario(sc)
    plan = plan0
    core = true_core(truth_sc, plan, model)
    n_min = sc.n_slots * sc.slot_min
    last, count, changes = {}, {}, []
    if arm != "plan_only":
        for minute in range(0, n_min, CHECK_EVERY_MIN):
            s = minute // sc.slot_min + (1 if minute % sc.slot_min >= 20 else 0)
            if s >= sc.n_slots:
                break
            for w in sc.crew:
                if not w.is_available(min(s, sc.n_slots - 1)):
                    continue
                ahead = min(n_min - 1, minute + w8.early_horizon_min)
                forecast = core[w.id][ahead] + FORECAST_MARGIN_C
                if forecast < w8.early_line_c:
                    continue
                if minute - last.get(w.id, -1e9) < config.PLAN_PROACTIVE_COOLDOWN_MIN \
                        or count.get(w.id, 0) >= config.PLAN_PROACTIVE_MAX_PER_SHIFT:
                    continue
                target = s if plan.assignments[w.id][s] != "rest" or s + 1 >= sc.n_slots else s + 1
                now = {c.id: float(core[c.id][minute]) for c in sc.crew}
                live = {c.id: float(core[c.id][ahead]) + FORECAST_MARGIN_C for c in sc.crew}
                options = propose(sc, plan, w.id, target, now, model, forecasts=live, weights=w8)
                pick = next((c for c in options if c.auto), None)
                if pick is None and arm == "auto+approve" and options:
                    pick = options[0]
                last[w.id], count[w.id] = minute, count.get(w.id, 0) + 1   # a proposal counts even if not taken
                if pick is None:
                    continue
                plan = pick.plan
                core = true_core(truth_sc, plan, model)
                changes.append({"minute": minute, "worker": w.id, "kind": pick.kind, "auto": pick.auto,
                                "throughput_delta": pick.throughput_delta})
    stack = np.stack([core[w.id] for w in sc.crew if w.available != []])
    return {
        "minutes_above_yellow": int((stack >= config.HEAT_WARNING_C).sum()),
        "minutes_above_red": int((stack >= config.HEAT_DANGER_C).sum()),
        "true_peak_c": round(float(stack.max()), 3),
        "throughput": round(weighted_done(sc, plan), 4),
        "tasks_completed": sum(n >= sc.task(t).units for t, n in plan.units_done(sc).items()),
        "changes": len(changes), "auto_changes": sum(c["auto"] for c in changes),
        "change_log": changes,
    }


def evaluate(scenarios: list[Scenario], verbose: bool = True) -> dict:
    model = PHSHeatModel()
    rows = []
    for sc in scenarios:
        t0 = time.time()
        plan0 = optimize(sc, "throughput", model, deterministic=True).plan
        row = {"scenario": sc.id, "tags": sc.tags}
        for arm in ARMS:
            row[arm] = run_arm(sc, plan0, arm, model)
        rows.append(row)
        if verbose:
            print(f"  {sc.id:10s} " + "  ".join(
                f"{a}: {row[a]['minutes_above_yellow']:4d} min>yellow, work {row[a]['throughput']:.2f}, "
                f"{row[a]['changes']} chg" for a in ARMS) + f"  ({time.time() - t0:.0f}s)")
    summary = {}
    for arm in ARMS:
        summary[arm] = {
            "minutes_above_yellow": int(sum(r[arm]["minutes_above_yellow"] for r in rows)),
            "minutes_above_red": int(sum(r[arm]["minutes_above_red"] for r in rows)),
            "scenarios_with_time_above_yellow": int(sum(r[arm]["minutes_above_yellow"] > 0 for r in rows)),
            "throughput_mean": round(float(np.mean([r[arm]["throughput"] for r in rows])), 4),
            "changes_total": int(sum(r[arm]["changes"] for r in rows)),
            "auto_changes_total": int(sum(r[arm]["auto_changes"] for r in rows)),
        }
    return {"rows": rows, "summary": summary,
            "setup": {"true_offset_mean_sd": [TRUE_OFFSET_MEAN, TRUE_OFFSET_SD],
                      "weather_error_mean_sd": [WEATHER_ERR_MEAN, WEATHER_ERR_SD],
                      "forecast_margin_c": FORECAST_MARGIN_C, "check_every_min": CHECK_EVERY_MIN,
                      "early_line_c": config.PLAN_EARLY_LINE_C}}


def table(res: dict) -> str:
    s = res["summary"]
    base = s["plan_only"]
    lines = ["| Arm | Worker-min ≥ 38.0 °C | vs plan only | Worker-min ≥ 38.5 °C | Scenarios with any | Work done | Changes (auto) |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for arm in ARMS:
        a = s[arm]
        cut = (1 - a["minutes_above_yellow"] / base["minutes_above_yellow"]) if base["minutes_above_yellow"] else 0.0
        lines.append(f"| {arm} | {a['minutes_above_yellow']} | {-cut:+.0%} | {a['minutes_above_red']} "
                     f"| {a['scenarios_with_time_above_yellow']}/{len(res['rows'])} | {a['throughput_mean']:.1%} "
                     f"| {a['changes_total']} ({a['auto_changes_total']}) |")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--limit", type=int, default=None, help="only the first N test scenarios")
    args = ap.parse_args()
    scen = load_test_set()[:args.limit] if args.limit else load_test_set()
    t0 = time.time()
    res = evaluate(scen)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1))
    print()
    print(table(res))
    print(f"\nSimulation, held-out test scenarios. Saved {args.out} in {time.time() - t0:.0f}s")
