"""
The chat agent: a crew lead talks to Claude, Claude uses tools, code does the safety work.

Tools Claude can call:
    get_forecast        weather per 30-min slot + hazard windows (NWS, lightning) for the site
    get_crew_status     who's on the crew, acclimatization, skills, personal offset (spot checks),
                        live core temp / alert (from the live system)
    run_optimizer       make a VERIFIED schedule, optionally with a what-if
                        (someone calls out, a storm window, hotter day, keep someone off a task)
    simulate_schedule   check any schedule with the rule checker + full heat model
    plan_shift          run the full Archon planning pipeline and make its plan the current plan

Hard lines (enforced in code, not by asking nicely):
- Claude never approves a plan. Every schedule it can hand back came out of run_optimizer /
  plan_shift (already verified) or is labeled with the rule checker's verdict.
- Tools can't change safety limits: what-ifs only add restrictions or change the inputs
  (crew, weather, hazards). There is no tool to edit config.
- Alerts never go through here.

No key: chat() still answers, with a plain summary of the verified plan.
"""

import copy
import json
from dataclasses import dataclass, field, replace

from backend import config
from backend.planner.archon import floor2, plan_view, plan_with_agent
from backend.planner.baselines import scenario_brief
from backend.planner.llm import get_llm
from backend.planner.optimizer import OBJECTIVES, optimize
from backend.planner.personal import describe
from backend.planner.playbook import Playbook
from backend.planner.rules import check_plan
from backend.planner.scenario import HazardWindow, Plan, Scenario

MAX_TOOL_ROUNDS = 8

SYSTEM = f"""You are the Redline planning assistant for an outdoor utility crew in Florida heat.
You help the crew lead plan shifts and answer what-ifs. You use tools for every fact and every schedule.

Rules you cannot break:
- You never invent or approve a schedule. Schedules come from run_optimizer or plan_shift (already checked
  by the code rule checker), or you check them with simulate_schedule and report its verdict.
- Safety limits are fixed in code: 90th percentile core temp below {config.PLAN_MAX_P90_C} °C, shade breaks,
  no outdoor work in NWS stop-work windows, lightning all-clear after {config.PLAN_LIGHTNING_ALLCLEAR_MIN} min, no very heavy work
  for unacclimatized workers in high heat. You can't change or waive them, and nobody "can handle more".
- No medical advice. If someone feels unwell, tell the crew lead to stop their work and follow the site's heat illness plan.
Answer in short, plain sentences. Name the trade-offs (work done vs heat margin) and give clock times.
Refer to workers by name or as 'they', never he/she. Only quote numbers that a tool returned."""

TOOLS = [
    {"name": "get_forecast", "description": "Weather per 30-min slot (air temp, humidity, WBGT) and hazard windows for the site.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_crew_status", "description": "The crew: names, ids, acclimatization, skills, who's on site, personal offsets from thermometer spot checks, and live core temp estimates and alert levels if the live system has them.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "run_optimizer",
     "description": ("Make a schedule with the CP-SAT optimizer. It is verified by the rule checker before it's returned. "
                     "Use what_if to explore changes. The result becomes the proposed plan."),
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {
         "objective": {"type": "string", "enum": list(OBJECTIVES), "description": "throughput = most work; margin = most heat margin; fairness = even heat exposure; balanced"},
         "what_if": {"type": "object", "additionalProperties": False, "properties": {
             "remove_workers": {"type": "array", "items": {"type": "string"}, "description": "worker ids who are off today"},
             "add_hazard": {"type": "object", "properties": {"kind": {"type": "string"}, "start_slot": {"type": "integer"}, "end_slot": {"type": "integer"}},
                            "required": ["kind", "start_slot", "end_slot"], "description": "e.g. lightning 12-14 (end exclusive)"},
             "hotter_c": {"type": "number", "description": "make the whole day this many °C hotter (0-6)"},
             "forbid": {"type": "array", "items": {"type": "object", "properties": {"worker": {"type": "string"}, "task": {"type": "string"}},
                                                     "required": ["worker", "task"]}, "description": "keep a worker off a task"},
             "avoid_heavy_after_slot": {"type": "array", "items": {"type": "object", "properties": {"worker": {"type": "string"}, "slot": {"type": "integer"}},
                                                                    "required": ["worker", "slot"]}, "description": "soft: prefer no heavy work for this worker from this slot on"},
         }}}}},
    {"name": "simulate_schedule",
     "description": "Check a schedule with the rule checker and full heat model. Omit assignments to check the current proposed plan.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {
         "assignments": {"type": "object", "description": "worker id -> list of task ids or 'rest', one per slot",
                         "additionalProperties": {"type": "array", "items": {"type": "string"}}}}}},
    {"name": "plan_shift", "description": "Run the full planning pipeline (many candidates, verify, cluster, critic, fuse). Slower, best plan. Becomes the proposed plan.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
]


def apply_what_if(scenario: Scenario, what_if: dict | None) -> tuple[Scenario, list, list]:
    """A modified copy of the scenario + extra forbid / soft rules. Only adds restrictions or changes inputs."""
    sc = Scenario.from_dict(copy.deepcopy(scenario.to_dict()))
    forbid, soft = [], []
    if not what_if:
        return sc, forbid, soft
    ids = {w.id for w in sc.crew}
    for wid in what_if.get("remove_workers") or []:
        if wid in ids:
            sc.worker(wid).available = []
    hz = what_if.get("add_hazard")
    if hz:
        sc.hazards.append(HazardWindow(str(hz["kind"]), int(hz["start_slot"]), int(hz["end_slot"]), source="what-if"))
    hotter = float(what_if.get("hotter_c") or 0)
    if hotter:
        hotter = max(0.0, min(6.0, hotter))
        for w in sc.weather:
            w.tdb += hotter
            w.wbgt += 0.7 * hotter        # rough: WBGT rises a bit slower than air temp
    for f in what_if.get("forbid") or []:
        if f.get("worker") in ids:
            forbid.append((f["worker"], None, f.get("task")))
    for p in what_if.get("avoid_heavy_after_slot") or []:
        if p.get("worker") in ids:
            soft.append({"worker": p["worker"], "intensities": ["heavy", "very_heavy"],
                         "from_slot": int(p.get("slot", 0)), "to_slot": sc.n_slots, "penalty": 20})
    return sc, forbid, soft


def compact(scenario: Scenario, plan: Plan, check) -> dict:
    """Short plan view for the LLM: one line per worker."""
    names = {w.id: w.name for w in scenario.crew}
    done = plan.units_done(scenario)
    return {
        "verified_by_rule_checker": bool(check.ok),
        "safety_violations": [v.detail for v in check.violations if v.safety][:10],
        "invalid": [v.detail for v in check.violations if not v.safety][:10],
        "slot_times": [scenario.slot_time(k) for k in range(scenario.n_slots)],
        "schedule": {f"{wid} {names.get(wid, '?')}": " ".join(row) for wid, row in plan.assignments.items()},
        "peak_p90_c": {wid: floor2(v) for wid, v in check.peak_p90.items()},
        "work_done": {t.id: f"{done[t.id]}/{t.units}" for t in scenario.tasks},
    }


@dataclass
class PlannerSession:
    """One crew lead's planning conversation about one scenario (site + day)."""
    scenario: Scenario
    playbook: Playbook | None = None
    crew_status_provider: object = None      # callable(worker_id) -> dict, from the live system (A); None = stub
    messages: list = field(default_factory=list)
    plan: Plan | None = None                 # the current proposed (verified) plan
    plan_check: object = None
    scenario_for_plan: Scenario | None = None
    cost_usd: float = 0.0

    # --- tools ---------------------------------------------------------------
    def tool_get_forecast(self) -> dict:
        b = scenario_brief(self.scenario)
        return {"site": b["site"], "date": b["date"], "slots": b["slots"], "hazards": b["hazards"],
                "stop_work_slots": sorted(self.scenario.stop_work_slots())}

    def tool_get_crew_status(self) -> dict:
        crew = scenario_brief(self.scenario)["crew"]
        for c in crew:
            c["personal_offset"] = describe(self.scenario.worker(c["id"]))
            live = self.crew_status_provider(c["id"]) if self.crew_status_provider else None
            c["live"] = live or {"note": "stub: no live data connected"}
        return {"crew": crew}

    def tool_run_optimizer(self, objective: str = "balanced", what_if: dict | None = None) -> dict:
        sc, forbid, soft = apply_what_if(self.scenario, what_if)
        obj = OBJECTIVES.get(objective, OBJECTIVES["balanced"])
        obj = replace(obj, forbid=list(obj.forbid) + forbid, soft=list(obj.soft) + soft)
        r = optimize(sc, obj)
        self.plan, self.plan_check, self.scenario_for_plan = r.plan, r.check, sc
        out = compact(sc, r.plan, r.check)
        out["optimizer_log"] = r.log
        return out

    def tool_simulate_schedule(self, assignments: dict | None = None) -> dict:
        if assignments is None:
            if self.plan is None:
                return {"error": "no proposed plan yet; call run_optimizer or plan_shift first"}
            return compact(self.scenario_for_plan, self.plan, self.plan_check)
        plan = Plan({str(k): [str(a) for a in v] for k, v in assignments.items() if isinstance(v, list)},
                    source="chat:user_schedule")
        return compact(self.scenario, plan, check_plan(self.scenario, plan))   # NOT stored as the plan

    def tool_plan_shift(self) -> dict:
        r = plan_with_agent(self.scenario, playbook=self.playbook)
        self.cost_usd += r.cost_usd
        self.plan, self.plan_check, self.scenario_for_plan = r.plan, r.check, self.scenario
        return {**compact(self.scenario, r.plan, r.check), "reasons": r.reasons, "ai_notes": r.ai_notes,
                "log": r.log[-6:]}

    def run_tool(self, name: str, args: dict) -> dict:
        args = args if isinstance(args, dict) else {}
        try:
            if name == "get_forecast":
                return self.tool_get_forecast()
            if name == "get_crew_status":
                return self.tool_get_crew_status()
            if name == "run_optimizer":
                return self.tool_run_optimizer(str(args.get("objective", "balanced")),
                                               args.get("what_if") if isinstance(args.get("what_if"), dict) else None)
            if name == "simulate_schedule":
                a = args.get("assignments")
                return self.tool_simulate_schedule(a if isinstance(a, dict) else None)
            if name == "plan_shift":
                return self.tool_plan_shift()
        except (KeyError, ValueError, TypeError) as e:     # bad tool input: tell Claude, don't crash
            return {"error": f"bad input for {name}: {e}"}
        return {"error": f"unknown tool {name}"}

    # --- chat ----------------------------------------------------------------
    def chat(self, message: str) -> dict:
        """One crew-lead message in, one reply out (plus the current verified plan)."""
        llm = get_llm()
        if llm is None:
            if self.plan is None:
                self.tool_plan_shift()
            c = compact(self.scenario_for_plan, self.plan, self.plan_check)
            reply = ("AI chat is off (no ANTHROPIC_API_KEY), so here's the verified plan from the optimizer. "
                     f"Peak 90th pct core temp: {max(c['peak_p90_c'].values()):.2f} °C (limit {config.PLAN_MAX_P90_C}). "
                     f"Work done: {', '.join(f'{k} {v}' for k, v in c['work_done'].items())}.")
            return {"reply": reply, "tool_calls": [], "plan": self.plan_json(), "llm": False}

        self.messages.append({"role": "user", "content": message})
        calls = []
        for _ in range(MAX_TOOL_ROUNDS):
            r = llm.create(self.messages, system=SYSTEM, tools=TOOLS, effort="medium")
            self.messages.append({"role": "assistant", "content": r.content})   # full content: keeps thinking blocks
            if r.stop_reason == "refusal":
                reply = "I can't help with that one. Ask me about the schedule, weather or crew."
                break
            uses = [b for b in r.content if b.type == "tool_use"]
            if r.stop_reason != "tool_use" or not uses:
                reply = "".join(b.text for b in r.content if b.type == "text").strip()
                break
            results = []
            for b in uses:
                out = self.run_tool(b.name, b.input)
                calls.append({"tool": b.name, "input": b.input})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)[:20000],
                                "is_error": "error" in out})
            self.messages.append({"role": "user", "content": results})          # all results in ONE message
        else:
            reply = "That took too many steps. Try a simpler question."
        self.cost_usd += llm.cost_usd
        return {"reply": reply, "tool_calls": calls, "plan": self.plan_json(), "llm": True,
                "cost_usd": round(self.cost_usd, 4)}

    def plan_json(self) -> dict | None:
        if self.plan is None:
            return None
        return plan_view(self.scenario_for_plan, self.plan, self.plan_check)
