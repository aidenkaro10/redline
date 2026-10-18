"""
The nightly learning loop: the system "learns from yesterday", one simulated day at a time.

Every simulated day d (14 days, our home crew, everything labeled SIMULATED):

  1. DAY      The crew works the plan made last night at 6 PM. What "really" happens comes from
              the simulated world (world.py): each worker has hidden traits the planner doesn't
              know (a personal offset; Kofi tires from 11:00; Marcus runs hot after lunch), the
              weather misses the forecast.
  2. LEARN    a. Worker profiles: thermometer spot checks at every break compare the model to the
                 worker's real temperature. The personal offset learns exactly like
                 backend/models/personal_offset.py (same shrinkage, same 0.6 °C cap). Plus a
                 per-task-intensity heat bias (how much faster than forecast they heat up on
                 light / moderate / heavy work), shrunk the same way.
                 SAFETY: the planner only ever uses the "runs hotter" side of a profile. A worker
                 who runs cooler never gets more heat because of it (no learned tolerance).
              b. ACE reflects on the real day and curates lessons, gated on held-out days (ace.py).
  3. NIGHT    Monte Carlo: N=30 variations of TOMORROW, built from tomorrow's forecast plus realistic
              surprises: weather error sized from the forecast misses seen so far, person variation
              from the profiles, and an early storm now and then.
              Practice: the agent plans a handful of variations; the curriculum keeps the ones it
              solves sometimes (0 < solve rate < 1). GEPA does one short round on those and must
              beat the current agent on held-out variations it didn't practice on (gepa.py pieces).
  4. 6 PM     Tomorrow's plan + a risk number ("k of 30 variations had someone above the yellow
              line"), the weak spot (worker + time), and the lessons it used.

Hard lines (unchanged): limits live in backend/config.py; every plan from every version goes through
B's rule checker; Claude only proposes (lessons, setting edits); code filters and gates decide.

Run:  python -m backend.learning.nightly --llm        (Claude for ACE + GEPA mutations, hard cap $5)
      python -m backend.learning.nightly              (no key: plain-code reflector/curator/mutator, $0)
Report + demo JSON: python -m backend.learning.nightly --report
"""

import argparse
import copy
import json
import time
from dataclasses import asdict, replace

import numpy as np

from backend import config
from backend.learning import settings
from backend.learning.ace import (_short, apply_ops, curate, find_facts, gate_scores, reflect)
from backend.learning.fake_llm import FakeLLM
from backend.learning.gepa import clamp, code_mutate, llm_mutate, seed_genome, to_settings
from backend.learning.llm_budget import get_budget_llm, llm_summary
from backend.learning.parallel import pmap
from backend.learning.scenarios import build
from backend.learning.world import (actual_weather, score_outcome, simulate_actual)
from backend.models.personal_offset import MAX_OFFSET_C, PRIOR_CHECKS, PersonalOffset
from backend.planner.archon import plan_with_agent
from backend.planner.heat_model import PHSHeatModel, activity_of, met_shade
from backend.planner.playbook import Lesson, Playbook
from backend.planner.rules import check_plans
from backend.planner.scenario import REST, HazardWindow, Plan

OUT_DIR = settings.DATA_DIR / "nightly"
N_VARIATIONS = 30           # Monte Carlo variations of tomorrow (risk number)
N_PRACTICE = 6              # variations the agent actually plans and practices on each night
N_GEPA_VAL = 3              # extra variations GEPA must win on (never practiced)
PRACTICE_DRAWS = 3          # residual luck draws per practice variation -> solve rate
EARLY_STORM_P = 0.15        # chance a variation gets a storm 1-2 h earlier than forecast (or a surprise one)
THERMOMETER_SD_C = 0.1      # spot-check thermometer noise
PRIOR_WEATHER_SD_C = 1.2    # before we've seen any forecast misses
INTENSITIES = ("light", "moderate", "heavy", "very_heavy")


# ---------------------------------------------------------------------------
# Worker profiles
# ---------------------------------------------------------------------------
def new_profiles(crew) -> dict:
    return {w.name.split(" ")[0]: {"misses": [], "bias": {i: [] for i in INTENSITIES}} for w in crew}


def _key(w) -> str:
    return w.name.split(" ")[0]


def profile_view(p: dict) -> dict:
    """The learned numbers, shrunk like personal_offset.py."""
    po = PersonalOffset("x", list(p["misses"]))
    runs_hotter = -po.offset_c                     # PersonalOffset: model minus truth. Hotter person = negative miss.
    bias = {}
    for i, xs in p["bias"].items():
        raw = sum(xs) / (len(xs) + PRIOR_CHECKS) if xs else 0.0
        bias[i] = float(np.clip(raw, -0.3, 0.3))   # °C extra per 30-min slot of that kind of work, capped
    return {"runs_hotter_c": round(runs_hotter, 3), "confidence": round(po.confidence, 3), "checks": len(p["misses"]),
            "heat_bias_c_per_slot": {k: round(v, 3) for k, v in bias.items()}}


def apply_profiles(scenario, profiles: dict):
    """Planning copy of a scenario: each worker's offset = the HOTTER side of their profile only."""
    sc = replace(scenario, crew=[replace(w) for w in scenario.crew])
    for w in sc.crew:
        p = profiles.get(_key(w))
        if p:
            hot = max(0.0, profile_view(p)["runs_hotter_c"])         # never use "runs cooler" to add heat
            w.core_offset_c = min(MAX_OFFSET_C, w.core_offset_c + hot)
    return sc


def learn_profiles(profiles: dict, plan_sc, plan, planned_trace, actual, rng) -> list[dict]:
    """Spot checks at every break (and shift end) + per-intensity excess heating. Returns what was learned."""
    sm = plan_sc.slot_min
    notes = []
    for w in plan_sc.crew:
        if w.available == [] or _key(w) not in profiles:
            continue
        p = profiles[_key(w)]
        base_p50 = planned_trace[w.id][:, 1] - w.core_offset_c       # the plain model, before this profile
        row = plan.assignments[w.id]
        check_minutes = [k * sm + sm // 2 for k, a in enumerate(row) if a == REST and k > 0] + [len(base_p50) - 1]
        for m in check_minutes[:6]:
            thermo = float(actual[w.id][m] + rng.normal(0, THERMOMETER_SD_C))
            PersonalOffset("x", p["misses"]).add_check(float(base_p50[m]), thermo)   # appends to p["misses"], same bad-reading filter
        a, pl = actual[w.id], planned_trace[w.id][:, 1]
        for k, t in enumerate(row):
            if t == REST or not w.is_available(k):
                continue
            lo, hi = k * sm, (k + 1) * sm - 1
            ex = float((a[hi] - (a[lo - 1] if lo else a[0])) - (pl[hi] - (pl[lo - 1] if lo else pl[0])))
            p["bias"][plan_sc.task(t).intensity].append(ex)
        notes.append({"worker": _key(w), **profile_view(p)})
    return notes


# ---------------------------------------------------------------------------
# Belief simulation: what the AGENT thinks could happen (planning PHS model + profiles)
# ---------------------------------------------------------------------------
def make_variations(tomorrow, profiles: dict, weather_sd: float, n: int, rng) -> list[dict]:
    """Each variation: weather error, early storm (or none), each worker's offset + per-intensity bias."""
    out = []
    for v in range(n):
        storm = None
        if rng.uniform() < EARLY_STORM_P:
            light = [h for h in tomorrow.hazards if h.kind == "lightning"]
            start = (light[0].start_slot - int(rng.integers(2, 5))) if light else int(rng.integers(8, 14))
            storm = (max(1, start), max(1, start) + int(rng.integers(1, 3)))
        people = {}
        for w in tomorrow.crew:
            pv = profile_view(profiles[_key(w)]) if _key(w) in profiles else {"runs_hotter_c": 0, "confidence": 0,
                                                                                  "heat_bias_c_per_slot": {}}
            sd = 0.05 + 0.2 * (1 - pv["confidence"])       # less sure about people we've seen less
            people[w.id] = {"offset": float(rng.normal(pv["runs_hotter_c"], sd)),
                            "bias": {i: float(b + rng.normal(0, 0.02)) for i, b in pv["heat_bias_c_per_slot"].items()}}
        out.append({"weather_err": float(rng.normal(0, weather_sd)), "early_storm": storm, "people": people})
    return out


def _storm_plan(sc, plan, storm):
    """In a variation with an early storm, outdoor work in that window stops (the crew rests)."""
    if not storm:
        return plan
    a = {wid: list(row) for wid, row in plan.assignments.items()}
    for row in a.values():
        for k in range(storm[0], min(sc.n_slots, storm[1] + 1)):      # +1 slot = 30-min all-clear
            if row[k] != REST and sc.task(row[k]).outdoor:
                row[k] = REST
    return Plan(a, source=plan.source)


def belief_simulate(sc, plan, variations: list[dict]) -> list[dict[str, np.ndarray]]:
    """Core temp per worker per minute in each variation (one PHS run for all of them)."""
    model = PHSHeatModel(n_members=1)
    rows, plans = [], []
    for vi, var in enumerate(variations):
        p = _storm_plan(sc, plan, var["early_storm"])
        plans.append(p)
        for w in sc.crew:
            slots = p.assignments.get(w.id, [REST] * sc.n_slots)
            acts = [activity_of(sc, w, s, slots[s]) for s in range(sc.n_slots)]
            ms = np.array([met_shade(x) for x in acts])
            outdoor = np.array([x != REST and x[1] for x in acts], float)
            rows.append({"plan": vi, "worker": w, "met": ms[:, 0], "shade": ms[:, 1], "indoor": 1 - outdoor,
                         "dtdb": var["weather_err"], "clo": w.clo,
                         "offset": var["people"][w.id]["offset"],      # what we believe this person's real offset could be
                         "acts": acts})
    core = model._run(sc, rows, sc.n_slots * sc.slot_min)
    out = [{} for _ in variations]
    sm = sc.slot_min
    for r, c in zip(rows, core):
        c = c.copy()
        bias = variations[r["plan"]]["people"][r["worker"].id]["bias"]
        ramp = np.zeros_like(c)
        for k, a in enumerate(r["acts"]):
            if a != REST and bias.get(a[0]):
                ramp[k * sm:(k + 1) * sm] += np.linspace(0, bias[a[0]], sm)
                ramp[(k + 1) * sm:] += bias[a[0]]
        out[r["plan"]][r["worker"].id] = c + ramp
    return out, plans


def risk_report(sc, plan, variations: list[dict]) -> dict:
    """The 6 PM risk number: k of N variations had anyone above the yellow line. Plus the weak spot."""
    sims, vplans = belief_simulate(sc, plan, variations)
    k, spots = 0, {}
    lost_work = []
    for sim, vp in zip(sims, vplans):
        over = {wid: np.nonzero(tr >= config.HEAT_WARNING_C)[0] for wid, tr in sim.items()}
        if any(len(m) for m in over.values()):
            k += 1
            for wid, m in over.items():
                if len(m):
                    key = (wid, sc.slot_time(int(m[0]) // sc.slot_min))
                    spots[key] = spots.get(key, 0) + 1
        done = vp.units_done(sc)
        lost_work.append(sum(plan.units_done(sc).values()) - sum(done.values()))
    if spots:
        (wid, t), cnt = max(spots.items(), key=lambda kv: kv[1])
        weak = {"worker": sc.worker(wid).name, "time": t, "variations": cnt}
    else:   # nobody crosses: the closest call
        peaks = {(wid, int(np.argmax(tr))): float(tr.max()) for sim in sims for wid, tr in sim.items()}
        (wid, m), pk = max(peaks.items(), key=lambda kv: kv[1])
        weak = {"worker": sc.worker(wid).name, "time": sc.slot_time(m // sc.slot_min), "variations": 0,
                "closest_peak_c": round(pk, 2)}
    return {"risk_k": k, "risk_n": len(variations), "risk": round(k / len(variations), 3), "weak_spot": weak,
            "storm_variations": sum(v["early_storm"] is not None for v in variations),
            "mean_units_lost_to_storms": round(float(np.mean(lost_work)), 2)}


def variation_scenario(tomorrow, var: dict, idx: int):
    """A practice copy of tomorrow: its weather (incl. the error) and storm are what the agent sees."""
    sc = replace(tomorrow, id=f"{tomorrow.id}|var{idx}", crew=[replace(w) for w in tomorrow.crew],
                 weather=[replace(w, tdb=round(w.tdb + var["weather_err"], 2), wbgt=round(w.wbgt + 0.7 * var["weather_err"], 2))
                          for w in tomorrow.weather],
                 hazards=list(tomorrow.hazards))
    if var["early_storm"]:
        s, e = var["early_storm"]
        sc.hazards.append(HazardWindow("lightning", s, min(sc.n_slots, e), source="variation", note="early storm"))
    return sc


def practice_outcomes(vsc, plan, var: dict, draws: int, seed: int) -> list[dict]:
    """Score a plan on one variation under the agent's belief, with a little residual luck per draw."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(draws):
        v = copy.deepcopy(var)
        v["early_storm"] = None                     # already in vsc's hazards
        v["weather_err"] = float(rng.normal(0, 0.3))
        for p in v["people"].values():
            p["offset"] += float(rng.normal(0, 0.05))
        sims, _ = belief_simulate(vsc, plan, [v])
        out.append(score_outcome(vsc, plan, sims[0]))
    return out


# ---------------------------------------------------------------------------
# Parallel jobs (top level so they can run in worker processes)
# ---------------------------------------------------------------------------
def _plan_job(args):
    sc, book, genome = args
    r = plan_with_agent(sc, playbook=book, settings=to_settings(genome), use_llm=False)
    return r.plan, r.check.n_safety, r.check.trace, r.log[-6:], [
        {"name": c.name, "verified": bool(c.check and c.check.ok), "throughput": c.score.get("throughput"),
         "peak_p90": c.score.get("peak_p90"), "rank": c.score.get("rank")} for c in r.candidates][:8]


def _practice_job(args):
    vsc, var, book, genome, seed = args
    plan, n_safety, trace, log, cands = _plan_job((vsc, book, genome))
    outs = practice_outcomes(vsc, plan, var, PRACTICE_DRAWS, seed)
    sims, _ = belief_simulate(vsc, plan, [dict(var, early_storm=None)])
    facts = find_facts(vsc, plan, trace, sims[0], outs[0])
    done = plan.units_done(vsc)
    return {"scenario": vsc.id, "tags": vsc.tags, "safety_violations": n_safety,
            "solve_rate": float(np.mean([o["solved"] and n_safety == 0 for o in outs])),
            "score": float(np.mean([o["score"] for o in outs])), "throughput": float(np.mean([o["throughput"] for o in outs])),
            "workers_alerted": float(np.mean([o["workers_alerted"] for o in outs])),
            "actual_peak_c": float(np.mean([o["actual_peak_c"] for o in outs])),
            "heat_spread": float(np.mean([o["heat_spread"] for o in outs])),
            "trace": {"agent_log": log, "facts": facts, "candidates": cands,
                      "work_left": [{"task": t.name, "priority": t.priority, "intensity": t.intensity,
                                     "left": t.units - done[t.id]} for t in vsc.tasks if done[t.id] < t.units][:6]}}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def state_snapshot(day, profiles, book, genome) -> dict:
    return {"day": day, "profiles": copy.deepcopy(profiles), "playbook": [asdict(l) for l in book.lessons],
            "playbook_version": book.version, "genome": copy.deepcopy(genome)}


def run_nightly(days: list, gate_days: list, llm=None, verbose: bool = True, seed: int = 0) -> dict:
    """days: the simulated days in order (days[d+1] is 'tomorrow' for night d). gate_days: ACE's held-out days."""
    t0 = time.time()
    rng = np.random.default_rng(seed)
    profiles = new_profiles(days[0].crew)
    book, genome = Playbook(), clamp(seed_genome())
    snapshots = {0: state_snapshot(0, profiles, book, genome)}
    weather_misses = []
    timeline = []
    # Night 0: the day-1 agent makes day 1's plan at 6 PM
    sc0 = apply_profiles(days[0], profiles)
    plan, n_safety, trace, _, _ = _plan_job((sc0, book, genome))
    pending = {"plan_sc": sc0, "plan": plan, "trace": trace, "n_safety": n_safety, "used": plan.lessons_used,
               "risk": risk_report(sc0, plan, make_variations(days[0], profiles, PRIOR_WEATHER_SD_C, N_VARIATIONS, rng))}
    for d, day in enumerate(days, start=1):
        entry = {"day": d, "scenario": day.id, "tags": [t for t in day.tags if t != "home_crew"],
                 "forecast_risk": pending["risk"], "lessons_used": pending["used"],
                 "safety_violations": pending["n_safety"]}
        # 1. DAY: the crew works last night's plan in the simulated world
        actual = simulate_actual(day, [pending["plan"]], realization=200 + d)[0]
        out = score_outcome(day, pending["plan"], actual)
        entry["outcome"] = {k: out[k] for k in ("score", "throughput", "actual_peak_c", "workers_alerted", "priority1_share")}
        entry["outcome"]["minutes_above_yellow"] = int(sum(out["alert_minutes"].values()))
        entry["anyone_above_yellow"] = out["workers_alerted"] > 0
        aw = actual_weather(day, 200 + d)
        weather_misses.append(float(np.mean([a.tdb - f.tdb for a, f in zip(aw, day.weather)])))
        # 2a. LEARN: profiles
        entry["profiles"] = learn_profiles(profiles, pending["plan_sc"], pending["plan"], pending["trace"], actual, rng)
        # 2b. ACE: reflect on the real day, curate, gate each update alone (on profile-aware gate days)
        s = to_settings(genome)
        gdays = [apply_profiles(g, profiles) for g in gate_days]
        facts = find_facts(pending["plan_sc"], pending["plan"], pending["trace"], actual, out)
        if llm is not None:
            llm.purpose = "nightly:ace"
        refl = reflect(llm, f"day {d}", facts)
        ops, _ = curate(llm, book, refl, facts, f"day {d}")
        gates, current = [], None
        for op in ops:
            cand, log1 = apply_ops(book, [op], f"day {d}")
            if not any(o["result"] in ("applied", "merged near-duplicate") for o in log1):
                gates.append({"op": log1[0], "kept": False, "gain": None})
                continue
            current = current or gate_scores(book, gdays, s)
            g = gate_scores(cand, gdays, s)
            gain = g["score"] - current["score"]
            keep = g["safety_violations"] == 0 and gain >= settings.ACE_MIN_GAIN
            gates.append({"op": {**log1[0], "by": op.get("by")}, "kept": keep, "gain": round(gain, 4)})
            if keep:
                cand.version = book.version + 1
                book, current = cand, g
        entry["reflection"] = refl["root_cause"]
        entry["ace"] = gates
        # 3. NIGHT: Monte Carlo of tomorrow, practice, short GEPA round
        tomorrow = days[d] if d < len(days) else None
        if tomorrow is not None:
            wsd = float(np.sqrt((PRIOR_WEATHER_SD_C ** 2 * 2 + np.sum(np.square(weather_misses))) / (2 + len(weather_misses))))
            tsc = apply_profiles(tomorrow, profiles)
            variations = make_variations(tsc, profiles, wsd, N_VARIATIONS + N_PRACTICE + N_GEPA_VAL, rng)
            practice_vars = variations[N_VARIATIONS:N_VARIATIONS + N_PRACTICE]
            val_vars = variations[N_VARIATIONS + N_PRACTICE:]
            vscs = [variation_scenario(tsc, v, i) for i, v in enumerate(practice_vars)]
            prows = pmap(_practice_job, [(vsc, v, book, genome, 1000 * d + i) for i, (vsc, v) in enumerate(zip(vscs, practice_vars))])
            kept = [r for r in prows if 0 < r["solve_rate"] < 1]
            entry["practice"] = {"weather_sd_c": round(wsd, 2), "tried": len(prows), "kept": len(kept),
                                 "solve_rates": [round(r["solve_rate"], 2) for r in prows],
                                 "safety_violations": int(sum(r["safety_violations"] for r in prows))}
            entry["gepa"] = None
            if kept:
                if llm is not None:
                    llm.purpose = "nightly:gepa"
                    child, why = llm_mutate(llm, genome, kept[:3])
                    by = "llm"
                else:
                    child, why = None, ""
                if child is None or child == genome:
                    child, why = code_mutate(genome, kept[:3], rng)
                    by = "code"
                mb = [vscs[i] for i, r in enumerate(prows) if 0 < r["solve_rate"] < 1][:3]
                mb_vars = [practice_vars[i] for i, r in enumerate(prows) if 0 < r["solve_rate"] < 1][:3]
                c_mb = pmap(_practice_job, [(vsc, v, book, child, 2000 * d + i) for i, (vsc, v) in enumerate(zip(mb, mb_vars))])
                p_mb = kept[:3]
                gain_mb = float(np.mean([r["score"] for r in c_mb]) - np.mean([r["score"] for r in p_mb]))
                g = {"by": by, "why": why[:200], "minibatch_gain": round(gain_mb, 4), "accepted": False}
                if gain_mb > 0 and sum(r["safety_violations"] for r in c_mb) == 0:
                    vv = [variation_scenario(tsc, v, 100 + i) for i, v in enumerate(val_vars)]
                    both = pmap(_practice_job, [(vsc, v, book, gg, 3000 * d + i) for gg in (genome, child)
                                                for i, (vsc, v) in enumerate(zip(vv, val_vars))])
                    p_val, c_val = both[:len(vv)], both[len(vv):]
                    gain_val = float(np.mean([r["score"] for r in c_val]) - np.mean([r["score"] for r in p_val]))
                    ok = gain_val > 0.002 and sum(r["safety_violations"] for r in c_val) == 0
                    g.update(heldout_gain=round(gain_val, 4), accepted=ok)
                    if ok:
                        genome = child
                entry["gepa"] = g
            # 4. 6 PM: tomorrow's plan, risk number, weak spot, lessons used
            plan, n_safety, trace, _, _ = _plan_job((tsc, book, genome))
            risk = risk_report(tsc, plan, variations[:N_VARIATIONS])
            pending = {"plan_sc": tsc, "plan": plan, "trace": trace, "n_safety": n_safety, "used": plan.lessons_used, "risk": risk}
            lessons = {l.id: l.text for l in book.lessons}
            entry["six_pm"] = {"for_day": d + 1, "risk_text": f"{risk['risk_k']} of {risk['risk_n']} variations had someone "
                                                         f"above the yellow line", **risk,
                               "lessons_used": [{"id": i, "text": lessons.get(i, "")} for i in plan.lessons_used],
                               "rule_checker_ok": n_safety == 0}
        entry["playbook_version"], entry["n_lessons"] = book.version, len(book.lessons)
        entry["genome_changed"] = genome != snapshots[0]["genome"]
        timeline.append(entry)
        if d in (1, 7, 14) or d == len(days):
            snapshots[d] = state_snapshot(d, profiles, book, genome)
        if verbose:
            gp = entry.get("gepa") or {}
            print(f"  day {d:2d} {day.id} score {out['score']:.3f} alerted {out['workers_alerted']} "
                  f"risk {entry['forecast_risk']['risk']:.2f} | ACE {sum(g['kept'] for g in gates)}/{len(gates)} kept "
                  f"| practice {entry.get('practice', {}).get('kept', '-')}/{entry.get('practice', {}).get('tried', '-')} "
                  f"| GEPA {'accepted' if gp.get('accepted') else ('tried' if gp else '-')} | lessons {len(book.lessons)} "
                  f"| {time.time() - t0:.0f}s", flush=True)
    return {"timeline": timeline, "snapshots": snapshots, "llm": llm_summary(llm), "seconds": round(time.time() - t0, 1),
            "label": "Simulated days (not real shifts)"}


# ---------------------------------------------------------------------------
# Held-out report: day 1 vs day 7 vs day 14 agents
# ---------------------------------------------------------------------------
HELDOUT_REALIZATIONS = range(6)


def _heldout_job(args):
    sc, snap, seed = args
    rng = np.random.default_rng(seed)
    book = Playbook([Lesson(**l) for l in snap["playbook"]])
    psc = apply_profiles(sc, snap["profiles"])
    plan, n_safety, trace, _, _ = _plan_job((psc, book, snap["genome"]))
    risk = risk_report(psc, plan, make_variations(psc, snap["profiles"], PRIOR_WEATHER_SD_C, N_VARIATIONS, rng))
    outs = [score_outcome(sc, plan, simulate_actual(sc, [plan], 300 + r)[0]) for r in HELDOUT_REALIZATIONS]
    return {"scenario": sc.id, "safety_violations": n_safety, "risk": risk["risk"], "weak_spot": risk["weak_spot"],
            "actual_anyone_above_yellow": float(np.mean([o["workers_alerted"] > 0 for o in outs])),
            "workers_alerted": float(np.mean([o["workers_alerted"] for o in outs])),
            "minutes_above_yellow": float(np.mean([sum(o["alert_minutes"].values()) for o in outs])),
            "throughput": float(np.mean([o["throughput"] for o in outs])),
            "score": float(np.mean([o["score"] for o in outs])), "lessons_used": plan.lessons_used,
            "plan": plan.assignments}


def calibration(rows: list[dict]) -> dict:
    """Did ~X% risk days go wrong ~X% of the time? Bins of the risk number vs the actual rate."""
    bins = [(0, 0.1), (0.1, 0.3), (0.3, 0.6), (0.6, 1.01)]
    out = []
    for lo, hi in bins:
        b = [r for r in rows if lo <= r["risk"] < hi]
        if b:
            out.append({"risk_bin": f"{lo:.0%}-{min(hi, 1):.0%}", "days": len(b),
                        "mean_predicted": round(float(np.mean([r["risk"] for r in b])), 3),
                        "actual_rate": round(float(np.mean([r["actual_anyone_above_yellow"] for r in b])), 3)})
    brier = float(np.mean([(r["risk"] - r["actual_anyone_above_yellow"]) ** 2 for r in rows]))
    return {"bins": out, "brier": round(brier, 4)}


def heldout_report(snapshots: dict, heldout: list) -> dict:
    res = {}
    for d, snap in sorted(snapshots.items()):
        if d == 0:
            continue
        label = "day 1" if d == 1 else f"day {d}"
        src = snapshots[0] if d == 1 else snap          # "day 1 agent" = before any learning
        rows = pmap(_heldout_job, [(sc, src, 7000 + i) for i, sc in enumerate(heldout)])
        res[label] = {"n_days": len(rows), "safety_violations": int(sum(r["safety_violations"] for r in rows)),
                      "workers_alerted_per_day": round(float(np.mean([r["workers_alerted"] for r in rows])), 3),
                      "minutes_above_yellow_per_day": round(float(np.mean([r["minutes_above_yellow"] for r in rows])), 1),
                      "work_done": round(float(np.mean([r["throughput"] for r in rows])), 3),
                      "score": round(float(np.mean([r["score"] for r in rows])), 4),
                      "calibration": calibration(rows), "rows": rows}
        print(f"  {label:7s} alerted/day {res[label]['workers_alerted_per_day']} min above yellow "
              f"{res[label]['minutes_above_yellow_per_day']} work {res[label]['work_done']} violations "
              f"{res[label]['safety_violations']} brier {res[label]['calibration']['brier']}", flush=True)
    return res


def heldout_days() -> list:
    """Home-crew days no loop learned from: the bank's home val days + 8 extra-hot heat-wave days + 4 milder ones."""
    from backend.learning.curriculum import bank_scenarios, load_bank
    from backend.learning.demo import DEMO_CANDIDATES
    extra = [{"seed": s, "crew": "home", "heat_c": h, "heavy_bias": 0.4, "demand": 0.7}
             for s, h in ((61, 1.0), (62, 2.0), (63, 0.5), (64, 2.5))]
    return bank_scenarios(load_bank(split="val", crew="home"))[4:] + [build(s) for s in DEMO_CANDIDATES + extra]


def demo_json(run: dict, report: dict, heldout: list) -> dict:
    """Day 1 vs day 14, same crew, same held-out day: plan, risk number, weak spot, lessons, profiles."""
    d1, d14 = report["day 1"]["rows"], report[max(report, key=lambda k: int(k.split()[1]))]["rows"]
    i = int(np.argmax([b["score"] - a["score"] for a, b in zip(d1, d14)]))
    sc = heldout[i]
    side = lambda r: {"risk": r["risk"], "risk_text": f"{round(r['risk'] * N_VARIATIONS)} of {N_VARIATIONS} variations had "
                                                      f"someone above the yellow line", "weak_spot": r["weak_spot"],
                      "actual_anyone_above_yellow_rate": r["actual_anyone_above_yellow"],
                      "workers_alerted": r["workers_alerted"], "minutes_above_yellow": r["minutes_above_yellow"],
                      "work_done": r["throughput"], "score": r["score"], "lessons_used": r["lessons_used"],
                      "safety_violations": r["safety_violations"],
                      "schedule": [{"worker": w.name, "slots": [{"time": sc.slot_time(k), "task": a if a == REST else sc.task(a).intensity,
                                                                  "label": "Shade break" if a == REST else sc.task(a).name}
                                                                 for k, a in enumerate(r["plan"][w.id])]} for w in sc.crew]}
    last = run["snapshots"][max(run["snapshots"], key=int)]
    return {"label": "Simulated days (not real shifts). Same crew, same held-out day.",
            "day": {"id": sc.id, "tags": [t for t in sc.tags if t != "home_crew"]},
            "pick_rule": f"Held-out day with the largest day-14 score gain, of {len(heldout)}; all are in nightly_report.json.",
            "day1": side(d1[i]), "day14": side(d14[i]),
            "profiles_day14": {k: profile_view(v) for k, v in last["profiles"].items()},
            "lessons_day14": [{"id": l["id"], "text": l["text"], "learned": l["source"]} for l in last["playbook"]],
            "summary": {k: {kk: v[kk] for kk in ("workers_alerted_per_day", "minutes_above_yellow_per_day", "work_done",
                                                 "score", "safety_violations")} | {"brier": v["calibration"]["brier"]}
                        for k, v in report.items()}}


if __name__ == "__main__":
    from backend.learning.curriculum import bank_scenarios, load_bank
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--budget", type=float, default=5.0)
    ap.add_argument("--report", action="store_true", help="only the held-out report + demo JSON from the saved run")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not args.report:
        llm = FakeLLM() if args.fake else (get_budget_llm(args.budget, "nightly") if args.llm else None)
        days = bank_scenarios(load_bank(split="train", crew="home"))[:args.days]
        gate = bank_scenarios(load_bank(split="val", crew="home"))[:4]
        print(f"Nightly loop: {len(days)} simulated days, home crew. Claude: {'on, cap $%.2f' % args.budget if llm else 'off'}")
        run = run_nightly(days, gate, llm=llm)
        (OUT_DIR / "run.json").write_text(json.dumps(run, indent=1, default=str))
        print(f"LLM {run['llm']}, {run['seconds']:.0f}s")
    run = json.loads((OUT_DIR / "run.json").read_text())
    run["snapshots"] = {int(k): v for k, v in run["snapshots"].items()}
    heldout = heldout_days()
    print(f"Held-out report on {len(heldout)} home-crew days (never learned from):")
    rep = heldout_report(run["snapshots"], heldout)
    (OUT_DIR / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    settings.DEMO_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DEMO_DIR / "nightly_day1_vs_day14.json").write_text(json.dumps(demo_json(run, rep, heldout), indent=1, default=str))
    print("Saved", OUT_DIR / "report.json", "and", settings.DEMO_DIR / "nightly_day1_vs_day14.json")
