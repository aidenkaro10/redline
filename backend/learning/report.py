"""
Scores the learned agents next to session B's baselines, on held-out scenarios only.

Two scorecards for every planner:
  1. B's benchmark (rule checker + planning heat model): success rate, safety violations,
     throughput, peak 90th pct. Same code B uses (backend/planner/benchmark.py score_plans).
  2. The simulated world (world.py): how the plan actually went. Outcome score, workers who
     would have hit a heat alert, actual peak.

Planners:
  fixed          B: today's practice (task list in order, a break every 90 min)
  optimizer      B: CP-SAT + verify loop, no LLM
  llm_only       B: Claude alone, NO rule checker (only if B's cached answers exist or --llm-only)
  agent_day1     B's full agent, empty playbook, no LLM
  agent_day14    the same agent with the playbook learned in Loop 2 (ACE)
  agent_evolved  the agent with Loop 3's best settings (GEPA) + the learned playbook

Held-out sets: B's 12 test scenarios (never learned from) and our home-crew validation days
(never used to write a lesson or a mutation).

Run:  python -m backend.learning.report
"""

import argparse
import json
import time

import numpy as np

from backend.learning import settings
from backend.learning.parallel import pmap
from backend.learning.world import mean_outcome, outcomes
from backend.planner.archon import plan_with_agent
from backend.planner.baselines import fixed_schedule, llm_only, optimizer_only
from backend.planner.benchmark import score_plans
from backend.planner.generate import load_test_set
from backend.planner.playbook import Lesson, Playbook

REALIZATIONS = (0, 1, 2)


def load_playbook() -> Playbook | None:
    if not settings.PLAYBOOK_PATH.exists():
        return None
    d = json.loads(settings.PLAYBOOK_PATH.read_text())
    return Playbook([Lesson(**l) for l in d["lessons"]], d.get("version", 0), d.get("history", []))


def load_evolved():
    from backend.learning.gepa import to_settings
    p = settings.HERE / "best_agent.json"
    return to_settings(json.loads(p.read_text())["genome"]) if p.exists() else None


def plan_for(name: str, scenario):
    t0 = time.time()
    if name == "fixed":
        plan = fixed_schedule(scenario)[0]
    elif name == "optimizer":
        plan = optimizer_only(scenario)[0]
    elif name == "llm_only":
        plan = llm_only(scenario, 0)[0]
    elif name == "agent_day1":
        plan = plan_with_agent(scenario, use_llm=False).plan
    elif name == "agent_day14":
        plan = plan_with_agent(scenario, playbook=load_playbook(), use_llm=False).plan
    elif name == "agent_evolved":
        plan = plan_with_agent(scenario, playbook=load_playbook(), settings=load_evolved(), use_llm=False).plan
    else:
        raise KeyError(name)
    return plan, time.time() - t0


def _job(args):
    name, sc = args
    plan, latency = plan_for(name, sc)
    if plan is None:
        return {"scenario": sc.id, "skipped": True}
    bench = score_plans(sc, [plan])[0]
    world = mean_outcome(outcomes(sc, plan, REALIZATIONS)) if bench["valid"] else None
    return {"scenario": sc.id, "tags": sc.tags, "bench": bench, "world": world, "latency_s": round(latency, 1)}


def run_report(planners: list[str], sets: dict) -> dict:
    out = {}
    for set_name, scenarios in sets.items():
        out[set_name] = {}
        for name in planners:
            rows = pmap(_job, [(name, sc) for sc in scenarios])
            rows = [r for r in rows if not r.get("skipped")]
            if not rows:
                out[set_name][name] = {"skipped": True}
                continue
            worlds = [r["world"] for r in rows if r["world"]]
            out[set_name][name] = {
                "n": len(rows),
                "success_rate": round(float(np.mean([r["bench"]["success"] for r in rows])), 3),
                "safety_violations": int(sum(r["bench"]["n_safety"] for r in rows)),
                "invalid_plans": int(sum(not r["bench"]["valid"] for r in rows)),
                "throughput": round(float(np.mean([r["bench"]["throughput"] for r in rows])), 3),
                "peak_p90_max": round(max((r["bench"]["peak_p90"] or 0) for r in rows), 2),
                "world_score": round(float(np.mean([w["score"] for w in worlds])), 4) if worlds else None,
                "world_alerted_per_day": round(float(np.mean([w["workers_alerted"] for w in worlds])), 3) if worlds else None,
                "world_peak_c": round(float(np.mean([w["actual_peak_c"] for w in worlds])), 3) if worlds else None,
                "latency_s": round(float(np.mean([r["latency_s"] for r in rows])), 1),
                "rows": rows,
            }
            print(f"  {set_name:10s} {name:14s} {out[set_name][name]['success_rate']:.2f} ok, "
                  f"{out[set_name][name]['safety_violations']} safety violations, world score "
                  f"{out[set_name][name]['world_score']}")
    return out


def table(rep: dict) -> str:
    lines = []
    for set_name, res in rep.items():
        lines += [f"**{set_name}**", "",
                  "| Planner | Passes rule checker | Safety violations | Throughput | Peak p90 (°C) | Simulated score | Workers alerted / day | Simulated peak (°C) |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, r in res.items():
            if r.get("skipped"):
                lines.append(f"| {name} | skipped (no key / no cache) | | | | | | |")
                continue
            f = lambda v, fmt: "-" if v is None else format(v, fmt)
            lines.append(f"| {name} | {r['success_rate']:.0%} | {r['safety_violations']} | {r['throughput']:.2f} | "
                         f"{r['peak_p90_max']:.2f} | {f(r['world_score'], '.3f')} | {f(r['world_alerted_per_day'], '.2f')} | "
                         f"{f(r['world_peak_c'], '.2f')} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    from backend.learning.curriculum import bank_scenarios, load_bank
    ap = argparse.ArgumentParser()
    ap.add_argument("--planners", default="fixed,optimizer,agent_day1,agent_day14,agent_evolved")
    ap.add_argument("--sets", default="test,home_val")
    args = ap.parse_args()
    sets = {}
    if "test" in args.sets:
        sets["test"] = load_test_set()
    if "home_val" in args.sets:
        sets["home_val"] = bank_scenarios(load_bank(split="val", crew="home"))
    rep = run_report(args.planners.split(","), sets)
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    print()
    print(table(rep))
