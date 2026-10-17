"""
The schedule optimizer: Google OR-Tools CP-SAT.

It decides, for every worker and 30-min slot, which task they do (or rest), to get
the most important work done while every hard rule holds:
  - each worker's heat stays under their budget (the linear heat table from the heat model)
  - shade breaks (max 3 work slots in a row, max 2 when it's hot)
  - no outdoor work in NWS stop-work / lightning windows
  - no very heavy work for unacclimatized workers when WBGT >= 28 °C
  - skills, time windows, who's on site

Then the loop the spec asks for:
  solve -> check with the FULL heat model (rules.py) -> if anyone crosses the limit,
  tighten that worker's heat budget and re-solve -> repeat. If it still can't find a
  verified plan, it returns the all-rest plan (always safe) and says so.

How heat works inside the solver (all integers, in 0.01 °C):
  T[w, s+1] >= (1 - alpha) * T[w, s] + alpha * Teq[w, s, activity]   and   T <= budget
T is an upper bound on the 90th pct core temp at the end of each slot.
"""

import time
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from backend import config
from backend.planner.heat_model import HeatModel, default_heat_model
from backend.planner.rules import CheckResult, check_plan
from backend.planner.scenario import REST, Plan, Scenario, all_rest_plan

PRIORITY_WEIGHT = {1: 10, 2: 5, 3: 2}     # value of one work unit, by task priority
SCALE = 100                                # temps in 0.01 °C
Q = 1000                                   # alpha as a fraction of 1000


@dataclass
class Objective:
    """
    How to trade things off. The Archon pipeline and the evolution loop try different ones.
    Safety is NOT in here: it's a hard constraint in every variant.
    """
    name: str = "throughput"
    throughput: int = 10          # weight on priority-weighted work done
    margin: int = 0               # weight on keeping everyone's peak heat low
    fairness: int = 0             # weight on keeping peaks similar across the crew
    budget_margin_c: float = 0.05 # plan this far under the limit (the table is approximate)
    # Storm chance as a SOFT cost (storm_risk.py): an outdoor work unit in a slot with storm chance p
    # costs storm * p, doubled for elevated or hard-to-stop tasks. 15 is below the value of any work
    # unit at 60% chance, so work moves EARLIER when there's room instead of being dropped.
    storm: int = 15
    # Soft preferences, e.g. from playbook lessons or the LLM:
    # {"worker": "w3", "intensities": ["heavy", "very_heavy"], "from_slot": 10, "to_slot": 16, "penalty": 20}
    soft: list = field(default_factory=list)
    # Hard extra rules from a what-if, e.g. ("w2", None, "t4") = w2 never does t4; slot None = all slots
    forbid: list = field(default_factory=list)


OBJECTIVES = {
    "throughput": Objective("throughput", throughput=10, margin=0, fairness=0, budget_margin_c=0.05),
    "balanced": Objective("balanced", throughput=10, margin=1, fairness=1, budget_margin_c=0.10),
    "margin": Objective("margin", throughput=10, margin=3, fairness=0, budget_margin_c=0.20),
    "fairness": Objective("fairness", throughput=10, margin=1, fairness=4, budget_margin_c=0.10),
}


@dataclass
class OptimizerResult:
    plan: Plan
    check: CheckResult
    verified: bool                # passed the full rule check
    iterations: int               # how many solve -> check rounds
    solve_s: float
    log: list[str] = field(default_factory=list)


def solve(scenario: Scenario, costs, objective: Objective, budgets: dict[str, float],
          time_limit_s: float = 5.0, deterministic: bool = False) -> Plan | None:
    """One CP-SAT solve. Returns None if no schedule satisfies every constraint."""
    m = cp_model.CpModel()
    S, crew, tasks = scenario.n_slots, scenario.crew, scenario.tasks
    blocked = scenario.stop_work_slots()
    hot = [w.wbgt >= config.PLAN_HOT_WBGT_C for w in scenario.weather]
    forbid = {(f[0], f[1], f[2]) for f in objective.forbid}

    x = {}      # x[w, s, t] = worker w does task t in slot s
    rest = {}
    for w in crew:
        for s in range(S):
            rest[w.id, s] = m.NewBoolVar(f"rest_{w.id}_{s}")
            options = [rest[w.id, s]]
            for t in tasks:
                deadline = t.deadline if t.deadline is not None else S
                allowed = (w.is_available(s)
                           and t.earliest <= s < deadline
                           and (t.skill is None or t.skill in w.skills)
                           and not (t.outdoor and s in blocked)
                           and not (t.intensity == "very_heavy" and w.acclim < config.PLAN_UNACCLIM_LEVEL
                                    and scenario.weather[s].wbgt >= config.PLAN_UNACCLIM_NO_VERY_HEAVY_WBGT_C)
                           and (w.id, None, t.id) not in forbid and (w.id, s, t.id) not in forbid)
                if allowed:
                    x[w.id, s, t.id] = m.NewBoolVar(f"x_{w.id}_{s}_{t.id}")
                    options.append(x[w.id, s, t.id])
            m.AddExactlyOne(options)

    # Tasks can't be over-worked.
    done = {}
    for t in tasks:
        vs = [v for (wid, s, tid), v in x.items() if tid == t.id]
        done[t.id] = sum(vs) if vs else 0
        if vs:
            m.Add(done[t.id] <= t.units)

    # Breaks: in any (limit + 1) slots in a row, at least one rest.
    n, nh = config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS, config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS_HOT
    for w in crew:
        for s in range(S):
            if s + n + 1 <= S:
                m.Add(sum(rest[w.id, k] for k in range(s, s + n + 1)) >= 1)
            if s + nh + 1 <= S and any(hot[s:s + nh + 1]):
                m.Add(sum(rest[w.id, k] for k in range(s, s + nh + 1)) >= 1)

    # Heat: T[s+1] >= (1 - alpha) T[s] + alpha Teq, for whichever activity is chosen.
    T, peak = {}, {}
    lo, hi = 30 * SCALE, 45 * SCALE
    for w in crew:
        budget = int(round(budgets[w.id] * SCALE))
        T[w.id, 0] = m.NewIntVar(lo, hi, f"T_{w.id}_0")
        m.Add(T[w.id, 0] >= int(round(costs.start[w.id] * SCALE)))
        peak[w.id] = m.NewIntVar(lo, hi, f"peak_{w.id}")
        for s in range(S):
            nxt = m.NewIntVar(lo, budget, f"T_{w.id}_{s + 1}")
            T[w.id, s + 1] = nxt
            m.Add(peak[w.id] >= nxt)
            # which activity is this slot? rest, or the (intensity, outdoor) of the chosen task
            by_act = {REST: [rest[w.id, s]]}
            for t in tasks:
                if (w.id, s, t.id) in x:
                    by_act.setdefault((t.intensity, t.outdoor), []).append(x[w.id, s, t.id])
            for act, vs in by_act.items():
                alpha = costs.alpha[w.id][s][act]
                teq = int(round(costs.teq[w.id][s][act] * SCALE))
                keep = int(round((1 - alpha) * Q))
                on = m.NewBoolVar("")
                m.Add(sum(vs) == 1).OnlyEnforceIf(on)
                m.Add(sum(vs) == 0).OnlyEnforceIf(on.Not())
                m.Add(Q * nxt >= keep * T[w.id, s] + (Q - keep) * teq).OnlyEnforceIf(on)

    # Objective.
    value = sum(PRIORITY_WEIGHT.get(t.priority, 1) * done[t.id] for t in tasks if not isinstance(done[t.id], int))
    obj = objective.throughput * SCALE * value
    if objective.margin:
        obj -= objective.margin * sum(peak.values())
    if objective.fairness and len(crew) > 1:
        top, bottom = m.NewIntVar(lo, hi, "top"), m.NewIntVar(lo, hi, "bottom")
        m.AddMaxEquality(top, list(peak.values()))
        m.AddMinEquality(bottom, list(peak.values()))
        obj -= objective.fairness * len(crew) * (top - bottom)
    if objective.storm:
        for (wid, s, tid), var in x.items():
            t = next(t for t in tasks if t.id == tid)
            p = scenario.weather[s].storm_prob
            if t.outdoor and p > 0:
                mult = 1 + int(t.elevated) + int(t.hard_to_stop)
                obj -= int(round(objective.storm * SCALE * p * mult)) * var
    for pref in objective.soft:
        for s in range(pref.get("from_slot", 0), min(S, pref.get("to_slot", S))):
            for t in tasks:
                if t.intensity in pref.get("intensities", []) and (pref["worker"], s, t.id) in x:
                    obj -= int(pref.get("penalty", 10)) * SCALE * x[pref["worker"], s, t.id]
    m.Maximize(obj)

    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    if deterministic:
        # Same answer every run (tests, reproducible demos): one thread, a work budget instead of a clock.
        solver.parameters.num_workers = 1
        solver.parameters.max_deterministic_time = time_limit_s
    else:
        solver.parameters.max_time_in_seconds = time_limit_s
        solver.parameters.num_workers = 8
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    assign = {}
    for w in crew:
        row = []
        for s in range(S):
            chosen = next((t.id for t in tasks if (w.id, s, t.id) in x and solver.Value(x[w.id, s, t.id])), REST)
            row.append(chosen)
        assign[w.id] = row
    return Plan(assign, source=f"optimizer:{objective.name}")


def optimize(scenario: Scenario, objective: Objective | str = "throughput", heat_model: HeatModel | None = None,
             max_rounds: int = 6, time_limit_s: float = 5.0, deterministic: bool = False) -> OptimizerResult:
    """Solve, verify with the full model, tighten budgets and re-solve until it passes.
    deterministic=True: same plan every run (single thread), for tests and reproducible results."""
    t0 = time.time()
    heat_model = heat_model or default_heat_model()
    if isinstance(objective, str):
        objective = OBJECTIVES[objective]
    costs = heat_model.slot_costs(scenario)
    budgets = {w.id: config.PLAN_MAX_P90_C - objective.budget_margin_c for w in scenario.crew}
    log = []
    for rnd in range(1, max_rounds + 1):
        plan = solve(scenario, costs, objective, budgets, time_limit_s, deterministic)
        if plan is None:
            log.append(f"round {rnd}: no feasible schedule under the current budgets")
            break
        check = check_plan(scenario, plan, heat_model)
        if check.ok:
            log.append(f"round {rnd}: verified, peak 90th pct {max(check.peak_p90.values()):.2f} °C")
            return OptimizerResult(plan, check, True, rnd, time.time() - t0, log)
        heat = [v for v in check.violations if v.rule == "heat"]
        other = [v for v in check.violations if v.rule != "heat"]
        if other:   # the solver encodes these exactly, so this would be a bug: never ship it
            log.append(f"round {rnd}: BUG, non-heat violations: {[v.detail for v in other]}")
            break
        for v in heat:
            over = check.peak_p90[v.worker] - config.PLAN_MAX_P90_C
            budgets[v.worker] -= over + 0.05
            log.append(f"round {rnd}: {v.worker} peaked {check.peak_p90[v.worker]:.2f} °C, "
                       f"budget tightened to {budgets[v.worker]:.2f}")
    fallback = all_rest_plan(scenario)
    check = check_plan(scenario, fallback, heat_model)
    log.append("fell back to the all-rest plan" + (" (verified)" if check.ok else " (NOT verified: conditions too hot even at rest)"))
    return OptimizerResult(fallback, check, check.ok, max_rounds, time.time() - t0, log)
