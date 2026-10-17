"""
Demo artifacts for the frontend (session E). Plain JSON in backend/learning/demo/:

  playbook_growth.json   the playbook over 14 simulated days: lessons, gate decisions, rollbacks
  family_tree.json       every agent version from Loop 3 with its scores, parent and edit
  day1_vs_day14.json     the same heat-wave day planned by the day-1 and the day-14 agent,
                         worked in the simulated world, side by side
  summary.json           headline numbers, API cost, and safety violations per version (all 0)

Shapes: docs/interfaces/agent-learning.md.

Run (after the loops):  python -m backend.learning.demo
"""

import json

import numpy as np

from backend import config
from backend.learning import settings
from backend.learning.report import load_evolved, load_playbook
from backend.learning.scenarios import build
from backend.learning.world import score_outcome, simulate_actual
from backend.planner.archon import plan_with_agent
from backend.planner.scenario import REST

# Candidate demo heat waves: our home crew, a heat dome with lots of heavy outdoor work.
# None is in the training bank (checked below), so neither loop learned from them. We run all
# of them, show the clearest one, and list every candidate's result in the JSON (nothing hidden).
DEMO_CANDIDATES = [{"seed": s, "crew": "home", "heat_c": h, "heavy_bias": 0.6, "demand": 0.75}
                   for s, h in ((4, 5.0), (41, 4.0), (42, 4.5), (43, 3.5), (44, 5.0), (45, 4.0), (46, 3.0), (47, 4.5))]
TASK_KIND = {"light": "light", "moderate": "moderate", "heavy": "heavy", "very_heavy": "heavy"}


def _read(name):
    p = settings.DATA_DIR / name
    return json.loads(p.read_text()) if p.exists() else None


def playbook_growth(ace_run: dict) -> dict:
    days = []
    for e in ace_run["timeline"]:
        days.append({
            "day": e["day"], "scenario": e["scenario"], "tags": [t for t in e["tags"] if t != "home_crew"],
            "outcome_score": e["outcome"]["score"], "workers_alerted": e["outcome"]["workers_alerted"],
            "lessons_used": e["lessons_used"], "root_cause": e["reflection"]["root_cause"],
            "reflection_by": e["reflection"]["by"],
            "proposed": [{"op": o["op"], "text": o.get("text", ""), "result": o["result"], "why": o.get("why", "")}
                         for o in e["operations"]],
            "gate": None if not e["gate"] else {"kept": e["gate"]["kept"], "gain": e["gate"]["gain"],
                                                "why": e["gate"]["why"],
                                                "score_before": e["gate"]["before"]["score"],
                                                "score_after": e["gate"]["after"]["score"],
                                                "safety_violations": e["gate"]["after"]["safety_violations"]},
            "updates": [{"op": g["op"]["op"], "text": g["op"].get("text", ""), "by": g["op"].get("by", "?"),
                         "kept": g["kept"], "gain": g["gain"]}
                        for g in e.get("gates", [])],
            "playbook_version": e["playbook_version"], "n_lessons": e["n_lessons"],
            "safety_violations": e["safety_violations"],
        })
    versions = [{"version": v["version"], "day": v["day"], "gate_score": v["gate"]["score"],
                 "gate_safety_violations": v["gate"]["safety_violations"],
                 "lessons": [{"id": l["id"], "text": l["text"], "source": l["source"]} for l in v["lessons"]]}
                for v in ace_run["versions"]]
    book = load_playbook()
    return {"label": "Simulated days (not real shifts)", "days": days, "versions": versions,
            "final_lessons": [{"id": l.id, "text": l.text, "hint": l.hint, "learned": l.source.split(";")[0],
                               "uses": l.uses, "helped": l.helped} for l in (book.lessons if book else [])],
            "llm": ace_run["llm"]}


def family_tree(gepa_run: dict) -> dict:
    nodes = []
    front = set(gepa_run["pareto_front"])
    for c in gepa_run["archive"]:
        s = c["genome"]["settings"]
        nodes.append({
            "id": c["id"], "parent": c["parent"], "generation": c["generation"], "accepted": c["accepted"],
            "pareto": c["id"] in front, "best": c["id"] == gepa_run["best"], "edit": c["why"],
            "mutator": c.get("mutator", "seed"),
            "minibatch": c.get("minibatch"),
            "scores": None if not c["val"] else {k: c["val"][k] for k in ("score", "strain_margin_c", "throughput",
                                                                          "fairness", "latency", "cost",
                                                                          "workers_alerted", "safety_violations")},
            "settings": {k: s[k] for k in ("objectives", "fuse", "w_peak", "w_fair", "w_complaint", "n_llm_ideas")},
            "strategy_note": c["genome"]["strategy_note"],
        })
    return {"nodes": nodes, "best": gepa_run["best"], "seed": gepa_run["seed"], "pareto_front": gepa_run["pareto_front"],
            "gain_val": gepa_run["gain_val"], "llm": gepa_run["llm"],
            "metrics_note": "Validation scenarios only (never shown to the mutator). fairness = -heat spread, "
                            "latency = -seconds, cost = -dollars, so higher is better for all."}


def _schedule(sc, plan, check, actual):
    rows = []
    for w in sc.crew:
        slots = []
        for k, a in enumerate(plan.assignments[w.id]):
            if a == REST:
                kind, label = ("off", "Off site") if w.available == [] else (("stop_work", "Stop work") if k in
                                                                              sc.stop_work_slots() else ("break", "Shade break"))
            else:
                t = sc.task(a)
                kind, label = TASK_KIND[t.intensity], t.name
            slots.append({"slot": k, "time": sc.slot_time(k), "task": kind, "label": label})
        rows.append({"worker_id": w.id, "name": w.name, "slots": slots,
                     "planned_peak_p90_c": round(float(check.trace[w.id][:, 2].max()), 2),
                     "simulated_peak_c": round(float(actual[w.id].max()), 2),
                     "simulated_alert_minutes": int((actual[w.id] >= config.HEAT_WARNING_C).sum())})
    return rows


def _side(sc, playbook, agent_settings, label):
    r = plan_with_agent(sc, playbook=playbook, settings=agent_settings, use_llm=False)
    actual = simulate_actual(sc, [r.plan], 0)[0]
    out = score_outcome(sc, r.plan, actual)
    lessons = {l.id: l for l in (playbook.lessons if playbook else [])}
    focus = next(w for w in sc.crew if w.name.startswith("Kofi"))
    curve = actual[focus.id][::10]
    return {
        "label": label,
        "schedule": _schedule(sc, r.plan, r.check, actual),
        "lessons_cited": [{"id": lid, "text": lessons[lid].text, "learned": lessons[lid].source.split(";")[0]}
                          for lid in r.plan.lessons_used if lid in lessons],
        "outcome": {k: out[k] for k in ("score", "throughput", "actual_peak_c", "workers_alerted", "priority1_done")},
        "safety_violations": r.check.n_safety,
        "rule_checker_ok": r.check.ok,
        "focus_worker": {"id": focus.id, "name": focus.name,
                         "heavy_or_moderate_after_11": [s["time"] for s in _schedule(sc, r.plan, r.check, actual)
                                                        [sc.crew.index(focus)]["slots"]
                                                        if s["task"] in ("moderate", "heavy") and s["time"] >= "11:00"],
                         "simulated_core_c_every_10_min": [round(float(x), 3) for x in curve],
                         "planned_p90_c_every_10_min": [round(float(x), 3) for x in r.check.trace[focus.id][::10, 2]],
                         "peak_c": round(float(actual[focus.id].max()), 3)},
        "reasons": r.reasons,
    }


def _candidate_job(spec):
    sc = build(spec)
    return {"spec": spec, "id": sc.id,
            "day1": _side(sc, None, None, "Day 1 agent (empty playbook)"),
            "day14": _side(sc, load_playbook(), None, "Day 14 agent (learned playbook)"),
            "day14_evolved": _side(sc, load_playbook(), load_evolved(), "Day 14 agent + evolved settings (Loop 3)")}


def day1_vs_day14(bank_ids: set) -> dict:
    from backend.learning.parallel import pmap
    for spec in DEMO_CANDIDATES:
        assert build(spec).id not in bank_ids, "a demo day must not be a training day"
    runs = pmap(_candidate_job, DEMO_CANDIDATES)
    gain = lambda r: (r["day14"]["outcome"]["score"] - r["day1"]["outcome"]["score"],
                      r["day1"]["focus_worker"]["peak_c"] - r["day14"]["focus_worker"]["peak_c"])
    # Only days where the day-14 plan actually used a lesson, so the difference is the lesson's doing.
    cited = [r for r in runs if r["day14"]["lessons_cited"]] or runs
    pick = max(cited, key=gain)
    sc = build(pick["spec"])
    day1, day14 = pick["day1"], pick["day14"]
    out = {"scenario": {"id": sc.id, "site": sc.site["name"], "date": sc.date, "tags": sc.tags,
                        "max_wbgt_c": round(max(w.wbgt for w in sc.weather), 1),
                        "max_air_c": round(max(w.tdb for w in sc.weather), 1),
                        "hazards": [h.note or h.kind for h in sc.hazards]},
           "note": "Simulated day, held out from all learning. Same scenario, same simulated weather and people.",
           "day1": day1, "day14": day14,
           "difference": {"focus_peak_drop_c": round(day1["focus_worker"]["peak_c"] - day14["focus_worker"]["peak_c"], 3),
                          "score_gain": round(day14["outcome"]["score"] - day1["outcome"]["score"], 4),
                          "alerts_avoided": day1["outcome"]["workers_alerted"] - day14["outcome"]["workers_alerted"]}}
    out["day14_evolved"] = pick["day14_evolved"]
    out["candidates"] = [{"id": r["id"], "spec": r["spec"], **{k: {"score": r[k]["outcome"]["score"],
                          "workers_alerted": r[k]["outcome"]["workers_alerted"], "focus_peak_c": r[k]["focus_worker"]["peak_c"],
                          "safety_violations": r[k]["safety_violations"]} for k in ("day1", "day14", "day14_evolved")}}
                         for r in runs]
    out["candidates_summary"] = summarize_candidates(out["candidates"])
    out["pick_rule"] = (f"Shown: the clearest of {len(runs)} held-out heat-wave days (largest day-14 score gain among days where the day-14 plan used a lesson). "
                        "All candidates are listed in 'candidates'.")
    return out


def summarize_candidates(cands: list[dict]) -> dict:
    """How day 14 did on ALL candidate days, not just the one shown (so nobody oversells the demo)."""
    import numpy as np
    d = [c["day14"]["score"] - c["day1"]["score"] for c in cands]
    return {"n": len(cands), "day14_better": sum(x > 0.005 for x in d), "day14_worse": sum(x < -0.005 for x in d),
            "same": sum(abs(x) <= 0.005 for x in d),
            "mean_score_day1": round(float(np.mean([c["day1"]["score"] for c in cands])), 4),
            "mean_score_day14": round(float(np.mean([c["day14"]["score"] for c in cands])), 4),
            "mean_score_day14_evolved": round(float(np.mean([c["day14_evolved"]["score"] for c in cands])), 4),
            "alerts_day1": sum(c["day1"]["workers_alerted"] for c in cands),
            "alerts_day14": sum(c["day14"]["workers_alerted"] for c in cands)}


def build_all() -> dict:
    settings.DEMO_DIR.mkdir(parents=True, exist_ok=True)
    ace_run, gepa_run, report = _read("ace_run.json"), _read("gepa_run.json"), _read("report.json")
    bank = json.loads(settings.BANK_PATH.read_text())
    written = {}
    if ace_run:
        written["playbook_growth"] = playbook_growth(ace_run)
    if gepa_run:
        written["family_tree"] = family_tree(gepa_run)
    written["day1_vs_day14"] = day1_vs_day14({r["id"] for r in bank["bank"]})
    viol = {"ace_days": sum(d["safety_violations"] for d in written.get("playbook_growth", {}).get("days", [])),
            "ace_gates": sum((d["gate"] or {}).get("safety_violations", 0)
                             for d in written.get("playbook_growth", {}).get("days", [])),
            "gepa_versions": sum((n["scores"] or {}).get("safety_violations", 0)
                                 for n in written.get("family_tree", {}).get("nodes", [])),
            "demo_days": sum(c[k]["safety_violations"] for c in written["day1_vs_day14"]["candidates"]
                             for k in ("day1", "day14", "day14_evolved"))}
    written["summary"] = {
        "safety_violations": viol, "all_zero": all(v == 0 for v in viol.values()),
        "demo_days": written["day1_vs_day14"]["candidates_summary"],
        "curriculum": {"kept": bank["n_kept"], "tried": bank["n_tried"], "llm": bank["llm"]},
        "ace": None if not ace_run else {"days": len(ace_run["timeline"]), "final_version": ace_run["versions"][-1]["version"],
                                         "gate_score_start": ace_run["versions"][0]["gate"]["score"],
                                         "gate_score_end": ace_run["final_gate"]["score"], "llm": ace_run["llm"]},
        "gepa": None if not gepa_run else {"versions": len(gepa_run["archive"]), "best": gepa_run["best"],
                                           "gain_val": gepa_run["gain_val"], "llm": gepa_run["llm"]},
        "held_out": None if not report else {s: {p: {k: v[k] for k in ("success_rate", "safety_violations", "throughput",
                                                                         "world_score", "world_alerted_per_day")}
                                                 for p, v in res.items() if not v.get("skipped")}
                                             for s, res in report.items()},
    }
    for name, data in written.items():
        (settings.DEMO_DIR / f"{name}.json").write_text(json.dumps(data, indent=1, default=float))
    return written


if __name__ == "__main__":
    w = build_all()
    d = w["day1_vs_day14"]
    print(f"Demo day {d['scenario']['id']}: {d['day1']['focus_worker']['name']} peak "
          f"{d['day1']['focus_worker']['peak_c']} -> {d['day14']['focus_worker']['peak_c']} °C, "
          f"score {d['day1']['outcome']['score']} -> {d['day14']['outcome']['score']}, lessons cited "
          f"{[l['id'] for l in d['day14']['lessons_cited']]}")
    print("Safety violations:", w["summary"]["safety_violations"], "all zero:", w["summary"]["all_zero"])
    print(f"Wrote {', '.join(w)} to {settings.DEMO_DIR}")
