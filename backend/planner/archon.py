"""
The planning agent at inference time, Archon-style.

    gather  ->  many candidates  ->  rule checker  ->  slot-by-slot scoring  ->  cluster by outcome
            ->  critic + meta-check  ->  fuse  ->  re-verify  ->  present

1. Candidates: CP-SAT with 4 objective weightings, the same 4 again with the playbook's
   soft preferences, up to 3 LLM strategy ideas (turned into optimizer hints), and one
   schedule the LLM writes directly.
2. Rule checker: anything with a violation is thrown out. No exceptions.
3. Scoring, slot by slot: the full heat model gives everyone's 90th pct each minute;
   we track the tightest safety margin in every slot, not just the day's peak.
4. Cluster: plans with the same outcome (same throughput, same per-worker peaks) count once.
5. Critic: a code critic always checks the soft constitution (fair hot-task rotation,
   new hires' heavy work late in the day, lessons followed). With a key, the LLM critic
   adds complaints; a META-CHECK in code keeps only complaints whose facts are true.
6. Fuse: re-solve with the best plan's objective plus fixes for the confirmed complaints.
7. Re-verify the fused plan. It's used only if it passes and scores at least as well.

The LLM proposes. The rule checker approves. Safety limits are never in the LLM's hands.
No key = the same pipeline without the LLM steps.
"""

import json
import math
import re
import time
from dataclasses import dataclass, field, replace

import numpy as np

from backend import config
from backend.planner.baselines import rules_in_words, scenario_brief
from backend.planner.heat_model import HeatModel, default_heat_model
from backend.planner.llm import extract_json, get_llm
from backend.planner.optimizer import OBJECTIVES, PRIORITY_WEIGHT, Objective, objectives_for, optimize
from backend.planner.weights import PlannerWeights
from backend.planner.playbook import Playbook
from backend.planner.rules import CheckResult, check_plans
from backend.planner.scenario import INTENSITIES, REST, Plan, Scenario, all_rest_plan
from backend.planner.storm_risk import storm_reason

HOT_INTENSITIES = {"heavy", "very_heavy"}


@dataclass
class AgentSettings:
    """What Loop 3 (evolution) is allowed to change. Nothing here touches safety."""
    objectives: tuple = ("throughput", "balanced", "margin", "fairness")
    use_playbook: bool = True
    n_llm_ideas: int = 3
    llm_direct_plan: bool = True
    fuse: bool = True
    w_peak: float = 0.5            # ranking: penalty per °C of peak above 37.5
    w_fair: float = 0.002          # ranking: penalty per degree-minute of heat-dose spread
    w_complaint: float = 0.03      # ranking: penalty per confirmed critic complaint
    critic_instructions: str = ("Check the soft constitution: rotate hot tasks fairly, keep new hires' heavy "
                                "work early in the day, respect stated preferences, explain trade-offs.")
    time_limit_s: float = 3.0
    weights: PlannerWeights | None = None   # the optimizer's soft weights (None = defaults). Session F searches these.


@dataclass
class Candidate:
    name: str
    plan: Plan
    objective: Objective | None = None
    check: CheckResult | None = None
    score: dict = field(default_factory=dict)
    complaints: list = field(default_factory=list)


@dataclass
class AgentResult:
    plan: Plan
    check: CheckResult
    alternatives: list               # up to 2 other verified plans (Candidate)
    summary: str
    reasons: list[str]
    lessons_used: list[str]
    log: list[str]
    latency_s: float
    cost_usd: float
    candidates: list = field(default_factory=list)
    used_llm: bool = False           # False = made without Claude (no key, or use_llm=False): optimizer + rule checker only
    ai_notes: list = field(default_factory=list)      # labeled LLM notes, no numbers, safe to show
    ai_notes_raw: list = field(default_factory=list)  # the LLM's unfiltered bullets: evaluation only, never shown

    def to_dict(self, scenario: Scenario) -> dict:
        return {
            "plan": plan_view(scenario, self.plan, self.check),
            "alternatives": [{"name": c.name, **plan_view(scenario, c.plan, c.check), "score": c.score}
                             for c in self.alternatives],
            "summary": self.summary, "reasons": self.reasons, "lessons_used": self.lessons_used,
            "log": self.log, "latency_s": round(self.latency_s, 2), "cost_usd": round(self.cost_usd, 4),
            "llm": self.used_llm, "ai_notes": self.ai_notes,
            "candidates": [{"name": c.name, "verified": bool(c.check and c.check.ok),
                            "violations": c.check.n_safety if c.check else None, "score": c.score}
                           for c in self.candidates],
        }


NO_LLM_NOTE = "Made without the LLM (no ANTHROPIC_API_KEY, or LLM off): optimizer + rule checker only."


def floor2(x: float) -> float:
    """Round DOWN to 0.01, so a peak of 37.996 shows as 37.99, never as the 38.00 limit."""
    return math.floor(x * 100) / 100


# ---------------------------------------------------------------------------
# Scoring and critics (code)
# ---------------------------------------------------------------------------
def score(scenario: Scenario, plan: Plan, check: CheckResult, s: AgentSettings) -> dict:
    """Slot-by-slot scoring from the full heat model, plus the overall ranking number."""
    done = plan.units_done(scenario)
    total = sum(PRIORITY_WEIGHT.get(t.priority, 1) * t.units for t in scenario.tasks) or 1
    tp = sum(PRIORITY_WEIGHT.get(t.priority, 1) * done[t.id] for t in scenario.tasks) / total
    on_site = [w for w in scenario.crew if w.available != []] or scenario.crew
    p90 = np.stack([check.trace[w.id][:, 2] for w in on_site])                      # (workers, minutes)
    sm = scenario.slot_min
    slot_margin = [float(config.PLAN_MAX_P90_C - p90[:, k * sm:(k + 1) * sm].max()) for k in range(scenario.n_slots)]
    doses = [float(np.clip(check.trace[w.id][:, 1] - 37.0, 0, None).sum()) for w in on_site]
    peak = float(p90.max())
    spread = float(np.std(doses))
    rank = tp - s.w_peak * max(0.0, peak - 37.5) - s.w_fair * spread
    # peak rounded DOWN and margin rounded DOWN, so neither can display as safer-looking or over the limit by rounding
    return {"throughput": round(tp, 4), "peak_p90": math.floor(peak * 1000) / 1000,
            "min_margin_c": math.floor(min(slot_margin) * 1000) / 1000,
            "slot_margin_c": [round(m, 3) for m in slot_margin], "heat_spread": round(spread, 1),
            "rank": round(rank, 4)}


def code_critic(scenario: Scenario, plan: Plan) -> list[dict]:
    """
    Soft-constitution checks in plain code. Each complaint is a fact about the plan.
    Deliberately small: things like "new hires do better with heavy work early" are NOT
    hard-coded here. The agent has to learn them from outcomes (Loop 2, the playbook).
    """
    out = []
    hot = {t.id for t in scenario.tasks if t.intensity in HOT_INTENSITIES and t.outdoor}
    counts = {w.id: sum(a in hot for a in plan.assignments[w.id]) for w in scenario.crew if w.available != []}
    if counts and max(counts.values()) - min(counts.values()) >= 4:
        top = max(counts, key=counts.get)
        out.append({"type": "hot_rotation", "worker": top,
                    "text": f"{scenario.worker(top).name} gets {counts[top]} hot heavy slots; others as few as "
                            f"{min(counts.values())}. Rotate hot tasks."})
    return out


def meta_check(scenario: Scenario, plan: Plan, complaint: dict) -> bool:
    """Is the LLM critic's complaint actually true of this plan? (DeepSeekMath-V2 style meta-verifier.)"""
    wid = complaint.get("worker")
    if wid not in plan.assignments:
        return False
    slots = complaint.get("slots") or []
    if not slots or not all(isinstance(k, int) and 0 <= k < scenario.n_slots for k in slots):
        return False
    claimed = complaint.get("intensities") or list(HOT_INTENSITIES)
    for k in slots:
        a = plan.assignments[wid][k]
        if a == REST or a not in {t.id for t in scenario.tasks} or scenario.task(a).intensity not in claimed:
            return False                         # the critic claimed hard work that isn't there
    return True


def fixes_for(complaints: list[dict], scenario: Scenario) -> list[dict]:
    """Turn confirmed complaints into soft preferences for the fusion solve."""
    prefs = []
    for c in complaints:
        if c.get("slots"):
            prefs.append({"worker": c["worker"], "intensities": c.get("intensities") or sorted(HOT_INTENSITIES),
                          "from_slot": min(c["slots"]), "to_slot": scenario.n_slots, "penalty": 15})
        elif c["type"] == "hot_rotation":
            prefs.append({"worker": c["worker"], "intensities": sorted(HOT_INTENSITIES),
                          "from_slot": 0, "to_slot": scenario.n_slots, "penalty": 3})
    return prefs


# ---------------------------------------------------------------------------
# LLM steps
# ---------------------------------------------------------------------------
IDEAS_SYSTEM = ("You help plan safe outdoor crew shifts. A code rule checker and an optimizer make the final "
                "schedule; you suggest strategy ideas. Answer with JSON only.")


def llm_ideas(llm, scenario: Scenario, playbook: Playbook | None, best: Candidate | None, s: AgentSettings):
    """Ask Claude for strategy ideas (as optimizer hints) and, optionally, one direct schedule."""
    best_txt = json.dumps(plan_view(scenario, best.plan, best.check)) if best else "none yet"
    prompt = (
        f"Scenario:\n{json.dumps(scenario_brief(scenario))}\n\n{rules_in_words()}\n"
        f"Playbook lessons:\n{playbook.text() if playbook else '(none)'}\n\n"
        f"Best optimizer plan so far:\n{best_txt}\n\n"
        f"Suggest up to {s.n_llm_ideas} different strategy ideas, like 'front-load outdoor work before the storm' "
        "or 'swap the new hire to light work after 11:00'. Express each idea as optimizer hints:\n"
        '{"ideas": [{"name": "...", "why": "...", "objective": "throughput|balanced|margin|fairness", '
        '"soft": [{"worker": "<id>", "intensities": ["heavy"], "from_slot": 0, "to_slot": 16, "penalty": 10}], '
        '"forbid": [["<worker id>", <slot or null>, "<task id>"]]}]'
        + (', "direct_plan": {"assignments": {"<worker id>": [<one entry per slot>]}}' if s.llm_direct_plan else "")
        + "}\nHints can only discourage or forbid assignments. You cannot change any safety rule."
    )
    data = extract_json(llm.text(prompt, system=IDEAS_SYSTEM, effort="medium")) or {}
    ideas = []
    workers = {w.id for w in scenario.crew}
    tasks = {t.id for t in scenario.tasks}
    for idea in (data.get("ideas") or [])[:s.n_llm_ideas]:
        if not isinstance(idea, dict):
            continue
        soft = [p for p in idea.get("soft", []) if isinstance(p, dict) and p.get("worker") in workers
                and set(p.get("intensities", [])) <= set(INTENSITIES)
                and 0 < int(p.get("penalty", 0)) <= 50]
        forbid = [tuple(f) for f in idea.get("forbid", []) if isinstance(f, list) and len(f) == 3
                  and f[0] in workers and f[2] in tasks and (f[1] is None or isinstance(f[1], int))]
        objs = objectives_for(s.weights) if s.weights is not None else OBJECTIVES
        base = objs.get(idea.get("objective"), objs["balanced"])
        ideas.append((str(idea.get("name", "idea"))[:60], replace(base, name=f"llm:{base.name}", soft=soft, forbid=forbid),
                      str(idea.get("why", ""))[:200]))
    direct = None
    dp = data.get("direct_plan")
    if isinstance(dp, dict) and isinstance(dp.get("assignments"), dict):
        direct = Plan({str(k): [str(a) for a in v] for k, v in dp["assignments"].items() if isinstance(v, list)},
                      source="llm:direct")
    return ideas, direct


def llm_critic(llm, scenario: Scenario, cands: list[Candidate], s: AgentSettings) -> dict[str, list[dict]]:
    views = {c.name: plan_view(scenario, c.plan, c.check) for c in cands}
    prompt = (f"{s.critic_instructions}\n\nCrew and tasks:\n{json.dumps(scenario_brief(scenario))}\n\n"
              f"Verified plans:\n{json.dumps(views)}\n\n"
              'List concrete complaints as JSON: {"complaints": [{"plan": "<name>", "worker": "<id>", '
              '"slots": [<slot numbers>], "intensities": ["heavy"], "text": "..."}]}. '
              "Only complain about specific heavy assignments you can point to.")
    data = extract_json(llm.text(prompt, system=IDEAS_SYSTEM, effort="medium")) or {}
    out = {}
    for c in data.get("complaints", []) or []:
        if isinstance(c, dict) and c.get("plan") in views:
            out.setdefault(c["plan"], []).append({**c, "type": "llm"})
    return out


PRONOUN_RULE = "Refer to workers by name or as 'they', never he/she."
_GENDERED = re.compile(r"\b(he|she|him|his|her|hers|himself|herself)\b", re.IGNORECASE)


def ai_notes_from(bullets: list[str]) -> list[str]:
    """
    LLM bullets the crew lead may see, as labeled AI notes. Numbers only ever come from code, so any
    bullet with a digit is dropped (a local model once wrote wrong percentages); so is any bullet that
    calls a worker he/she.
    """
    out = []
    for b in bullets:
        if re.search(r"\d", b) or _GENDERED.search(b):
            continue
        out.append(f"AI note: {b}")
    return out[:3]


def explain(llm, scenario: Scenario, best: Candidate, alts: list[Candidate], lessons: list[str],
            playbook: Playbook | None) -> tuple[str, list[str], list[str], list[str]]:
    """
    Plain reasons for the crew lead. Returns (summary, reasons, ai_notes, raw_bullets).
    reasons are ALWAYS the code-computed facts. With an LLM, its bullets become separate,
    labeled ai_notes (filtered by ai_notes_from); raw_bullets are kept for evaluation only.
    """
    sc = best.score
    facts = (f"Peak 90th pct core temp {floor2(sc['peak_p90']):.2f} °C (limit {config.PLAN_MAX_P90_C}), tightest margin "
             f"{floor2(sc['min_margin_c']):.2f} °C, {sc['throughput']:.0%} of priority-weighted work done.")
    reasons = [facts]
    blocked = sorted(scenario.stop_work_slots())
    if blocked:
        reasons.append(f"No outdoor work {scenario.slot_time(blocked[0])}-{scenario.slot_time(blocked[-1] + 1)} "
                       f"({', '.join(sorted({h.kind for h in scenario.hazards if h.stops_outdoor_work()}))}).")
    storm = storm_reason(scenario, best.plan)
    if storm:
        reasons.append(storm)
    for w in scenario.crew:
        if w.acclim < config.PLAN_UNACCLIM_LEVEL:
            reasons.append(f"{w.name} is not acclimatized: no very heavy work in high heat.")
    if alts:
        reasons.append("Alternatives: " + "; ".join(
            f"{a.name} ({a.score['throughput']:.0%} work, peak {floor2(a.score['peak_p90']):.2f} °C)" for a in alts))
    if playbook and lessons:
        used = {l.id: l.text for l in playbook.lessons}
        reasons += [f"Lesson {lid}: {used[lid]}" for lid in lessons if lid in used]
    summary = f"Recommended: {best.name}. {facts}"
    raw = []
    if llm is not None:
        txt = llm.text("Write 3 short plain-language bullet points (no jargon) for a crew lead explaining this "
                       f"schedule's trade-offs. Don't use numbers. {PRONOUN_RULE} Facts:\n" + "\n".join(reasons),
                       effort="low")
        raw = [b.strip("-• ").strip() for b in txt.splitlines() if b.strip()][:5]
    return summary, reasons, ai_notes_from(raw), raw


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------
def plan_with_agent(scenario: Scenario, playbook: Playbook | None = None, run: int = 0,
                    settings: AgentSettings | None = None, heat_model: HeatModel | None = None,
                    use_llm: bool = True, llm=None) -> AgentResult:
    """use_llm=False forces the no-LLM pipeline; llm= lets tests pass a fake LLM."""
    t0 = time.time()
    s = settings or AgentSettings()
    heat_model = heat_model or default_heat_model()
    if llm is None and use_llm:
        llm = get_llm()
    log = []

    # 1. Candidates from the optimizer (each one is already verified by optimize()'s own loop).
    cands: list[Candidate] = []
    prefs, lessons_used = (playbook.soft_preferences(scenario) if (playbook and s.use_playbook) else ([], []))
    for name in s.objectives:
        obj = (objectives_for(s.weights) if s.weights is not None else OBJECTIVES)[name]
        r = optimize(scenario, obj, heat_model, time_limit_s=s.time_limit_s)
        cands.append(Candidate(f"optimizer:{name}", r.plan, obj))
        if prefs:
            obj_p = replace(obj, name=f"{name}+playbook", soft=list(obj.soft) + prefs)
            r = optimize(scenario, obj_p, heat_model, time_limit_s=s.time_limit_s)
            cands.append(Candidate(f"optimizer:{name}+playbook", r.plan, obj_p))
    if lessons_used:
        log.append(f"playbook: using lessons {lessons_used}")

    # 2. LLM ideas, compiled into optimizer runs, plus one direct LLM schedule.
    if llm is not None and (s.n_llm_ideas or s.llm_direct_plan):
        first = check_plans(scenario, [cands[0].plan], heat_model)[0]
        ideas, direct = llm_ideas(llm, scenario, playbook if s.use_playbook else None,
                                  Candidate(cands[0].name, cands[0].plan, check=first), s)
        for name, obj, why in ideas:
            r = optimize(scenario, obj, heat_model, time_limit_s=s.time_limit_s)
            cands.append(Candidate(f"llm-idea:{name}", r.plan, obj))
            log.append(f"LLM idea '{name}': {why}")
        if direct is not None:
            cands.append(Candidate("llm:direct", direct, None))

    # 3. Rule checker on EVERY candidate (optimizer plans included). Violations = discarded.
    checks = check_plans(scenario, [c.plan for c in cands], heat_model)
    survivors = []
    for c, chk in zip(cands, checks):
        c.check = chk
        if chk.ok:
            c.score = score(scenario, c.plan, chk, s)
            survivors.append(c)
        else:
            log.append(f"rule checker rejected {c.name}: {chk.n_safety} safety violations, "
                       f"{len(chk.violations) - chk.n_safety} invalid "
                       f"({', '.join(sorted({v.rule for v in chk.violations}))})")
    if not survivors:
        fb = all_rest_plan(scenario)
        chk = check_plans(scenario, [fb], heat_model)[0]
        return AgentResult(fb, chk, [], "No verified plan: everyone rests in the shade.", [], [], log,
                           time.time() - t0, llm.cost_usd if llm else 0.0, cands, used_llm=llm is not None)

    # 4. Cluster by outcome: same throughput and same per-worker peaks = same plan.
    clusters = {}
    for c in survivors:
        key = (round(c.score["throughput"], 2),) + tuple(round(c.check.peak_p90[w.id] * 20) / 20 for w in scenario.crew)
        if key not in clusters or c.score["rank"] > clusters[key].score["rank"]:
            clusters[key] = c
    reps = sorted(clusters.values(), key=lambda c: -c.score["rank"])
    log.append(f"{len(cands)} candidates, {len(survivors)} verified, {len(reps)} distinct outcomes")

    # 5. Critic (code always, LLM if available) + meta-check, on the top 3.
    top = reps[:3]
    llm_complaints = llm_critic(llm, scenario, top, s) if llm is not None else {}
    for c in top:
        c.complaints = code_critic(scenario, c.plan)
        for comp in llm_complaints.get(c.name, []):
            if meta_check(scenario, c.plan, comp):
                c.complaints.append(comp)
            else:
                log.append(f"meta-check dropped an LLM complaint about {c.name}: not true of the plan")
        c.score["complaints"] = len(c.complaints)
        c.score["rank"] = round(c.score["rank"] - s.w_complaint * len(c.complaints), 4)
    top.sort(key=lambda c: -c.score["rank"])
    best = top[0]

    # 6. Fuse: best plan's objective + the soft preferences of the runner-up + fixes for confirmed complaints.
    if s.fuse and best.objective is not None and (best.complaints or len(top) > 1):
        extra = fixes_for(best.complaints, scenario)
        if len(top) > 1 and top[1].objective is not None:
            extra += [p for p in top[1].objective.soft if p not in best.objective.soft]
        fused_obj = replace(best.objective, name=f"fused:{best.objective.name}", soft=list(best.objective.soft) + extra)
        r = optimize(scenario, fused_obj, heat_model, time_limit_s=s.time_limit_s)
        # 7. Re-verify (the fused plan goes through the rule checker like everything else).
        chk = check_plans(scenario, [r.plan], heat_model)[0]
        if chk.ok:
            fused = Candidate("fused", r.plan, fused_obj, chk, score(scenario, r.plan, chk, s))
            fused.complaints = code_critic(scenario, fused.plan)
            fused.score["complaints"] = len(fused.complaints)
            fused.score["rank"] = round(fused.score["rank"] - s.w_complaint * len(fused.complaints), 4)
            if fused.score["rank"] >= best.score["rank"] - 1e-6:
                log.append(f"fused plan kept (rank {fused.score['rank']:.3f} vs {best.score['rank']:.3f})")
                best = fused
            else:
                log.append("fused plan scored lower; kept the best single plan")
        else:
            log.append("fused plan failed the rule checker; discarded")

    alts = [c for c in reps if c is not best and c.plan.assignments != best.plan.assignments][:2]
    used = lessons_used if (best.objective and any(p.get("lesson") for p in best.objective.soft)) else []
    summary, reasons, ai_notes, raw_bullets = explain(llm, scenario, best, alts, used, playbook)
    best.plan.notes = summary
    best.plan.lessons_used = used
    best.plan.source = f"agent:{best.name}"
    return AgentResult(best.plan, best.check, alts, summary, reasons, used, log, time.time() - t0,
                       llm.cost_usd if llm else 0.0, cands, used_llm=llm is not None,
                       ai_notes=ai_notes, ai_notes_raw=raw_bullets)


# ---------------------------------------------------------------------------
# A readable view of a plan (for the LLM, the API and the frontend)
# ---------------------------------------------------------------------------
def plan_view(scenario: Scenario, plan: Plan, check: CheckResult | None = None) -> dict:
    names = {t.id: t.name for t in scenario.tasks}
    workers = []
    for w in scenario.crew:
        row = plan.assignments.get(w.id, [])
        workers.append({
            "id": w.id, "name": w.name,
            "slots": [{"slot": k, "time": scenario.slot_time(k), "task": a, "task_name": names.get(a, "Rest (shade)")}
                      for k, a in enumerate(row)],
            "peak_p90_c": floor2(check.peak_p90[w.id]) if check and w.id in check.peak_p90 else None,
        })
    done = plan.units_done(scenario)
    return {
        "source": plan.source, "verified": bool(check and check.ok),
        "violations": [v.__dict__ for v in check.violations] if check else None,
        "workers": workers,
        "tasks": [{"id": t.id, "name": t.name, "done": done[t.id], "needed": t.units, "priority": t.priority}
                  for t in scenario.tasks],
    }
