"""
Loop 3: agent evolution, offline (GEPA reflective mutation + Darwin Godel Machine archive).

What evolves: HOW the planner plans. Its strategy settings (backend/planner/archon.py
AgentSettings: which optimizer objectives to try, fusion, ranking weights, how many LLM
ideas, the critic's instructions) plus a free-text "strategy note" added to the planner
agent's Claude prompts. What can't evolve: anything about safety. The rule checker and
the limits in backend/config.py sit outside everything here, every candidate's every plan
goes through the checker, and any violation disqualifies the candidate.

One round:
  1. Pick a parent from the archive: good score + a NOVELTY bonus (far from the others)
     + a bonus for having few children yet (DGM), so the search doesn't get stuck.
  2. Run the parent on a small minibatch of TRAINING scenarios and collect full traces:
     the agent's decision log, who got hot in the simulated day, what work didn't get done.
  3. REFLECTIVE MUTATION (GEPA): Claude reads those traces and proposes one targeted edit
     ("afternoon alerts on new hires -> weigh peak heat more when ranking plans").
     No key: a code mutator makes the same kind of edit from the same traces.
     Code clamps every knob to its allowed range and filters the strategy note.
  4. The child runs on the same minibatch. Only if it beats the parent there is it scored
     on the VALIDATION scenarios and added to the archive.
  5. The archive keeps every accepted variant; the Pareto front is everyone who is best on
     at least one metric: strain margin, throughput, fairness, latency, cost.
  6. Final pick = best validation score with zero violations. Validation scenarios are never
     shown to the mutator. B's held-out test set is only used afterwards, to report.

Run:  python -m backend.learning.gepa                    (no key: code mutator, planner without LLM, $0)
      python -m backend.learning.gepa --llm              (Claude mutator, budget-capped)
      python -m backend.learning.gepa --llm --llm-planner (Claude also inside the planner: much pricier)
      python -m backend.learning.gepa --fake
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields

import numpy as np

from backend.learning import settings
from backend.learning.ace import find_facts
from backend.learning.fake_llm import FakeLLM
from backend.learning.llm_budget import get_budget_llm, llm_summary
from backend.learning.parallel import pmap
from backend.learning.safety import note_ok
from backend.learning.world import mean_outcome, outcomes, simulate_actual, score_outcome
from backend.planner.archon import AgentSettings, plan_with_agent
from backend.planner.llm import extract_json
from backend.planner.optimizer import OBJECTIVES
from backend.planner.rules import check_plans

REALIZATIONS = (0, 1)
# Every knob a mutation may touch, with its allowed range. Anything else is ignored.
KNOBS = {
    "objectives": "subset of " + ", ".join(OBJECTIVES) + " (1-4, the first is also the LLM's starting plan)",
    "use_playbook": "true/false",
    "n_llm_ideas": "0-3 (LLM strategy ideas turned into optimizer runs)",
    "llm_direct_plan": "true/false (the LLM also writes one schedule directly; the checker usually rejects it)",
    "fuse": "true/false (re-solve the best plan with fixes for the critic's confirmed complaints)",
    "w_peak": "0-5 (ranking penalty per °C of predicted peak above 37.5)",
    "w_fair": "0-0.02 (ranking penalty per degree-minute of heat-dose spread)",
    "w_complaint": "0-0.2 (ranking penalty per confirmed critic complaint)",
    "time_limit_s": "1-5 (CP-SAT seconds per solve)",
    "critic_instructions": "text, max 400 chars",
    "weights": "PlannerWeights dict or null (optimizer soft weights: margin, fairness 0-6; budget_margin_c 0-0.3; "
               "priority_values {1,2,3}; storm 0-50; rain 0-30; hot_break_bonus 0-15). Validated by code.",
}
METRICS = ("strain_margin_c", "throughput", "fairness", "latency", "cost")   # all "higher is better" after sign flips


def seed_genome() -> dict:
    s = asdict(AgentSettings())
    s["objectives"] = list(s["objectives"])
    return {"settings": s, "strategy_note": ""}


def clamp(genome: dict) -> dict:
    """Force every knob into its allowed range. This is what makes mutations safe to try."""
    s = {**seed_genome()["settings"], **genome.get("settings", {})}
    objs = [o for o in s.get("objectives", []) if o in OBJECTIVES]
    s["objectives"] = list(dict.fromkeys(objs))[:4] or ["balanced"]
    s["n_llm_ideas"] = int(np.clip(int(s["n_llm_ideas"]), 0, 3))
    s["w_peak"] = float(np.clip(float(s["w_peak"]), 0, 5))
    s["w_fair"] = float(np.clip(float(s["w_fair"]), 0, 0.02))
    s["w_complaint"] = float(np.clip(float(s["w_complaint"]), 0, 0.2))
    s["time_limit_s"] = float(np.clip(float(s["time_limit_s"]), 1, 5))
    for k in ("use_playbook", "llm_direct_plan", "fuse"):
        s[k] = bool(s[k])
    default_critic = AgentSettings().critic_instructions
    crit = str(s.get("critic_instructions") or default_critic)[:400]
    s["critic_instructions"] = crit if note_ok(crit)[0] else default_critic
    s["weights"] = _clean_weights(s.get("weights"))
    note = str(genome.get("strategy_note", ""))[:400]
    return {"settings": s, "strategy_note": note if (not note or note_ok(note)[0]) else ""}


def _clean_weights(w):
    """PlannerWeights as a plain dict, or None (= B's defaults). Anything PlannerWeights.validate() refuses is dropped."""
    from backend.planner.weights import PlannerWeights
    if w is None:
        return None
    try:
        d = w.to_dict() if isinstance(w, PlannerWeights) else dict(w)
        d = PlannerWeights.from_dict(d).to_dict()            # validate: can't plan past the limit or act late
        d["priority_values"] = {int(k): v for k, v in d["priority_values"].items()}
        for k, hi in (("margin", 6), ("fairness", 6), ("storm", 50), ("rain", 30), ("hot_break_bonus", 15)):
            d[k] = int(np.clip(int(d[k]), 0, hi))
        d["budget_margin_c"] = float(np.clip(float(d["budget_margin_c"]), 0.0, 0.3))
        return d
    except (TypeError, ValueError, KeyError):
        return None


def to_settings(genome: dict) -> AgentSettings:
    from backend.planner.weights import PlannerWeights
    s = dict(genome["settings"])
    s["objectives"] = tuple(s["objectives"])
    w = _clean_weights(s.get("weights"))
    s["weights"] = PlannerWeights.from_dict(w) if w else None
    allowed = {f.name for f in fields(AgentSettings)}
    return AgentSettings(**{k: v for k, v in s.items() if k in allowed})


class NotedLLM:
    """Adds the candidate's evolved strategy note to every planner prompt (the 'system prompt' that evolves)."""

    def __init__(self, llm, note: str):
        self.llm, self.note = llm, note

    @property
    def cost_usd(self):
        return self.llm.cost_usd

    def text(self, prompt, system=None, **kw):
        if self.note:
            system = (system or "") + "\n\nStrategy guidance from past runs: " + self.note
        return self.llm.text(prompt, system=system, **kw)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate_one(genome: dict, scenario, llm=None, with_trace: bool = False) -> dict:
    s = to_settings(genome)
    before = llm.cost_usd if llm is not None else 0.0
    t0 = time.time()
    r = plan_with_agent(scenario, settings=s, llm=NotedLLM(llm, genome["strategy_note"]) if llm is not None else None,
                        use_llm=llm is not None)
    latency = time.time() - t0
    cost = (llm.cost_usd - before) if llm is not None else 0.0
    n_safety = check_plans(scenario, [r.plan])[0].n_safety       # verify again, outside the agent
    outs = outcomes(scenario, r.plan, REALIZATIONS)
    m = mean_outcome(outs)
    row = {"scenario": scenario.id, "tags": scenario.tags, **m, "safety_violations": n_safety,
           "latency_s": round(latency, 2), "cost_usd": round(cost, 4)}
    if with_trace:
        actual = simulate_actual(scenario, [r.plan], REALIZATIONS[0])[0]
        out0 = score_outcome(scenario, r.plan, actual)
        done = r.plan.units_done(scenario)
        row["trace"] = {
            "agent_log": r.log[-8:],
            "chosen": r.plan.source,
            "facts": find_facts(scenario, r.plan, r.check.trace, actual, out0),
            "work_left": [{"task": t.name, "priority": t.priority, "intensity": t.intensity, "left": t.units - done[t.id]}
                          for t in scenario.tasks if done[t.id] < t.units][:6],
            "candidates": [{"name": c.name, "verified": bool(c.check and c.check.ok), **{k: c.score.get(k) for k in
                            ("throughput", "peak_p90", "rank")}} for c in r.candidates][:10],
        }
    return row


def _eval_job(args):
    genome, sc, with_trace = args
    return evaluate_one(genome, sc, None, with_trace)


def evaluate(genome: dict, scenarios: list, llm=None, with_trace: bool = False) -> list[dict]:
    if llm is None:
        return pmap(_eval_job, [(genome, sc, with_trace) for sc in scenarios])
    with ThreadPoolExecutor(max_workers=3) as ex:            # LLM calls wait on the network: threads are enough
        return list(ex.map(lambda sc: evaluate_one(genome, sc, llm, with_trace), scenarios))


def summarize(rows: list[dict]) -> dict:
    return {"score": round(float(np.mean([r["score"] for r in rows])), 4),
            "strain_margin_c": round(float(np.mean([r["strain_margin_c"] for r in rows])), 4),
            "throughput": round(float(np.mean([r["throughput"] for r in rows])), 4),
            "fairness": round(-float(np.mean([r["heat_spread"] for r in rows])), 2),
            "latency": round(-float(np.mean([r["latency_s"] for r in rows])), 2),
            "cost": round(-float(np.sum([r["cost_usd"] for r in rows])), 4),
            "workers_alerted": round(float(np.mean([r["workers_alerted"] for r in rows])), 3),
            "solve_rate": round(float(np.mean([r["solve_rate"] for r in rows])), 3),
            "safety_violations": int(sum(r["safety_violations"] for r in rows))}


# ---------------------------------------------------------------------------
# Archive: Pareto front, novelty, parent selection
# ---------------------------------------------------------------------------
def dominates(a: dict, b: dict) -> bool:
    return all(a[m] >= b[m] for m in METRICS) and any(a[m] > b[m] for m in METRICS)


def pareto_front(archive: list[dict]) -> set[str]:
    live = [c for c in archive if c["accepted"]]
    return {c["id"] for c in live if not any(dominates(o["val"], c["val"]) for o in live if o is not c)}


def genome_distance(a: dict, b: dict) -> float:
    sa, sb = a["settings"], b["settings"]
    oa, ob = set(sa["objectives"]), set(sb["objectives"])
    d = 1 - len(oa & ob) / len(oa | ob)
    d += abs(sa["w_peak"] - sb["w_peak"]) / 5 + abs(sa["w_fair"] - sb["w_fair"]) / 0.02
    d += abs(sa["w_complaint"] - sb["w_complaint"]) / 0.2 + abs(sa["n_llm_ideas"] - sb["n_llm_ideas"]) / 3
    d += sum(sa[k] != sb[k] for k in ("use_playbook", "llm_direct_plan", "fuse")) / 3
    d += 0.5 * (a["strategy_note"] != b["strategy_note"]) + 0.5 * (sa["critic_instructions"] != sb["critic_instructions"])
    d += 0.5 * (sa.get("weights") != sb.get("weights"))
    return d


def novelty(c: dict, archive: list[dict], k: int = 3) -> float:
    ds = sorted(genome_distance(c["genome"], o["genome"]) for o in archive if o is not c and o["accepted"])
    return float(np.mean(ds[:k])) if ds else 1.0


def select_parent(archive: list[dict], rng) -> dict:
    live = [c for c in archive if c["accepted"]]
    scores = np.array([c["val"]["score"] for c in live])
    rank = (scores.argsort().argsort() + 1) / len(live)                 # 0..1, best = 1
    nov = np.array([novelty(c, archive) for c in live])
    nov = nov / nov.max() if nov.max() > 0 else nov
    kids = np.array([c["n_children"] for c in live])
    front = pareto_front(archive)
    w = (rank + settings.GEPA_NOVELTY_WEIGHT * nov + 0.3 * np.array([c["id"] in front for c in live])) / (1 + 0.5 * kids)
    return live[int(rng.choice(len(live), p=w / w.sum()))]


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------
MUTATE_PROMPT = """TASK: MUTATE
You improve a crew-scheduling agent by editing its strategy settings. A code rule checker
enforces every safety rule outside the agent; you can't change safety, only HOW it plans.
Each plan is then worked in a heat-strain simulator. The score rewards work done and
penalizes any worker who would hit a heat alert and high peak heat.

Current settings:
{genome}

Knobs you may change:
{knobs}
strategy_note: text (max 400 chars) added to the agent's LLM prompts. Never about limits,
thresholds, breaks, alerts or anyone handling more heat (such notes are thrown out).

How these settings did on {n} training days (traces):
{traces}

Read the traces. Find what went wrong (alerts, who got hot and when, work left undone,
which candidate plans won). Propose ONE targeted edit: change 1-3 knobs.
Answer with JSON only: {{"why": "...", "changes": {{"knob": value}}, "strategy_note": "..." (optional)}}"""


def llm_mutate(llm, genome: dict, rows: list[dict]) -> tuple[dict | None, str]:
    llm.purpose = "gepa:mutate"
    traces = json.dumps([{k: r[k] for k in ("scenario", "tags", "score", "throughput", "workers_alerted",
                                            "actual_peak_c", "trace")} for r in rows])
    data = extract_json(llm.text(MUTATE_PROMPT.format(genome=json.dumps(genome), knobs=json.dumps(KNOBS, indent=1),
                                                      n=len(rows), traces=traces), effort="medium")) or {}
    changes = data.get("changes") if isinstance(data.get("changes"), dict) else None
    if not changes and not data.get("strategy_note"):
        return None, "LLM gave no usable edit"
    child = {"settings": {**genome["settings"], **{k: v for k, v in (changes or {}).items() if k in KNOBS}},
             "strategy_note": str(data.get("strategy_note", genome["strategy_note"]))}
    return clamp(child), str(data.get("why", ""))[:300]


def code_mutate(genome: dict, rows: list[dict], rng) -> tuple[dict, str]:
    """Reflective mutation in plain code: read the same traces, make one targeted edit."""
    s = dict(genome["settings"])
    s["objectives"] = list(s["objectives"])
    alerts = sum(r["workers_alerted"] for r in rows)
    ran_hot = [f for r in rows for f in r["trace"]["facts"]["findings"]]
    p1_left = sum(1 for r in rows for t in r["trace"]["work_left"] if t["priority"] == 1)
    spread = np.mean([r["heat_spread"] for r in rows])
    options = []
    if alerts or ran_hot:
        options.append(("w_peak", min(5.0, s["w_peak"] * 1.6 + 0.3),
                        f"{alerts:.1f} workers alerted per day / {len(ran_hot)} ran hotter than forecast: weigh peak heat more"))
        if "margin" not in s["objectives"]:
            options.append(("objectives", s["objectives"][:3] + ["margin"], "hot days: also try the margin objective"))
    if p1_left and not alerts:
        options.append(("w_peak", max(0.0, s["w_peak"] * 0.6), f"{p1_left} priority-1 tasks left undone with no alerts: "
                                                                "rank less on peak heat"))
        if s["objectives"][0] != "throughput":
            options.append(("objectives", ["throughput"] + [o for o in s["objectives"] if o != "throughput"][:3],
                            "work left undone: start from the throughput objective"))
    if spread > 25:
        options.append(("w_fair", min(0.02, s["w_fair"] * 2 + 0.001), f"heat spread {spread:.0f}: weigh fairness more"))
    options.append(("fuse", not s["fuse"], "try the other fusion setting"))
    if s.get("weights"):
        from backend.learning.weights_search import nudge
        options.append(("weights", nudge(s["weights"], rng), "nudge one optimizer weight"))
    options.append(("w_complaint", float(np.clip(s["w_complaint"] + rng.normal(0, 0.05), 0, 0.2)), "explore critic weight"))
    knob, value, why = options[int(rng.integers(0, min(len(options), 3)))] if len(options) > 2 else options[0]
    s[knob] = value
    return clamp({"settings": s, "strategy_note": genome["strategy_note"]}), why


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def run_gepa(train: list, val: list, iterations: int = settings.GEPA_ITERATIONS, llm=None, llm_planner: bool = False,
             seed: int = 0, verbose: bool = True, start: dict | None = None) -> dict:
    """llm = mutator (Claude, Fake or None). llm_planner = also use it inside the planner (expensive).
    start = the starting genome (default: session B's AgentSettings defaults)."""
    t0 = time.time()
    rng = np.random.default_rng(seed)
    plan_llm = llm if llm_planner else None
    g0 = clamp(start or seed_genome())
    root = {"id": "a0", "parent": None, "generation": 0, "genome": g0, "why": "starting agent (session B defaults)",
            "accepted": True, "n_children": 0, "val_rows": evaluate(g0, val, plan_llm)}
    root["val"] = summarize(root["val_rows"])
    archive, cache = [root], {}
    if verbose:
        print(f"  a0 (seed) val score {root['val']['score']:.4f}")
    for it in range(1, iterations + 1):
        parent = select_parent(archive, rng)
        idx = rng.choice(len(train), min(settings.GEPA_MINIBATCH, len(train)), replace=False)
        mb = [train[int(i)] for i in idx]
        key = (parent["id"], tuple(sc.id for sc in mb))
        if key not in cache:
            cache[key] = evaluate(parent["genome"], mb, plan_llm, with_trace=True)
        p_rows = cache[key]
        child_g, why, by = None, "", "code"
        if llm is not None:
            child_g, why = llm_mutate(llm, parent["genome"], p_rows)
            by = "llm" if child_g else "code"
        if child_g is None or any(child_g == c["genome"] for c in archive):
            child_g, why2 = code_mutate(parent["genome"], p_rows, rng)
            why = why2 if not why or child_g is None else f"{why} (then code: {why2})"
            by = "code"
        cid = f"a{it}"
        node = {"id": cid, "parent": parent["id"], "generation": parent["generation"] + 1, "genome": child_g,
                "why": why, "mutator": by, "n_children": 0}
        parent["n_children"] += 1
        c_rows = evaluate(child_g, mb, plan_llm, with_trace=False)
        p_mb, c_mb = summarize(p_rows)["score"], summarize(c_rows)["score"]
        node["minibatch"] = {"parent": p_mb, "child": c_mb, "scenarios": [sc.id for sc in mb]}
        if c_mb > p_mb and summarize(c_rows)["safety_violations"] == 0:
            node["val_rows"] = evaluate(child_g, val, plan_llm)
            node["val"] = summarize(node["val_rows"])
            node["accepted"] = node["val"]["safety_violations"] == 0
        else:
            node["accepted"], node["val"] = False, None
        archive.append(node)
        if verbose:
            res = f"val {node['val']['score']:.4f} ACCEPTED" if node["accepted"] else "rejected on minibatch"
            print(f"  {cid} <- {parent['id']} [{by}] {why[:90]} | minibatch {p_mb:.3f} -> {c_mb:.3f} | {res}")
    live = [c for c in archive if c["accepted"]]
    best = max(live, key=lambda c: (c["val"]["score"], -c["generation"]))
    front = pareto_front(archive)
    return {"archive": archive, "best": best["id"], "seed": "a0", "pareto_front": sorted(front),
            "gain_val": round(best["val"]["score"] - root["val"]["score"], 4),
            "llm": llm_summary(llm), "seconds": round(time.time() - t0, 1)}


def save_run(res: dict, path=None) -> None:
    path = path or settings.DATA_DIR / "gepa_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=1, default=str))
    best = next(c for c in res["archive"] if c["id"] == res["best"])
    (settings.HERE / "best_agent.json").write_text(json.dumps(
        {"id": best["id"], "genome": best["genome"], "val": best["val"], "why": best["why"]}, indent=1))


if __name__ == "__main__":
    from backend.learning.curriculum import bank_scenarios, load_bank
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=settings.GEPA_ITERATIONS)
    ap.add_argument("--llm", action="store_true", help="Claude is the reflective mutator")
    ap.add_argument("--llm-planner", action="store_true", help="Claude also runs inside the planner (pricier)")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--budget", type=float, default=settings.BUDGET_GEPA_USD)
    ap.add_argument("--n-train", type=int, default=12)
    ap.add_argument("--n-val", type=int, default=6)
    ap.add_argument("--start-weights", action="store_true",
                    help="start from the weight search's best PlannerWeights (backend/learning/best_weights.json)")
    args = ap.parse_args()
    llm = FakeLLM() if args.fake else (get_budget_llm(args.budget, "gepa") if args.llm else None)
    train = bank_scenarios(load_bank(split="train")[:args.n_train])
    val = bank_scenarios(load_bank(split="val")[:args.n_val])
    print(f"GEPA: {len(train)} train / {len(val)} val scenarios, {args.iterations} rounds, "
          f"mutator {'Claude' if llm else 'code'}, planner {'with' if args.llm_planner else 'without'} LLM")
    start = None
    if args.start_weights:
        start = seed_genome()
        start["settings"]["weights"] = json.loads((settings.HERE / "best_weights.json").read_text())["weights"]
    res = run_gepa(train, val, args.iterations, llm=llm, llm_planner=args.llm_planner, start=start)
    save_run(res)
    best = next(c for c in res["archive"] if c["id"] == res["best"])
    print(f"\nBest {res['best']} val score {best['val']['score']:.4f} ({res['gain_val']:+.4f} vs seed). "
          f"Pareto front {res['pareto_front']}. LLM {res['llm']}. {res['seconds']:.0f}s")
