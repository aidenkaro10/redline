"""
The rule checker: plain code, no LLM. Every plan passes through here before anyone sees it.

The LLM and the optimizer can PROPOSE plans. Only this file can APPROVE one.
All limits come from backend/config.py (the PLAN_ block) and are never learned.

Hard safety rules:
  heat        every worker's 90th pct core temp stays below PLAN_MAX_P90_C (38.0 °C) every minute,
              checked with the full heat model (minute by minute, not the optimizer's shortcut)
  breaks      at most 3 work slots in a row (90 min) before a 30-min shade break,
              at most 2 in a row (60 min) when any of those slots is hot (WBGT >= 30 °C)
  hazards     no outdoor work during NWS stop-work windows
  lightning   no outdoor work until 30 min after the last strike within 10 miles
  new hires   no very heavy work for unacclimatized workers when WBGT >= 28 °C

Validity rules (a plan breaking these is rejected too, but they aren't "safety"):
  every worker has one entry per slot, entries are real task ids or "rest",
  off-site workers are resting, skills match, tasks are inside their time window.
"""

from dataclasses import asdict, dataclass, field

import numpy as np

from backend import config
from backend.planner.heat_model import HeatModel, default_heat_model
from backend.planner.scenario import REST, Plan, Scenario


@dataclass
class Violation:
    rule: str            # "heat", "breaks", "hazard", "lightning", "new_hire", or a validity rule
    safety: bool         # True = hard safety rule
    worker: str
    slot: int
    detail: str


@dataclass
class CheckResult:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    peak_p90: dict[str, float] = field(default_factory=dict)   # per worker, °C
    trace: dict | None = None                                  # worker -> (minutes, 3) from the heat model

    @property
    def n_safety(self) -> int:
        return sum(v.safety for v in self.violations)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "n_safety_violations": self.n_safety,
                "violations": [asdict(v) for v in self.violations],
                "peak_p90": {k: round(v, 3) for k, v in self.peak_p90.items()}}


def check_structure(scenario: Scenario, plan: Plan) -> list[Violation]:
    out = []
    task_ids = {t.id for t in scenario.tasks}
    for w in scenario.crew:
        slots = plan.assignments.get(w.id)
        if slots is None or len(slots) != scenario.n_slots:
            out.append(Violation("shape", False, w.id, -1,
                                 f"needs exactly {scenario.n_slots} slots, got {None if slots is None else len(slots)}"))
            continue
        for s, a in enumerate(slots):
            if a == REST:
                continue
            if a not in task_ids:
                out.append(Violation("unknown_task", False, w.id, s, f"'{a}' is not a task id"))
                continue
            t = scenario.task(a)
            if not w.is_available(s):
                out.append(Violation("off_site", False, w.id, s, f"{w.name} isn't on site but is assigned {t.name}"))
            if t.skill and t.skill not in w.skills:
                out.append(Violation("skill", False, w.id, s, f"{t.name} needs {t.skill}"))
            deadline = t.deadline if t.deadline is not None else scenario.n_slots
            if not (t.earliest <= s < deadline):
                out.append(Violation("time_window", False, w.id, s, f"{t.name} only allowed in slots {t.earliest}-{deadline - 1}"))
    for wid in plan.assignments:
        if wid not in {w.id for w in scenario.crew}:
            out.append(Violation("unknown_worker", False, wid, -1, "not in the crew"))
    return out


def check_rules(scenario: Scenario, plan: Plan) -> list[Violation]:
    """Every hard rule that doesn't need the heat model."""
    out = []
    blocked = scenario.stop_work_slots()
    lightning_slots = set()
    hazard_kind = {}
    for h in scenario.hazards:
        if h.stops_outdoor_work():
            extra = -(-config.PLAN_LIGHTNING_ALLCLEAR_MIN // scenario.slot_min) if h.kind == "lightning" else 0
            for s in range(h.start_slot, min(scenario.n_slots, h.end_slot + extra)):
                hazard_kind.setdefault(s, h.kind)
                if h.kind == "lightning":
                    lightning_slots.add(s)
    hot = [w.wbgt >= config.PLAN_HOT_WBGT_C for w in scenario.weather]
    task_ids = {t.id for t in scenario.tasks}

    for w in scenario.crew:
        slots = plan.assignments.get(w.id)
        if slots is None or len(slots) != scenario.n_slots:
            continue                     # reported by check_structure
        work = [a != REST and a in task_ids and w.is_available(s) for s, a in enumerate(slots)]

        for s, a in enumerate(slots):
            if not work[s]:
                continue
            t = scenario.task(a)
            if t.outdoor and s in blocked:
                rule = "lightning" if s in lightning_slots else "hazard"
                out.append(Violation(rule, True, w.id, s,
                                     f"{w.name} outdoors ({t.name}) at {scenario.slot_time(s)} during {hazard_kind[s]} stop-work"))
            if (t.intensity == "very_heavy" and w.acclim < config.PLAN_UNACCLIM_LEVEL
                    and scenario.weather[s].wbgt >= config.PLAN_UNACCLIM_NO_VERY_HEAVY_WBGT_C):
                out.append(Violation("new_hire", True, w.id, s,
                                     f"{w.name} isn't acclimatized: no very heavy work at WBGT {scenario.weather[s].wbgt:.1f} °C"))

        # Breaks: no run of (limit + 1) work slots in a row.
        n = config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS
        nh = config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS_HOT
        flagged = set()
        for s in range(scenario.n_slots):
            if s + n + 1 <= scenario.n_slots and all(work[s:s + n + 1]) and s not in flagged:
                out.append(Violation("breaks", True, w.id, s,
                                     f"{w.name} works {(n + 1) * scenario.slot_min} min straight from {scenario.slot_time(s)} without a break"))
                flagged.update(range(s, s + n + 1))
            elif (s + nh + 1 <= scenario.n_slots and all(work[s:s + nh + 1]) and any(hot[s:s + nh + 1])
                  and s not in flagged):
                out.append(Violation("breaks", True, w.id, s,
                                     f"{w.name} works {(nh + 1) * scenario.slot_min} min straight in WBGT >= {config.PLAN_HOT_WBGT_C} from {scenario.slot_time(s)}"))
                flagged.update(range(s, s + nh + 1))
    return out


def check_heat(scenario: Scenario, plan: Plan, trace: dict) -> tuple[list[Violation], dict[str, float]]:
    """90th pct core temp must stay below the limit at every minute."""
    out, peaks = [], {}
    limit = config.PLAN_MAX_P90_C
    for w in scenario.crew:
        p90 = trace[w.id][:, 2]
        peaks[w.id] = float(p90.max())
        over = np.nonzero(p90 >= limit)[0]
        if len(over):
            m = int(over[0])
            s = m // scenario.slot_min
            out.append(Violation("heat", True, w.id, s,
                                 f"{w.name}'s 90th pct core temp reaches {p90.max():.2f} °C (limit {limit}) "
                                 f"from {scenario.slot_time(s)}"))
    return out, peaks


def check_plans(scenario: Scenario, plans: list[Plan], heat_model: HeatModel | None = None) -> list[CheckResult]:
    """Check many plans at once (the heat model runs them in one batch)."""
    heat_model = heat_model or default_heat_model()
    results = [None] * len(plans)
    to_sim = []
    for i, p in enumerate(plans):
        structural = check_structure(scenario, p)
        if any(v.rule in ("shape", "unknown_worker") for v in structural):
            results[i] = CheckResult(False, structural)      # can't even simulate it
        else:
            to_sim.append(i)
    traces = heat_model.simulate(scenario, [plans[i] for i in to_sim]) if to_sim else []
    for i, trace in zip(to_sim, traces):
        v = check_structure(scenario, plans[i]) + check_rules(scenario, plans[i])
        hv, peaks = check_heat(scenario, plans[i], trace)
        v += hv
        results[i] = CheckResult(ok=not v, violations=v, peak_p90=peaks, trace=trace)
    return results


def check_plan(scenario: Scenario, plan: Plan, heat_model: HeatModel | None = None) -> CheckResult:
    return check_plans(scenario, [plan], heat_model)[0]
