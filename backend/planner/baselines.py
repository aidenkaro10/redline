"""
The 5 baselines from the design doc. Each one is a "planner": scenario in, plan out.

1. fixed       what crews do today: work through the task list, standard breaks, no heat or weather planning
2. optimizer   CP-SAT + the verify loop, no LLM
3. llm_only    Claude writes the schedule, NO rule checker. We expect safety violations:
               that's the generator-verifier gap, and why the rule checker exists.
4. agent_day1  the full Archon-style agent (archon.py), empty playbook
5. agent_day14 the same agent after 14 simulated days of playbook learning (Loop 2)

Every planner returns (plan, info) where info has latency_s, cost_usd and notes.
"""

import json
import time

from backend import config
from backend.planner.llm import cached_text, extract_json, get_llm
from backend.planner.optimizer import optimize
from backend.planner.scenario import INTENSITIES, REST, Plan, Scenario


def fixed_schedule(scenario: Scenario, run: int = 0):
    """Tasks in list order, each worker grabs the next task they can do, a break after every 90 min."""
    t0 = time.time()
    remaining = {t.id: t.units for t in scenario.tasks}
    assign = {}
    for w in scenario.crew:
        row, streak = [], 0
        for s in range(scenario.n_slots):
            if not w.is_available(s) or streak >= config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS:
                row.append(REST)
                streak = 0
                continue
            pick = next((t for t in scenario.tasks
                         if remaining[t.id] > 0 and (t.skill is None or t.skill in w.skills)
                         and t.earliest <= s < (t.deadline if t.deadline is not None else scenario.n_slots)), None)
            if pick is None:
                row.append(REST)
                streak = 0
            else:
                row.append(pick.id)
                remaining[pick.id] -= 1
                streak += 1
        assign[w.id] = row
    return Plan(assign, source="fixed"), {"latency_s": time.time() - t0, "cost_usd": 0.0}


def optimizer_only(scenario: Scenario, run: int = 0):
    r = optimize(scenario, "throughput")
    return r.plan, {"latency_s": r.solve_s, "cost_usd": 0.0, "notes": r.log[-1]}


# ---------------------------------------------------------------------------
# LLM only, no verifier
# ---------------------------------------------------------------------------
LLM_SYSTEM = "You are a construction and utility crew scheduler. You answer with JSON only."


def scenario_brief(scenario: Scenario) -> dict:
    """A compact, LLM-friendly view of a scenario (also used by the agent's tools)."""
    return {
        "site": scenario.site["name"], "date": scenario.date,
        "slots": [{"slot": s, "time": scenario.slot_time(s), "wbgt_c": w.wbgt, "air_c": w.tdb}
                  for s, w in enumerate(scenario.weather)],
        "crew": [{"id": w.id, "name": w.name, "acclimatized": w.acclim >= config.PLAN_UNACCLIM_LEVEL,
                  "skills": w.skills, "on_site_slots": w.available if w.available is not None else "all",
                  "current_core_c": w.start_core_c} for w in scenario.crew],
        "tasks": [{"id": t.id, "name": t.name, "intensity": t.intensity, "met": INTENSITIES[t.intensity][0],
                   "outdoor": t.outdoor, "units_needed": t.units, "priority": t.priority,
                   "allowed_slots": [t.earliest, (t.deadline if t.deadline is not None else scenario.n_slots) - 1],
                   "skill": t.skill} for t in scenario.tasks],
        "hazards": [{"kind": h.kind, "slots": [h.start_slot, h.end_slot - 1], "source": h.source,
                     "stops_outdoor_work": h.stops_outdoor_work()} for h in scenario.hazards],
    }


def rules_in_words() -> str:
    return (
        f"Hard safety rules:\n"
        f"- Every worker's 90th percentile core temperature must stay below {config.PLAN_MAX_P90_C} °C all shift. "
        f"Harder work, sun and high WBGT heat people up; 30-min shade rests cool them down.\n"
        f"- Breaks: at most {config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS} work slots in a row, then a 'rest' slot; "
        f"at most {config.PLAN_MAX_CONSECUTIVE_WORK_SLOTS_HOT} in a row if any of those slots has WBGT >= {config.PLAN_HOT_WBGT_C} °C.\n"
        f"- No outdoor tasks during hazards that stop outdoor work. After lightning, outdoor work stays stopped for "
        f"{config.PLAN_LIGHTNING_ALLCLEAR_MIN} more minutes.\n"
        f"- Workers who are not acclimatized never do very_heavy tasks when WBGT >= {config.PLAN_UNACCLIM_NO_VERY_HEAVY_WBGT_C} °C.\n"
        f"Validity: one entry per slot per worker; off-site workers rest; skills and allowed slots must match.\n"
    )


def llm_prompt(scenario: Scenario) -> str:
    return (
        "Plan tomorrow's shift for this crew in 30-minute slots. Get as much priority-weighted work done as you "
        "safely can (priority 1 matters most). Each slot, a worker does one task id or \"rest\" (shade break). "
        "A task needs units_needed worker-slots in total; several workers can share a task.\n\n"
        + rules_in_words()
        + "\nScenario:\n" + json.dumps(scenario_brief(scenario))
        + f"\n\nAnswer with only this JSON: {{\"assignments\": {{\"<worker id>\": [<{scenario.n_slots} entries>], ...}}, "
          "\"notes\": \"<one short sentence>\"}}"
    )


def llm_only(scenario: Scenario, run: int = 0):
    """Claude plans alone. Its plan is scored as-is: no rule checker, no retries."""
    t0 = time.time()
    prompt = llm_prompt(scenario)
    key = f"llm_only|{scenario.id}|run{run}"
    text = cached_text(prompt, LLM_SYSTEM, key)
    cost = 0.0
    if text is None:
        llm = get_llm()
        if llm is None:
            return None, {"latency_s": 0.0, "cost_usd": 0.0, "notes": "skipped: no ANTHROPIC_API_KEY"}
        text = llm.text(prompt, system=LLM_SYSTEM, cache_key=key)
        cost = llm.cost_usd
    data = extract_json(text) or {}
    assignments = data.get("assignments") if isinstance(data.get("assignments"), dict) else {}
    plan = Plan({str(k): [str(a) for a in v] for k, v in assignments.items() if isinstance(v, list)},
                source="llm_only", notes=str(data.get("notes", ""))[:300])
    return plan, {"latency_s": time.time() - t0, "cost_usd": cost}


def agent_day1(scenario: Scenario, run: int = 0):
    from backend.planner.archon import plan_with_agent
    r = plan_with_agent(scenario, playbook=None, run=run)
    return r.plan, {"latency_s": r.latency_s, "cost_usd": r.cost_usd, "notes": r.summary}


def agent_day14(scenario: Scenario, run: int = 0):
    from backend.planner.archon import plan_with_agent
    from backend.planner.playbook import Playbook
    book = Playbook.load_learned()
    if book is None:
        return None, {"latency_s": 0.0, "cost_usd": 0.0, "notes": "skipped: no learned playbook yet (run the Loop 2 training)"}
    r = plan_with_agent(scenario, playbook=book, run=run)
    return r.plan, {"latency_s": r.latency_s, "cost_usd": r.cost_usd, "notes": r.summary}


BASELINES = {
    "fixed": fixed_schedule,
    "optimizer": optimizer_only,
    "llm_only": llm_only,
    "agent_day1": agent_day1,
    "agent_day14": agent_day14,
}
DETERMINISTIC = {"fixed", "optimizer"}   # same plan every run, so we run them once and count it 5 times
