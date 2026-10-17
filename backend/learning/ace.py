"""
Loop 2: the playbook (ACE: generator, reflector, curator) + Reflexion, with a gate.

Each simulated day:
  1. GENERATOR  The planner agent (backend/planner/archon.py) plans the day with the current
                playbook. It tags which lessons it used (plan.lessons_used).
  2. OUTCOME    The crew "works" the plan in the simulated world (world.py).
  3. REFLECTOR  Compares plan vs outcome: whose temp ran hotter than the planner forecast,
                on what kind of work, from what time; who would have triggered an alert.
                Code finds the facts; Claude (if there's a key) writes the root cause in
                plain words. Without a key, a code template writes it.
  4. CURATOR    Turns the reflection into SMALL itemized updates: add / edit / merge.
                Claude proposes up to 3; the code curator adds one written straight from the
                measured facts. Each update is labeled with its author.
                Never a full rewrite (ACE's "context collapse"). Near-duplicates get merged.
                Every lesson goes through the code safety filter (safety.py): anything about
                limits, thresholds, breaks, alerts or "can handle more" is rejected.
  5. GATE       Replay ACE_GATE_DAYS held-out training days (days no lesson was written from)
                with the old and the new playbook, ONE update at a time. Keep an update only if
                the mean outcome score goes up AND every plan passes the rule checker with zero
                violations. Otherwise roll it back.
Every version is saved (data/learning/ace/versions/vN.json) and can be restored.

Run:  python -m backend.learning.ace                 (no key: code reflector/curator, $0)
      python -m backend.learning.ace --llm           (Claude reflector + curator, budget-capped)
      python -m backend.learning.ace --fake          (fake LLM, offline)
"""

import argparse
import copy
import json
import time
from dataclasses import asdict

import numpy as np

from backend import config
from backend.learning import settings
from backend.learning.fake_llm import FakeLLM
from backend.learning.llm_budget import get_budget_llm, llm_summary
from backend.learning.parallel import pmap
from backend.learning.safety import lesson_ok
from backend.learning.world import NOT_LIGHT, mean_outcome, outcomes, score_outcome, simulate_actual
from backend.planner.archon import AgentSettings, plan_with_agent
from backend.planner.llm import extract_json
from backend.planner.playbook import MAX_LESSONS, Lesson, Playbook, _similar
from backend.planner.rules import check_plans
from backend.planner.scenario import REST

VERSIONS_DIR = settings.DATA_DIR / "ace" / "versions"
GATE_REALIZATIONS = (0, 1)       # each gate day is worked twice (different luck), scores averaged
EXCESS_MIN_C = 0.05              # reflector: a worker "ran hot" if their temp rose this much more per slot than forecast


# ---------------------------------------------------------------------------
# Reflector
# ---------------------------------------------------------------------------
def slot_hour(scenario, slot: int) -> float:
    h, m = map(int, scenario.shift_start.split(":"))
    return h + m / 60 + slot * scenario.slot_min / 60


def find_facts(scenario, plan, planned_trace: dict, actual: dict, outcome: dict) -> dict:
    """
    Plan vs outcome, in numbers. For each worker and slot: how much more did their temp rise
    than the planner forecast (actual rise minus forecast median rise)? Then look for a time
    of day after which hot (heavy) work ran hotter than forecast.
    """
    sm = scenario.slot_min
    findings, alerts = [], []
    excess = {}
    for w in scenario.crew:
        if w.available == []:
            continue
        a, p = actual[w.id], planned_trace[w.id][:, 1]
        ex = []
        for k in range(scenario.n_slots):
            lo, hi = k * sm, (k + 1) * sm - 1
            a0 = a[lo - 1] if lo else a[0]
            p0 = p[lo - 1] if lo else p[0]
            ex.append(float((a[hi] - a0) - (p[hi] - p0)))
        excess[w.id] = ex
    for w in scenario.crew:
        if w.id not in excess:
            continue
        row = plan.assignments[w.id]
        hot = [k for k, t in enumerate(row) if t != REST and scenario.task(t).intensity in NOT_LIGHT]
        best = None
        for H in (9.0, 10.0, 11.0, 12.0, 13.0):
            after = [k for k in hot if slot_hour(scenario, k) >= H]
            before = [k for k in hot if slot_hour(scenario, k) < H]
            if len(after) < 2:
                continue
            ex_after = float(np.mean([excess[w.id][k] for k in after]))
            # compare with this worker before H, and with the rest of the crew in the same slots
            others = [excess[o][k] for o in excess if o != w.id for k in after]
            base = max(float(np.mean([excess[w.id][k] for k in before])) if before else -1.0,
                       float(np.median(others)) if others else 0.0, 0.0)
            gain = ex_after - base
            if gain >= EXCESS_MIN_C and (best is None or gain > best["excess_c_per_slot"] + 0.01):
                # the lightest kind of work that ran hot, and everything heavier than it
                lightest = min(NOT_LIGHT.index(scenario.task(row[k]).intensity) for k in after)
                best = {"worker": w.id, "name": w.name, "first_name": w.name.split(" ")[0],
                        "unacclimatized": w.acclim < config.PLAN_UNACCLIM_LEVEL,
                        "intensities": list(NOT_LIGHT[lightest:]),
                        "from_hour": H, "excess_c_per_slot": round(gain, 3), "n_slots": len(after),
                        "tasks": sorted({scenario.task(row[k]).name for k in after})}
        if best:
            findings.append(best)
        mins = outcome["alert_minutes"].get(w.id, 0)
        if mins:
            first = int(np.argmax(actual[w.id] >= config.HEAT_WARNING_C))
            k = first // sm
            alerts.append({"worker": w.id, "name": w.name, "minutes": mins, "time": scenario.slot_time(k),
                           "doing": row[k] if row[k] == REST else scenario.task(row[k]).name,
                           "forecast_p90_c": round(float(planned_trace[w.id][first, 2]), 2),
                           "actual_c": round(float(actual[w.id][first]), 2)})
    return {"findings": findings, "alerts": alerts, "score": outcome["score"],
            "throughput": outcome["throughput"], "lessons_used": list(plan.lessons_used)}


REFLECT_PROMPT = """TASK: REFLECT
You review yesterday's crew schedule against what actually happened (simulated outcomes).
Find the ROOT CAUSE of any problem in plain words a crew lead understands. Don't suggest
changing any safety rule, limit, threshold or break rule: those are fixed in code.

Day: {day}
Facts (temperatures in °C; "excess" = how much more a worker's temp rose per 30-min slot than forecast):
{facts}

Playbook lessons the planner used today: {used}

Answer with JSON only: {{"root_cause": "...", "insights": ["short, specific scheduling insight", ...]}}"""


def code_reflect(facts: dict) -> dict:
    if not facts["findings"] and not facts["alerts"]:
        return {"root_cause": "Nothing ran hotter than forecast. No change needed.", "insights": []}
    parts = []
    for f in facts["findings"]:
        who = f"{f['first_name']} (not acclimatized)" if f["unacclimatized"] else f["first_name"]
        parts.append(f"{who} heated up faster than forecast on {_kind(f['intensities'])} from {int(f['from_hour'])}:00 "
                     f"({', '.join(f['tasks'])}), about {f['excess_c_per_slot']:.2f} °C extra per slot.")
    for a in facts["alerts"]:
        parts.append(f"{a['name'].split(' ')[0]} would have triggered a heat alert at {a['time']} while doing {a['doing']}.")
    return {"root_cause": " ".join(parts),
            "insights": [f"Move {f['first_name']}'s {_kind(f['intensities'])} earlier than {int(f['from_hour'])}:00."
                         for f in facts["findings"]]}


def _kind(intensities) -> str:
    return "heavy work" if intensities[0] in ("heavy", "very_heavy") else "moderate and heavy work"


def reflect(llm, day_label: str, facts: dict) -> dict:
    base = code_reflect(facts)
    if llm is None or (not facts["findings"] and not facts["alerts"]):
        return {**base, "by": "code"}
    llm.purpose = "ace:reflect"
    data = extract_json(llm.text(REFLECT_PROMPT.format(day=day_label, facts=json.dumps(facts),
                                                       used=facts["lessons_used"] or "none"), effort="low")) or {}
    if not isinstance(data.get("root_cause"), str):
        return {**base, "by": "code (LLM answer unusable)"}
    return {"root_cause": data["root_cause"][:600], "insights": [str(i)[:200] for i in data.get("insights", [])][:4],
            "by": "llm"}


# ---------------------------------------------------------------------------
# Curator
# ---------------------------------------------------------------------------
CURATE_PROMPT = """TASK: CURATE
You maintain a short playbook of scheduling lessons for a crew planner. Turn the reflection
below into at most 3 SMALL, specific updates. Never rewrite the whole playbook.

Current playbook (id: text | hint):
{playbook}

Reflection on {day}: {root_cause}
Insights: {insights}
Facts: {facts}

Operations:
  {{"op": "add", "text": "...", "hint": {{...}} or null}}
  {{"op": "edit", "id": "L3", "text": "...", "hint": {{...}} or null}}
  {{"op": "merge", "ids": ["L2", "L5"], "text": "...", "hint": {{...}} or null}}
Hint format (the only kind the optimizer accepts; it can only DISCOURAGE work, never allow more):
  {{"type": "avoid_intensity", "who": "unacclimatized" | "all" | "<first name>", "intensities": ["heavy", "very_heavy"],
    "from_hour": 11, "to_hour": 24, "penalty": 1-50}}
Rules: one idea per lesson, under 200 characters, plain words. Lessons must never mention limits,
thresholds, temperatures, breaks, alerts, lightning, or anyone handling more heat; such lessons are
rejected automatically. If nothing should change, return no operations.

Answer with JSON only: {{"operations": [...]}}"""


def code_curate(book: Playbook, facts: dict, day_label: str) -> list[dict]:
    """Plain-code curator: one finding -> one add (or an edit of the lesson that already covers it)."""
    ops = []
    for f in facts["findings"]:
        who = "unacclimatized" if f["unacclimatized"] else f["first_name"]
        hour = int(f["from_hour"])
        kind = _kind(f["intensities"])
        text = (f"New hires heat up faster than forecast on {kind} from {hour}:00. Give them that work "
                f"before {hour}:00 and light or indoor work after." if who == "unacclimatized" else
                f"{f['first_name']} heats up faster than forecast on {kind} from {hour}:00. Give {f['first_name']} "
                f"that work before {hour}:00.")
        hint = {"type": "avoid_intensity", "who": who, "intensities": f["intensities"], "from_hour": hour, "penalty": 15}
        existing = next((l for l in book.lessons if l.hint and l.hint.get("who") == who
                         and set(l.hint.get("intensities", [])) & set(f["intensities"])), None)
        if existing is None:
            ops.append({"op": "add", "text": text, "hint": hint})
        else:
            h = dict(existing.hint)
            h["from_hour"] = min(int(h.get("from_hour", 24)), hour)
            h["intensities"] = sorted(set(h["intensities"]) | set(f["intensities"]), key=["light", "moderate", "heavy", "very_heavy"].index)
            h["penalty"] = min(40, int(h.get("penalty", 15)) + 5)    # it happened again: lean on it harder
            if h != existing.hint:
                ops.append({"op": "edit", "id": existing.id, "text": text if h["from_hour"] != existing.hint.get("from_hour")
                            else existing.text, "hint": h})
    return ops


def curate(llm, book: Playbook, reflection: dict, facts: dict, day_label: str) -> tuple[list[dict], str]:
    """Claude's updates (if there's a key) PLUS the code curator's update written straight from the
    measured facts. Each is labeled with who wrote it ("by"); the gate judges each one on its own."""
    code_ops = [{**o, "by": "code"} for o in code_curate(book, facts, day_label)]
    if llm is None or reflection.get("by") != "llm":
        return code_ops, "code"
    llm.purpose = "ace:curate"
    pb = "\n".join(f"{l.id}: {l.text} | {json.dumps(l.hint)}" for l in book.lessons) or "(empty)"
    data = extract_json(llm.text(CURATE_PROMPT.format(playbook=pb, day=day_label, root_cause=reflection["root_cause"],
                                                      insights=reflection["insights"], facts=json.dumps(facts)),
                                 effort="low")) or {}
    ops = [{**o, "by": "llm"} for o in (data.get("operations") or []) if isinstance(o, dict)][:3]
    return ops + code_ops, "llm+code"


def next_id(book: Playbook) -> str:
    nums = [int(l.id[1:]) for l in book.lessons if l.id[1:].isdigit()]
    return f"L{max(nums, default=0) + 1}"


def apply_ops(book: Playbook, ops: list[dict], day_label: str) -> tuple[Playbook, list[dict]]:
    """Apply small updates to a COPY of the playbook. Every lesson passes the safety filter. Returns (new book, log)."""
    new = copy.deepcopy(book)
    log = []
    for op in ops:
        kind = op.get("op")
        hint = op.get("hint") if isinstance(op.get("hint"), dict) else None
        text = str(op.get("text", "")).strip()
        if kind == "add":
            lesson = Lesson(id=next_id(new), text=text, hint=hint, source=day_label)
            ok, why = lesson_ok(lesson)
            if not ok:
                log.append({"op": "add", "text": text, "result": "rejected", "why": why})
                continue
            dup = next((l for l in new.lessons if _similar(l.text, text) or (hint and l.hint and _same_target(l.hint, hint))), None)
            if dup is not None:                       # near-duplicate: merge into the existing lesson instead
                merged = _merge_hints(dup.hint, hint)
                cand = Lesson(dup.id, dup.text, merged, dup.source, dup.uses, dup.helped)
                if lesson_ok(cand)[0] and merged != dup.hint:
                    dup.hint = merged
                    log.append({"op": "merge", "into": dup.id, "text": text, "result": "merged near-duplicate"})
                else:
                    log.append({"op": "add", "text": text, "result": "skipped", "why": f"near-duplicate of {dup.id}"})
                continue
            if len(new.lessons) >= MAX_LESSONS:
                log.append({"op": "add", "text": text, "result": "rejected", "why": "playbook full"})
                continue
            new.lessons.append(lesson)
            log.append({"op": "add", "id": lesson.id, "text": text, "hint": hint, "result": "applied"})
        elif kind == "edit":
            target = next((l for l in new.lessons if l.id == op.get("id")), None)
            if target is None:
                log.append({"op": "edit", "id": op.get("id"), "result": "rejected", "why": "no such lesson"})
                continue
            cand = Lesson(target.id, text or target.text, hint, target.source, target.uses, target.helped)
            ok, why = lesson_ok(cand)
            if not ok:
                log.append({"op": "edit", "id": target.id, "text": cand.text, "result": "rejected", "why": why})
                continue
            log.append({"op": "edit", "id": target.id, "before": {"text": target.text, "hint": target.hint},
                        "text": cand.text, "hint": hint, "result": "applied"})
            target.text, target.hint = cand.text, hint
            target.source = f"{target.source}; edited {day_label}"
        elif kind == "merge":
            ids = [i for i in op.get("ids", []) if any(l.id == i for l in new.lessons)]
            if len(ids) < 2:
                log.append({"op": "merge", "ids": op.get("ids"), "result": "rejected", "why": "needs 2+ existing ids"})
                continue
            keep = next(l for l in new.lessons if l.id == ids[0])
            cand = Lesson(keep.id, text or keep.text, hint if hint is not None else keep.hint, keep.source,
                          sum(l.uses for l in new.lessons if l.id in ids), sum(l.helped for l in new.lessons if l.id in ids))
            ok, why = lesson_ok(cand)
            if not ok:
                log.append({"op": "merge", "ids": ids, "result": "rejected", "why": why})
                continue
            new.lessons = [l for l in new.lessons if l.id not in ids[1:]]
            keep.text, keep.hint, keep.uses, keep.helped = cand.text, cand.hint, cand.uses, cand.helped
            keep.source = f"{keep.source}; merged {','.join(ids[1:])} {day_label}"
            log.append({"op": "merge", "ids": ids, "text": cand.text, "result": "applied"})
        else:
            log.append({"op": kind, "result": "rejected", "why": "unknown op"})
    return new, log


def _same_target(a: dict, b: dict) -> bool:
    return a.get("who") == b.get("who") and bool(set(a.get("intensities", [])) & set(b.get("intensities", [])))


def _merge_hints(a: dict | None, b: dict | None) -> dict | None:
    if not a or not b:
        return a or b
    return {**a, "intensities": sorted(set(a["intensities"]) | set(b["intensities"])),
            "from_hour": min(a.get("from_hour", 0), b.get("from_hour", 0)),
            "penalty": max(int(a.get("penalty", 0)), int(b.get("penalty", 0)))}


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------
def gate_scores(book: Playbook | None, gate_days: list, agent_settings: AgentSettings) -> dict:
    """Plan every gate day with this playbook (no LLM: free and repeatable), work each plan in the
    simulated world, and count rule-checker violations."""
    rows, safety, per_day = [], 0, []
    for sc, (n_safety, outs, used) in zip(gate_days, pmap(_gate_job, [(sc, book, agent_settings) for sc in gate_days])):
        safety += n_safety
        rows += outs
        per_day.append({"day": sc.id, **mean_outcome(outs), "lessons_used": used})
    return {**mean_outcome(rows), "safety_violations": safety, "per_day": per_day}


def _gate_job(args):
    sc, book, agent_settings = args
    r = plan_with_agent(sc, playbook=book, settings=agent_settings, use_llm=False)
    n_safety = check_plans(sc, [r.plan])[0].n_safety            # re-check: never trust, always verify
    return n_safety, outcomes(sc, r.plan, GATE_REALIZATIONS), r.plan.lessons_used


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def snapshot(book: Playbook, extra: dict) -> dict:
    return {"version": book.version, "lessons": [asdict(l) for l in book.lessons], **extra}


def run_ace(days: list, gate_days: list, llm=None, agent_settings: AgentSettings | None = None,
            llm_in_generator: bool = False, verbose: bool = True, save_versions: bool = True) -> dict:
    """
    days       the learning days, in order (lessons are written from these)
    gate_days  held-out training days (only used to accept/reject updates)
    llm        Claude (BudgetLLM), FakeLLM, or None for the plain-code reflector/curator
    llm_in_generator  also let Claude help plan each day (costs ~3 calls per day)
    """
    t0 = time.time()
    s = agent_settings or AgentSettings()
    book = Playbook()
    current_gate = gate_scores(book, gate_days, s)
    versions = [snapshot(book, {"day": 0, "gate": _short(current_gate), "change": "empty playbook"})]
    timeline = []
    for d, day in enumerate(days, start=1):
        label = f"day {d}"
        # 1. generator
        r = plan_with_agent(day, playbook=book, settings=s, llm=llm if llm_in_generator else None,
                            use_llm=llm_in_generator and llm is not None)
        # 2. outcome in the simulated world (each day has its own luck)
        actual = simulate_actual(day, [r.plan], realization=100 + d)[0]
        out = score_outcome(day, r.plan, actual)
        for l in book.lessons:                                     # Reflexion bookkeeping
            if l.id in r.plan.lessons_used:
                l.uses += 1
                l.helped += int(out["workers_alerted"] == 0)
        # 3. reflector, 4. curator
        facts = find_facts(day, r.plan, r.check.trace, actual, out)
        refl = reflect(llm, label, facts)
        ops, curator = curate(llm, book, refl, facts, label)
        # 5. gate, ONE update at a time: each lesson change is kept only if it helps on its own
        #    (on top of the ones kept before it). A weak lesson can't ride along with a good one.
        op_log, gates = [], []
        for op in ops:
            cand, log1 = apply_ops(book, [op], label)
            for o in log1:
                o["by"] = op.get("by", "?")
            op_log += log1
            changed = [o for o in log1 if o["result"] in ("applied", "merged near-duplicate")]
            if not changed:
                continue
            g = gate_scores(cand, gate_days, s)
            gain = g["score"] - current_gate["score"]
            keep = g["safety_violations"] == 0 and gain >= settings.ACE_MIN_GAIN
            gates.append({"op": changed[0], "before": _short(current_gate), "after": _short(g), "gain": round(gain, 4),
                          "kept": keep, "why": ("better on held-out days, zero violations" if keep else
                                                "rule-checker violation" if g["safety_violations"] else
                                                "not better on held-out days: rolled back")})
            log1[-1]["gate"] = "kept" if keep else "rolled back"
            if keep:
                cand.version = book.version + 1
                cand.history = book.history + [{"version": cand.version, "day": d, "ops": changed,
                                                "gate_gain": round(gain, 4)}]
                book, current_gate = cand, g
                versions.append(snapshot(book, {"day": d, "gate": _short(g), "change": changed}))
        entry = {"day": d, "scenario": day.id, "tags": day.tags, "outcome": _short(out),
                 "alerts": facts["alerts"], "lessons_used": r.plan.lessons_used, "reflection": refl,
                 "curator": curator, "operations": op_log, "safety_violations": r.check.n_safety, "gates": gates}
        # day summary: kept if any update was kept
        entry["gate"] = None if not gates else {
            "before": gates[0]["before"], "after": _short(current_gate) if any(x["kept"] for x in gates) else gates[-1]["after"],
            "gain": round(sum(x["gain"] for x in gates if x["kept"]) if any(x["kept"] for x in gates)
                          else max(x["gain"] for x in gates), 4),
            "kept": any(x["kept"] for x in gates),
            "why": f"{sum(x['kept'] for x in gates)} of {len(gates)} updates kept (each gated on its own)"}
        entry["playbook_version"] = book.version
        entry["n_lessons"] = len(book.lessons)
        timeline.append(entry)
        if verbose:
            gtxt = "" if not entry["gate"] else (f" gate {entry['gate']['gain']:+.3f} -> "
                                                 f"{sum(x['kept'] for x in gates)}/{len(gates)} kept")
            print(f"  {label:6s} {day.id} score {out['score']:.3f} alerts {out['workers_alerted']} "
                  f"used {r.plan.lessons_used} ops {[o['op'] + ':' + o['result'] for o in op_log]}{gtxt} "
                  f"-> v{book.version} ({len(book.lessons)} lessons)")
    if save_versions:
        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        for v in versions:
            (VERSIONS_DIR / f"v{v['version']}.json").write_text(json.dumps(v, indent=1, default=str))
    return {"playbook": book, "timeline": timeline, "versions": versions, "final_gate": _short(current_gate),
            "llm": llm_summary(llm), "seconds": round(time.time() - t0, 1)}


def restore(version: int) -> Playbook:
    """Roll the playbook back to any saved version."""
    v = json.loads((VERSIONS_DIR / f"v{version}.json").read_text())
    return Playbook([Lesson(**l) for l in v["lessons"]], version=v["version"])


def _short(o: dict) -> dict:
    keys = ("score", "throughput", "actual_peak_c", "strain_margin_c", "workers_alerted", "solve_rate",
            "safety_violations", "heat_spread")
    return {k: o[k] for k in keys if k in o}


def ace_days(n_days: int = settings.ACE_DAYS, n_gate: int = settings.ACE_GATE_DAYS):
    """The home crew's simulated days: learning days from the bank's train split, gate days from its val split."""
    from backend.learning.curriculum import bank_scenarios, load_bank
    train = load_bank(split="train", crew="home")
    val = load_bank(split="val", crew="home")
    if len(train) < n_days or len(val) < n_gate:
        raise SystemExit(f"Bank has {len(train)} home train days and {len(val)} home val days; need {n_days} and "
                         f"{n_gate}. Run: python -m backend.learning.curriculum")
    return bank_scenarios(train[:n_days]), bank_scenarios(val[:n_gate])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=settings.ACE_DAYS)
    ap.add_argument("--llm", action="store_true", help="Claude writes reflections and curates (needs a key)")
    ap.add_argument("--llm-generator", action="store_true", help="also let Claude help plan each day")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--budget", type=float, default=settings.BUDGET_ACE_USD)
    ap.add_argument("--weights", action="store_true",
                    help="plan on top of the weight search's best PlannerWeights (backend/learning/best_weights.json)")
    args = ap.parse_args()
    llm = FakeLLM() if args.fake else (get_budget_llm(args.budget, "ace") if (args.llm or args.llm_generator) else None)
    days, gate = ace_days(args.days)
    print(f"ACE: {len(days)} learning days, {len(gate)} gate days. Reflector/curator: {'Claude' if llm else 'code'}")
    agent = None
    if args.weights:
        from backend.planner.weights import PlannerWeights
        agent = AgentSettings(weights=PlannerWeights.from_dict(
            json.loads((settings.HERE / "best_weights.json").read_text())["weights"]))
    res = run_ace(days, gate, llm=llm, agent_settings=agent, llm_in_generator=args.llm_generator)
    book = res["playbook"]
    book.save(settings.PLAYBOOK_PATH)
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / "ace_run.json").write_text(json.dumps(
        {k: v for k, v in res.items() if k != "playbook"}, indent=1, default=str))
    print(f"\nPlaybook v{book.version}, {len(book.lessons)} lessons -> {settings.PLAYBOOK_PATH}")
    for l in book.lessons:
        print(f"  [{l.id}] {l.text}  (from {l.source}, used {l.uses}x)")
    print(f"Gate days: {res['versions'][0]['gate']} -> {res['final_gate']}")
    print(f"LLM: {res['llm']}, {res['seconds']:.0f}s")
