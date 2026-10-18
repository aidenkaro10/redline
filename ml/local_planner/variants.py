"""
Ways to get a plan out of the local model, each measured on the VALIDATION days only.

    free      plain generation (what we had): the model writes the whole schedule JSON
    format    constrained decoding: right shape and legal ids only (constrained.py)
    rules     constrained decoding: format + the simple hard rules (storms, new hires, breaks)
    edit      the model gets the optimizer's verified plan and writes a few edits; code applies them
    ...+repair  on a rule-checker failure, the exact violations go back to the model, up to 3 tries

Every plan from every variant goes through B's rule checker. Nothing here decides safety.

Run:  python -m ml.local_planner.variants --modes free,format,rules --adapter adapter
Out:  data/local_planner/variants/<adapter>/<mode>.json and a table on screen.
"""

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from ml.local_planner.common import BASE_MODEL_DIR, CKPT_DIR, DATA_DIR, out_dir

from backend.planner.baselines import LLM_SYSTEM, llm_prompt
from backend.planner.scenario import Plan, Scenario
from ml.local_planner.evaluate import parse_plan
from ml.local_planner.self_train import GRADE_WORKERS, WORK_TOL, load_days

VAR_DIR = DATA_DIR / "variants"
PCT_RE = __import__("re").compile(r"(\d{1,3})\s?%")      # "about 93%" in the notes
VAL_K = 4
TEMPERATURE = 0.7
MAX_TRIES = 3


# ---------------------------------------------------------------------------
# Grading (CPU processes)
# ---------------------------------------------------------------------------
def grade_plans(item: dict) -> dict:
    """Rule checker + simulated shift for each plan of one day (top-level so processes can run it)."""
    from backend.planner.archon import AgentSettings, score
    from backend.planner.benchmark import score_plans
    from backend.planner.rules import check_plans
    sc = Scenario.from_dict(item["scenario"])
    plans = [Plan(a or {}) for a in item["assignments"]]
    scores = score_plans(sc, plans)
    ok = [p for p, s in zip(plans, scores) if s["success"]]
    checks = iter(check_plans(sc, ok) if ok else [])
    out = []
    for p, s, extra in zip(plans, scores, item.get("extra") or [{}] * len(plans)):
        rank = score(sc, p, next(checks), AgentSettings())["rank"] if s["success"] else None
        out.append({"success": s["success"], "valid": s["valid"], "n_safety": s["n_safety"], "rules": s["rules"],
                    "throughput": s["throughput"], "peak_p90": s["peak_p90"], "rank": rank, **extra})
    return {"scenario_id": sc.id, "opt_throughput": item["opt_throughput"], "opt_rank": item.get("opt_rank"),
            "samples": out}


def violations_text(sc: Scenario, assignments: dict, limit: int = 8) -> list[str]:
    """The rule checker's complaints in plain words, for the repair loop."""
    from backend.planner.rules import check_plans
    chk = check_plans(sc, [Plan(assignments)])[0]
    return [f"{v.rule}: {v.detail}" + (f" (worker {v.worker}, slot {v.slot})" if v.slot >= 0 else f" (worker {v.worker})")
            for v in chk.violations[:limit]], chk.ok


def summarize(graded: list[dict]) -> dict:
    s = [x for g in graded for x in g["samples"]]
    passed = [x for x in s if x["success"]]
    good = [x for g in graded for x in g["samples"]
            if x["success"] and x["throughput"] >= g["opt_throughput"] - WORK_TOL and x["throughput"] > 0]
    out = {"days": len(graded), "samples": len(s),
           "valid": float(np.mean([x["valid"] for x in s])),
           "pass": float(np.mean([x["success"] for x in s])),
           "good_pass": len(good) / len(s),
           "work_when_pass": float(np.mean([x["throughput"] for x in passed])) if passed else None,
           "opt_work": float(np.mean([g["opt_throughput"] for g in graded])),
           "safety_violations_per_sample": float(np.mean([x["n_safety"] for x in s])),
           "seconds_per_sample": float(np.mean([x.get("seconds", 0.0) for x in s]))}
    claims = [(c, x["throughput"]) for x in s for c in PCT_RE.findall(x.get("notes") or "")]
    if claims:
        out["notes_pct_claims"] = len(claims)
        out["notes_pct_wrong"] = float(np.mean([abs(float(c) - 100 * tp) > 2.5 for c, tp in claims]))
    if any("tries" in x for x in s):
        out["tries_mean"] = float(np.mean([x.get("tries", 1) for x in s]))
    ranks = [(x["rank"], g["opt_rank"]) for g in graded for x in g["samples"] if x["success"] and g.get("opt_rank") is not None]
    if ranks:
        out["beats_optimizer_rank"] = float(np.mean([a > b + 1e-6 for a, b in ranks]))
    return out


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def prompt_ids(tok, system: str, user: str, history: list | None = None) -> list[int]:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}] + (history or [])
    return tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, enable_thinking=False,
                                   return_dict=False)


def gen_plans(model, tok, sc: Scenario, mode: str, k: int, history: list | None = None) -> list[tuple[str, dict | None]]:
    """k samples of a whole schedule. mode: free / format / rules."""
    ids = prompt_ids(tok, LLM_SYSTEM, llm_prompt(sc), history)
    if mode == "free":
        from mlx_lm import batch_generate
        from mlx_lm.sample_utils import make_sampler
        import mlx.core as mx
        r = batch_generate(model, tok, [ids] * k, max_tokens=900, sampler=make_sampler(temp=TEMPERATURE, top_p=0.95))
        mx.clear_cache()
        return [(t, parse_plan(t, "x").assignments) for t in r.texts]
    from ml.local_planner.constrained import generate_constrained
    return generate_constrained(model, tok, sc, ids, k, TEMPERATURE, level=mode)


def run_mode(mode: str, adapter_dir: Path | None, days: list[dict]) -> list[dict]:
    """Sample on the GPU, grade on CPU processes as days finish."""
    from ml.local_planner.client import load_model
    (model, tok), _ = load_model(BASE_MODEL_DIR, adapter_dir)
    base_mode, repair = mode.replace("+repair", ""), mode.endswith("+repair")
    futures = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=GRADE_WORKERS) as ex:
        for i, day in enumerate(days):
            sc = Scenario.from_dict(day["scenario"])
            t = time.time()
            if base_mode == "edit":
                from ml.local_planner.edits import gen_edit_plans
                res = gen_edit_plans(model, tok, sc, day, VAL_K, TEMPERATURE, repair=repair)
                assigns = [a for a, _ in res]
                extra = [x for _, x in res]
            elif repair:
                assigns, extra = [], []
                for _ in range(VAL_K):
                    a, info = repair_loop(model, tok, sc, base_mode)
                    assigns.append(a)
                    extra.append(info)
            else:
                res = gen_plans(model, tok, sc, base_mode, VAL_K)
                assigns = [a for _, a in res]
                extra = [{"notes": (parse_plan(txt, "x").notes or "")} for txt, _ in res]
            dt = (time.time() - t) / VAL_K
            for e in extra:
                e["seconds"] = dt
            futures.append(ex.submit(grade_plans, {"scenario": day["scenario"], "opt_throughput": day["opt_throughput"],
                                                   "opt_rank": day.get("opt_rank"), "assignments": assigns,
                                                   "extra": extra}))
            if (i + 1) % 7 == 0:
                print(f"    {mode}: {i + 1}/{len(days)} days ({time.time() - t0:.0f}s)", flush=True)
        return [f.result() for f in futures]


def repair_loop(model, tok, sc: Scenario, mode: str) -> tuple[dict | None, dict]:
    """One sample; if the checker rejects it, show the model the violations and ask for a fixed plan. Up to 3 tries."""
    history, a = [], None
    for attempt in range(1, MAX_TRIES + 1):
        text, a = gen_plans(model, tok, sc, mode, 1, history)[0]
        if a is None:
            continue
        problems, ok = violations_text(sc, a)
        if ok:
            return a, {"tries": attempt, "notes": parse_plan(text, "x").notes}
        history += [{"role": "assistant", "content": text},
                    {"role": "user", "content": "The rule checker rejected this schedule:\n- " + "\n- ".join(problems)
                     + "\nFix these problems (add shade breaks, move or lighten work) and answer with the full "
                       "corrected JSON in the same format."}]
    return a, {"tries": MAX_TRIES, "notes": ""}


def main():
    global VAL_K
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="free,format,rules")
    ap.add_argument("--adapter", default="adapter")
    ap.add_argument("--days", type=int, default=0, help="only the first N days (0 = all)")
    ap.add_argument("--split", default="val", choices=["val", "test"],
                    help="val for choosing; test = B's 12 held-out days, for the ONE final scoring")
    ap.add_argument("--k", type=int, default=4, help="samples per day")
    args = ap.parse_args()
    VAL_K = args.k
    adapter = CKPT_DIR / args.adapter if args.adapter != "none" else None
    if args.split == "test":
        from backend.planner.generate import load_test_set
        days = [{"scenario": sc.to_dict()} for sc in load_test_set()]     # scored only, never learned from
    else:
        days = load_days("val")
    if args.days:
        days = days[:args.days]
    out = out_dir(VAR_DIR / (args.adapter + ("_TEST" if args.split == "test" else "")))
    from ml.local_planner.edits import add_optimizer_plans
    days = add_optimizer_plans(days)     # every mode is compared with the same optimizer plan (work and score)
    rows = {}
    for mode in args.modes.split(","):
        t0 = time.time()
        graded = run_mode(mode, adapter, days)
        s = summarize(graded)
        (out / f"{mode}.json").write_text(json.dumps({"mode": mode, "summary": s, "graded": graded}))
        rows[mode] = s
        print(f"  VAL {args.adapter} {mode}: " + json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()})
              + f" ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
