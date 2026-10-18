"""
"Edit, don't write": the model gets the optimizer's VERIFIED plan and answers with a few edits.

    {"edits": [["w3", 9, "rest"], ["w1", 4, "t2"]], "notes": "..."}

Each edit sets one worker's slot to a task id or "rest". Code applies the edits to the optimizer's
plan (edits that are malformed, or illegal by the format rules, are dropped), then B's rule checker
judges the result like any other plan. An empty list keeps the optimizer's plan.

Why: writing 100 slots from scratch is hard for a 1.7B model; changing a few slots of a plan that
already passes is much easier, and the optimizer's plan is always there as a safe fallback.

Teaching targets (dataset): on each TRAINING day, the edits that turn the optimizer's throughput
plan into the verified plan Claude's agent picked (the same target as the plan examples).
"""

import json
from concurrent.futures import ProcessPoolExecutor

from ml.local_planner.common import DATA_DIR, limit_solver_threads, out_dir

from backend.planner.baselines import rules_in_words, scenario_brief
from backend.planner.llm import extract_json
from backend.planner.scenario import REST, Scenario

EDIT_SYSTEM = ("You improve safe outdoor crew schedules. A code rule checker re-checks every schedule; "
               "you suggest edits. Answer with JSON only.")
OPT_DIR = DATA_DIR / "opt_plans"
MAX_EDITS = 80


# ---------------------------------------------------------------------------
# The optimizer's plan for a day (cached; CPU)
# ---------------------------------------------------------------------------
def optimizer_plan_for(day: dict) -> dict:
    """optimizer:throughput plan + per-worker peaks + plan score. Top-level so processes can run it."""
    from backend.planner.archon import AgentSettings, score
    from backend.planner.optimizer import optimize
    from backend.planner.rules import check_plans
    limit_solver_threads()
    sc = Scenario.from_dict(day["scenario"])
    path = OPT_DIR / f"{sc.id}.json"
    if path.exists():
        return json.loads(path.read_text())
    plan = optimize(sc, "throughput").plan
    chk = check_plans(sc, [plan])[0]
    s = score(sc, plan, chk, AgentSettings())
    rec = {"assignments": plan.assignments, "ok": chk.ok,
           "peaks": {k: round(v, 2) for k, v in chk.peak_p90.items()},
           "done": plan.units_done(sc), "rank": s["rank"], "throughput": s["throughput"]}
    path.write_text(json.dumps(rec))
    return rec


def add_optimizer_plans(days: list[dict], workers: int = 3) -> list[dict]:
    out_dir(OPT_DIR)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        recs = list(ex.map(optimizer_plan_for, days))
    return [{**d, "opt_plan": r["assignments"], "opt_peaks": r["peaks"], "opt_done": r["done"],
             "opt_rank": r["rank"], "opt_throughput": r["throughput"]} for d, r in zip(days, recs)]


# ---------------------------------------------------------------------------
# Prompt, edits, applying them
# ---------------------------------------------------------------------------
def edit_prompt(sc: Scenario, day: dict) -> str:
    current = {"assignments": day["opt_plan"], "peak_p90_c": day["opt_peaks"],
               "tasks": [{"id": t.id, "done": day["opt_done"][t.id], "needed": t.units} for t in sc.tasks]}
    return (f"Scenario:\n{json.dumps(scenario_brief(sc), separators=(',', ':'))}\n\n{rules_in_words()}\n"
            f"Verified plan from the optimizer (it passes every rule):\n{json.dumps(current, separators=(',', ':'))}\n\n"
            "Improve it with edits: more priority-weighted work, more heat margin for anyone near the limit, "
            "fairer rotation of hot tasks. Each edit sets one worker's slot to a task id or \"rest\". "
            "Every edited plan is re-checked and rejected if it breaks a rule. An empty list keeps the plan.\n"
            'Answer with only this JSON: {"edits": [["<worker id>", <slot>, "<task id or rest>"], ...], '
            '"notes": "<one or two short sentences>"}')


def diff_edits(before: dict, after: dict) -> list[list]:
    """The edits that turn one plan into another, worker by worker, slot by slot."""
    out = []
    for wid, row in after.items():
        for k, a in enumerate(row):
            if before.get(wid, [None] * len(row))[k] != a:
                out.append([wid, k, a])
    return out


def legal(sc: Scenario, wid: str, k: int, value: str) -> bool:
    """Format-level legality of one edit (same as constrained decoding's 'format' level)."""
    if value == REST:
        return True
    try:
        w, t = sc.worker(wid), sc.task(value)
    except StopIteration:
        return False
    deadline = t.deadline if t.deadline is not None else sc.n_slots
    return w.is_available(k) and not (t.skill and t.skill not in w.skills) and t.earliest <= k < deadline


def apply_edits(sc: Scenario, base: dict, edits) -> tuple[dict, int, int]:
    """New assignments + how many edits were applied / dropped."""
    out = {wid: list(row) for wid, row in base.items()}
    applied = dropped = 0
    for e in (edits or [])[:MAX_EDITS]:
        ok = (isinstance(e, list) and len(e) == 3 and e[0] in out and isinstance(e[1], int)
              and 0 <= e[1] < sc.n_slots and isinstance(e[2], str) and legal(sc, e[0], e[1], e[2]))
        if ok:
            out[e[0]][e[1]] = e[2]
            applied += 1
        else:
            dropped += 1
    return out, applied, dropped


def parse_edits(text: str):
    data = extract_json(text or "")
    if not isinstance(data, dict) or not isinstance(data.get("edits"), list):
        return None, ""
    return data["edits"], str(data.get("notes", ""))[:300]


def gen_edit_plans(model, tok, sc: Scenario, day: dict, k: int, temperature: float, repair: bool = False):
    """k edit answers -> k edited plans (with info). With repair: show violations, up to 3 tries."""
    import mlx.core as mx
    from mlx_lm import batch_generate
    from mlx_lm.sample_utils import make_sampler
    from ml.local_planner.variants import MAX_TRIES, prompt_ids, violations_text
    sampler = make_sampler(temp=temperature, top_p=0.95)
    histories = [[] for _ in range(k)]
    results = [None] * k
    todo = list(range(k))
    for attempt in range(1, (MAX_TRIES if repair else 1) + 1):
        ids = [prompt_ids(tok, EDIT_SYSTEM, edit_prompt(sc, day), histories[i]) for i in todo]
        texts = batch_generate(model, tok, ids, max_tokens=1000, sampler=sampler).texts
        mx.clear_cache()
        still = []
        for i, text in zip(todo, texts):
            edits, notes = parse_edits(text)
            plan, applied, dropped = apply_edits(sc, day["opt_plan"], edits)
            info = {"parsed": edits is not None, "applied": applied, "dropped": dropped, "tries": attempt, "notes": notes}
            results[i] = (plan, info)
            if repair and attempt < MAX_TRIES:
                problems, ok = violations_text(sc, plan)
                if not ok:
                    histories[i] += [{"role": "assistant", "content": text},
                                     {"role": "user", "content": "The rule checker rejected the edited schedule:\n- "
                                      + "\n- ".join(problems) + "\nAnswer with a corrected edit list for the ORIGINAL "
                                      "optimizer plan, same JSON format."}]
                    still.append(i)
        todo = still
        if not todo:
            break
    return results


# ---------------------------------------------------------------------------
# Training data in the edit format (from the teacher runs; training days only)
# ---------------------------------------------------------------------------
def edit_examples(split: str = "train") -> tuple[list[dict], dict]:
    """
    One example per kept teacher day: optimizer plan + edits that turn it into the verified plan
    Claude's agent picked (the same STaR target as the plan examples). Returns (examples, stats).
    """
    from backend.planner.generate import assert_not_test
    from ml.local_planner.dataset import compact, notes_from_reasons, pick_target
    from ml.local_planner.scenarios import is_safe_to_learn_from
    from ml.local_planner.teacher import RUNS_DIR
    runs, days = [], []
    for path in sorted(RUNS_DIR.glob("*.json")):
        run = json.loads(path.read_text())
        if run["split"] != split:
            continue
        sc = Scenario.from_dict(run["scenario"])
        assert_not_test(sc)
        if not is_safe_to_learn_from(sc):
            continue
        target, source = pick_target(run)
        if target is None:
            continue
        runs.append((run, target))
        days.append({"scenario": run["scenario"]})
    days = add_optimizer_plans(days)
    out, sizes = [], []
    for (run, target), day in zip(runs, days):
        sc = Scenario.from_dict(run["scenario"])
        edits = diff_edits(day["opt_plan"], target)
        if len(edits) > MAX_EDITS:
            continue                                   # too different to teach as edits
        sizes.append(len(edits))
        answer = {"edits": edits, "notes": notes_from_reasons(run["final"]["reasons"])}
        out.append({"messages": [{"role": "system", "content": EDIT_SYSTEM},
                                 {"role": "user", "content": edit_prompt(sc, day)},
                                 {"role": "assistant", "content": compact(answer)}]})
    import numpy as np
    stats = {"examples": len(out), "edits_median": float(np.median(sizes)) if sizes else 0,
             "edits_max": max(sizes) if sizes else 0, "no_change": sum(s == 0 for s in sizes)}
    return out, stats
