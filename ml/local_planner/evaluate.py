"""
Held-out evaluation: does the fine-tuned local model beat Claude on our planning task?

Uses ONLY B's 12 held-out test scenarios (backend/planner/scenarios/test). Nothing here learns.

Two tables:

1. FIRST TRY (no rule checker): the model writes the whole schedule from B's llm_only prompt.
   How often is it valid, and how often does it pass every safety rule on the first try?
     - Claude Opus 5.5 alone: B's benchmark rows (data/planner/benchmark.json, llm_only, 12 x 5 runs)
     - base local model (Qwen3-1.7B, no fine-tune) and fine-tuned local model: 5 sampled runs each
   Plus "best of 5 + checker": keep the best sample that passes the checker, else fall back to the
   optimizer's plan. That's a planner you could ship (the checker still decides).

2. FULL PLANNER (what ships: every plan passes the rule checker before anyone sees it)
     - optimizer only (CP-SAT, no LLM), re-run here
     - Claude agent: B's benchmark rows (agent_day1, 12 x 5 runs, Opus 5.5)
     - the same agent pipeline with the base local model / the fine-tuned local model as its LLM
   Safety violations after the checker must be 0 for every row.

Run:  python -m ml.local_planner.evaluate                     (everything)
      python -m ml.local_planner.evaluate --only first_try --models finetuned --runs 5
Out:  data/local_planner/eval/results.json and a markdown table on screen.
"""

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

from ml.local_planner.common import ADAPTER_DIR, DATA_DIR, limit_solver_threads, out_dir

from backend.planner.archon import AgentSettings, plan_with_agent, score
from backend.planner.baselines import LLM_SYSTEM, llm_prompt
from backend.planner.benchmark import score_plans
from backend.planner.generate import load_test_set
from backend.planner.llm import extract_json
from backend.planner.optimizer import optimize
from backend.planner.rules import check_plans
from backend.planner.scenario import Plan, Scenario

EVAL_DIR = DATA_DIR / "eval"
# B's scored benchmark runs (same score_plans, same test set). Lives next to our data folder.
B_BENCHMARK = Path(os.getenv("LOCAL_PLANNER_B_BENCHMARK", str(DATA_DIR.parent / "planner" / "benchmark.json")))


def parse_plan(text: str, source: str) -> Plan:
    """Same parsing as B's llm_only: first JSON object, its "assignments" dict."""
    data = extract_json(text or "") or {}
    a = data.get("assignments") if isinstance(data.get("assignments"), dict) else {}
    return Plan({str(k): [str(x) for x in v] for k, v in a.items() if isinstance(v, list)},
                source=source, notes=str(data.get("notes", ""))[:300])


def make_llm(name: str, temperature: float = 0.0, seed: int = 0):
    from ml.local_planner.client import LocalLLM
    if name == "base":
        return LocalLLM(adapter_dir=None, temperature=temperature, seed=seed)
    if name == "finetuned":
        llm = LocalLLM(adapter_dir=ADAPTER_DIR, temperature=temperature, seed=seed)
        assert llm.adapter, f"no adapter in {ADAPTER_DIR}: train first"
        return llm
    raise ValueError(name)


# ---------------------------------------------------------------------------
# 1. First try
# ---------------------------------------------------------------------------
def first_try(model: str, scenarios: list[Scenario], runs: int, temperature: float = 0.7) -> list[dict]:
    rows = []
    for sc in scenarios:
        texts, lat, toks = [], [], []
        for run in range(runs):
            llm = make_llm(model, temperature=temperature, seed=1000 * run + 7)
            t0 = time.time()
            texts.append(llm.text(llm_prompt(sc), system=LLM_SYSTEM, max_tokens=2500))
            lat.append(time.time() - t0)
            toks.append(llm.tokens_out)
        plans = [parse_plan(t, f"local:{model}") for t in texts]
        scores = score_plans(sc, plans)
        for run, (p, s) in enumerate(zip(plans, scores)):
            rows.append({"scenario": sc.id, "tags": sc.tags, "run": run, **s, "latency_s": lat[run],
                         "tokens_out": toks[run], "cost_usd": 0.0, "assignments": p.assignments, "text": texts[run]})
        ok = sum(s["success"] for s in scores)
        print(f"  first_try {model:9s} {sc.id:10s} {ok}/{runs} pass, valid {sum(s['valid'] for s in scores)}/{runs}, "
              f"safety violations {sum(s['n_safety'] for s in scores)}, {np.mean(lat):.1f}s", flush=True)
    return rows


def best_of_k_with_checker(rows: list[dict], scenarios: list[Scenario], opt_rows: dict) -> list[dict]:
    """Keep the passing sample with the most work done; if none passed, use the optimizer's plan."""
    out = []
    for sc in scenarios:
        mine = [r for r in rows if r["scenario"] == sc.id]
        good = [r for r in mine if r["success"]]
        if good:
            best = max(good, key=lambda r: r["throughput"])
            out.append({**best, "fallback": False, "latency_s": sum(r["latency_s"] for r in mine)})
        else:
            o = opt_rows[sc.id]
            out.append({**o, "fallback": True, "latency_s": sum(r["latency_s"] for r in mine) + o["latency_s"]})
    return out


# ---------------------------------------------------------------------------
# 2. Full planner
# ---------------------------------------------------------------------------
def optimizer_rows(scenarios: list[Scenario]) -> dict:
    out = {}
    for sc in scenarios:
        t0 = time.time()
        r = optimize(sc, "throughput")
        lat = time.time() - t0
        s = score_plans(sc, [r.plan])[0]
        chk = check_plans(sc, [r.plan])[0]
        out[sc.id] = {"scenario": sc.id, "run": 0, **s, "latency_s": lat, "cost_usd": 0.0,
                      "rank": score(sc, r.plan, chk, AgentSettings())["rank"] if chk.ok else None}
        print(f"  optimizer {sc.id:10s} ok={s['success']} throughput {s['throughput']:.2f} {lat:.1f}s", flush=True)
    return out


def pipeline(model: str, scenarios: list[Scenario]) -> list[dict]:
    rows = []
    for sc in scenarios:
        llm = make_llm(model)
        r = plan_with_agent(sc, playbook=None, llm=llm)
        s = score_plans(sc, [r.plan])[0]
        direct = next((c for c in r.candidates if c.name == "llm:direct"), None)
        rows.append({"scenario": sc.id, "run": 0, **s, "latency_s": r.latency_s, "cost_usd": 0.0,
                     "llm_seconds": llm.seconds, "llm_calls": llm.calls, "source": r.plan.source,
                     "rank": score(sc, r.plan, r.check, AgentSettings())["rank"] if r.check.ok else None,
                     "direct_given": direct is not None, "direct_ok": bool(direct and direct.check and direct.check.ok),
                     "direct_valid": bool(direct and direct.check and len(direct.check.violations) == direct.check.n_safety),
                     "ideas": sum(1 for c in r.candidates if c.name.startswith("llm-idea:")),
                     "summary": r.summary, "reasons": r.reasons})
        print(f"  pipeline  {model:9s} {sc.id:10s} ok={s['success']} throughput {s['throughput']:.2f} "
              f"source {r.plan.source} direct_ok={rows[-1]['direct_ok']} ideas={rows[-1]['ideas']} "
              f"{r.latency_s:.0f}s", flush=True)
    return rows


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    by_sc = {}
    for r in rows:
        by_sc.setdefault(r["scenario"], []).append(r["success"])
    peaks = [r["peak_p90"] for r in rows if r.get("peak_p90") is not None and r.get("valid")]
    fair = [r["fairness"] for r in rows if r.get("fairness") is not None and r.get("valid")]
    return {
        "n_runs": len(rows),
        "valid": float(np.mean([r["valid"] for r in rows])),
        "pass_at_1": float(np.mean([r["success"] for r in rows])),
        "pass_all_runs": float(np.mean([all(v) for v in by_sc.values()])),
        "runs_with_safety_violations": float(np.mean([r["n_safety"] > 0 for r in rows])),
        "safety_violations_per_run": float(np.mean([r["n_safety"] for r in rows])),
        "throughput": float(np.mean([r["throughput"] for r in rows])),
        "throughput_if_passed": float(np.mean([r["throughput"] for r in rows if r["success"]])) if any(r["success"] for r in rows) else None,
        "peak_p90_max": float(np.max(peaks)) if peaks else None,
        "heat_spread": float(np.mean(fair)) if fair else None,
        "latency_s": float(np.mean([r["latency_s"] for r in rows])),
        "cost_usd_total": float(sum(r.get("cost_usd", 0.0) for r in rows)),
        "cost_usd_per_plan": float(np.mean([r.get("cost_usd", 0.0) for r in rows])),
    }


def b_rows(name: str) -> list[dict]:
    """B's benchmark rows for Claude (scored by the same score_plans on the same test set)."""
    d = json.loads(B_BENCHMARK.read_text())
    return d.get(name, {}).get("rows", [])


def table(results: dict) -> str:
    fmt = lambda v, f: "-" if v is None else format(v, f)
    out = []
    for title, keys in (("First try (no checker)", results.get("first_try", {})),
                        ("Full planner (rule checker in the loop)", results.get("pipeline", {}))):
        if not keys:
            continue
        out += [f"\n**{title}**\n",
                "| Planner | Runs | Valid | Pass on first try | Runs w/ safety violations | Safety violations/run | Throughput | Peak p90 (°C) | Heat spread | Latency (s) | Cost $/plan |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, s in keys.items():
            out.append(f"| {name} | {s['n_runs']} | {s['valid']:.0%} | {s['pass_at_1']:.0%} | {s['runs_with_safety_violations']:.0%} "
                       f"| {s['safety_violations_per_run']:.1f} | {s['throughput']:.2f} | {fmt(s['peak_p90_max'], '.2f')} "
                       f"| {fmt(s['heat_spread'], '.0f')} | {s['latency_s']:.1f} | {s['cost_usd_per_plan']:.3f} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["first_try", "pipeline", "all"], default="all")
    ap.add_argument("--models", default="base,finetuned")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=0.7)
    args = ap.parse_args()

    limit_solver_threads()
    out_dir(EVAL_DIR)
    scenarios = load_test_set()
    models = [m for m in args.models.split(",") if m]
    path = EVAL_DIR / "results.json"
    raw = json.loads(path.read_text()) if path.exists() else {}
    print(f"Held-out test set: {len(scenarios)} scenarios. Models: {models}", flush=True)

    if "optimizer" not in raw:
        raw["optimizer"] = optimizer_rows(scenarios)
    if args.only in ("first_try", "all"):
        for m in models:
            raw[f"first_try:{m}"] = first_try(m, scenarios, args.runs, args.temperature)
            raw[f"best_of_{args.runs}:{m}"] = best_of_k_with_checker(raw[f"first_try:{m}"], scenarios, raw["optimizer"])
            path.write_text(json.dumps(raw))
    if args.only in ("pipeline", "all"):
        for m in models:
            raw[f"pipeline:{m}"] = pipeline(m, scenarios)
            path.write_text(json.dumps(raw))
    path.write_text(json.dumps(raw))

    opt = list(raw["optimizer"].values())
    results = {"first_try": {"Claude Opus 5.5 alone (B's llm_only)": summarize(b_rows("llm_only"))},
               "pipeline": {"optimizer only": summarize(opt),
                            "Claude agent (B's agent_day1)": summarize(b_rows("agent_day1"))}}
    for m in ("base", "finetuned"):
        label = "Qwen3-1.7B base" if m == "base" else "Qwen3-1.7B + LoRA"
        if f"first_try:{m}" in raw:
            results["first_try"][f"{label} alone"] = summarize(raw[f"first_try:{m}"])
            k = next(key for key in raw if key.startswith("best_of_") and key.endswith(f":{m}"))
            results["pipeline"][f"{label}, best of {k.split('_')[2].split(':')[0]} + checker (optimizer fallback)"] = summarize(raw[k])
        if f"pipeline:{m}" in raw:
            results["pipeline"][f"{label} in the agent pipeline"] = summarize(raw[f"pipeline:{m}"])
    (EVAL_DIR / "summary.json").write_text(json.dumps(results, indent=1))
    print(table(results))


if __name__ == "__main__":
    main()
