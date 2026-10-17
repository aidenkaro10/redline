"""
Loop 4 for the planner: a self-proposed scenario curriculum (Absolute Zero + DAPO style).

    proposer  ->  code validator  ->  current agent plans it  ->  simulated days  ->  solve rate  ->  keep?

1. Proposer writes scenario SPECS (scenarios.py knobs) for the five stress families:
   heat dome, 2 pm storm window, a worker calls out, a new unacclimatized hire,
   a dead watch mid-shift. With a key, Claude proposes them, looking at earlier specs
   and how often the agent solved them. Without a key, a code proposer does the same
   job with a per-family "difficulty dial" that moves toward the edge.
2. Validator (code, scenarios.validate) rejects broken or impossible days, anything
   that is (or copies) a held-out test scenario, and anything out of bounds.
3. Solve rate: the current agent plans the day, then the plan is worked on SOLVE_RUNS
   simulated days (different weather/effort luck, world.py). "Solved" = the rule
   checker passed it, nobody hit a heat alert in the simulated day, and at least
   75% of the priority-1 work fit in the plan (settings.SOLVED_PRIORITY1_SHARE).
4. Reward = 1 - solve rate, and 0 if never solved (Absolute Zero). We keep days the
   agent solves SOMETIMES but not always: 0 < solve rate < 1. All-solved or
   never-solved days teach nothing (DAPO's dynamic sampling idea).

The kept days are the TRAINING bank for Loops 2 and 3, split into "train" and "val".
B's held-out test scenarios (test-*) are never used here (RLEF public/private split).

Run:  python -m backend.learning.curriculum                 (no key: code proposer, $0)
      python -m backend.learning.curriculum --llm           (Claude proposer, budget-capped)
      python -m backend.learning.curriculum --fake          (fake LLM, offline dry run)
"""

import argparse
import json
import time

import numpy as np

from backend.learning import settings
from backend.learning.fake_llm import FakeLLM
from backend.learning.llm_budget import get_budget_llm, llm_summary
from backend.learning.parallel import pmap
from backend.learning.scenarios import FAMILIES, SPEC_BOUNDS, normalize_spec, spec_id, validate
from backend.learning.world import outcomes
from backend.planner.archon import plan_with_agent
from backend.planner.llm import extract_json
from backend.planner.rules import check_plan

# How each family looks at difficulty d (0 = easy, 1 = hard). Used by the code proposer.
def family_spec(family: str, d: float, rng, crew: str) -> dict:
    d = float(np.clip(d, 0, 1))
    spec = {"seed": int(rng.integers(0, 10**6)), "crew": crew,
            "heat_c": round(float(rng.uniform(0, 1.5) + 2.5 * d), 2),
            "demand": round(0.55 + 0.4 * d, 2), "heavy_bias": round(0.2 + 0.6 * d, 2)}
    if family == "heat_dome":
        spec["heat_c"] = round(2.5 + 3.5 * d, 2)
    elif family == "storm_2pm":
        spec.update(storm_start=int(rng.integers(11, 15)), storm_len=1 + int(d * 2.5))
    elif family == "callout":
        spec["callouts"] = 1 + int(d > 0.6)
    elif family == "new_hire":
        spec["new_hires"] = 1 + int(d > 0.6)
    elif family == "dead_watch":
        spec["dead_watch"] = True
    return spec


def current_agent(scenario, run: int = 0):
    """The agent being tested: the full planner pipeline, no LLM (free, repeatable)."""
    return plan_with_agent(scenario, use_llm=False).plan


def solve_rate(scenario, planner=None, runs: int = settings.SOLVE_RUNS) -> dict:
    """Plan the day with the current agent, then work the plan on `runs` simulated days."""
    plan = (planner or current_agent)(scenario, 0)
    safety = check_plan(scenario, plan).n_safety
    outs = outcomes(scenario, plan, range(runs))
    solved = [o["solved"] and safety == 0 for o in outs]
    rate = float(np.mean(solved))
    reward = 0.0 if rate in (0.0, 1.0) else 1.0 - rate        # Absolute Zero: 0 if never solved (or always)
    return {"solve_rate": rate, "reward": round(reward, 3), "safety_violations": safety,
            "mean_score": round(float(np.mean([o["score"] for o in outs])), 4),
            "alerts": int(sum(o["workers_alerted"] for o in outs)),
            "priority1_share": outs[0]["priority1_share"], "priority1_done": bool(outs[0]["priority1_done"])}


PROPOSE_PROMPT = """TASK: PROPOSE
You design stress-test days for a crew-scheduling agent at Florida power plants. The agent
plans 30-minute work slots; a code rule checker enforces every safety rule; then the plan is
worked in a simulator. A day counts as "solved" when the plan fits at least 75% of the
priority-1 work and nobody in the simulated crew hits a heat alert. Too much work for the
crew's hours makes a day unsolvable no matter what (lower demand); heat makes alerts
random (more heat, heavier work = more alerts).

We want days the agent solves SOMETIMES, not always and not never (target solve rate 25-75%).

Knobs (JSON), with allowed ranges:
{bounds}
crew: "home" (our fixed crew of 6, incl. new hire Kofi) or "random"
dead_watch: true/false (replan at 11:00; one worker's watch died, so their state is uncertain)
storm_start / warning_start: slot number (slot 0 = 07:00, 12 = 13:00) or null

Families to cover: {families}

Days tried so far, with the agent's solve rate:
{refs}

Propose {n} NEW days. Mix the families, about {home_share}% with crew "home".
Make too-easy families harder and too-hard ones easier.
Answer with JSON only: {{"specs": [{{"family": "...", "seed": 123, "crew": "home", ...knobs}}]}}"""


def _why(r: dict) -> str:
    """Why a day was (not) solved, in words the proposer can act on."""
    bits = [f"heat alerts in {r['alerts']} of {settings.SOLVE_RUNS} simulated days" if r.get("alerts") else "no heat alerts"]
    if r.get("priority1_share") is not None:
        bits.append(f"{r['priority1_share']:.0%} of priority-1 work fit in the plan")
    return ", ".join(bits)


def llm_propose(llm, refs: list[dict], n: int, home_share: int = 60) -> list[dict]:
    llm.purpose = "curriculum:propose"
    bounds = json.dumps({k: list(v) for k, v in SPEC_BOUNDS.items()})
    ref_txt = "\n".join(f"- {json.dumps(r['spec'])} -> solve rate {r['solve_rate']:.2f} ({_why(r)})"
                        for r in refs[-20:]) or "(none yet)"
    prompt = PROPOSE_PROMPT.format(bounds=bounds, families=", ".join(FAMILIES), refs=ref_txt, n=n, home_share=home_share)
    data = extract_json(llm.text(prompt, effort="low")) or {}
    out = []
    for s in (data.get("specs") or [])[:n]:
        if isinstance(s, dict):
            out.append(s)
    return out


def code_propose(rng, dials: dict, n: int, round_i: int, home_share: int = 60) -> list[dict]:
    """Round-robin over families; each family's difficulty dial sets how hard its days are."""
    out = []
    for i in range(n):
        fam = FAMILIES[(round_i * n + i) % len(FAMILIES)]
        crew = "home" if rng.uniform() * 100 < home_share else "random"
        out.append({**family_spec(fam, dials[fam] + rng.normal(0, 0.1), rng, crew), "family": fam})
    return out


def _solve_job(args):
    return solve_rate(*args)


def run_curriculum(rounds: int = 4, per_round: int = 10, llm=None, seed: int = 0, planner=None,
                   runs: int = settings.SOLVE_RUNS, verbose: bool = True, workers: int | None = None,
                   home_share: int = 60, previous: dict | None = None) -> dict:
    """planner: a top-level function (scenario, run) -> Plan; default = current_agent.
    previous: an earlier result (or saved bank) to continue from: its days are kept and not retried."""
    rng = np.random.default_rng(seed)
    dials = dict((previous or {}).get("dials") or {f: 0.5 for f in FAMILIES})
    tried = {r["id"]: r for r in (previous or {}).get("tried", (previous or {}).get("bank", []))}
    bank, log = [dict(r) for r in (previous or {}).get("bank", [])], list((previous or {}).get("log", []))
    round0 = 1 + max([r.get("round", -1) for r in tried.values()], default=-1)
    t0 = time.time()
    for r in range(round0, round0 + rounds):
        refs = list(tried.values())
        specs = llm_propose(llm, refs, per_round, home_share) if llm is not None else []
        proposer = "llm" if specs else "code"
        if not specs:                       # no key, budget hit, or a bad answer: the code proposer steps in
            specs = code_propose(rng, dials, per_round, r, home_share)
        todo = []
        for raw in specs:
            fam = str(raw.get("family", "")) or "?"
            sc, why = validate({k: v for k, v in raw.items() if k != "family"})
            entry = {"round": r, "proposer": proposer, "family": fam, "raw": raw}
            if sc is None:
                log.append({**entry, "kept": False, "reason": why})
                continue
            if sc.id in tried or any(sc.id == t[1].id for t in todo):
                log.append({**entry, "kept": False, "reason": "duplicate"})
                continue
            todo.append((entry, sc, normalize_spec({k: v for k, v in raw.items() if k != "family"})))
        results = pmap(_solve_job, [(sc, planner, runs) for _, sc, _ in todo], workers)
        for (entry, sc, spec), res in zip(todo, results):
            fam = entry["family"]
            row = {"id": sc.id, "spec": spec, "family": fam, "tags": sc.tags, **res, "round": r, "proposer": proposer}
            tried[sc.id] = row
            keep = 0 < res["solve_rate"] < 1
            if keep:
                bank.append(row)
            if fam in dials:                # move the dial toward the edge
                dials[fam] += 0.15 if res["solve_rate"] == 1 else (-0.15 if res["solve_rate"] == 0 else 0)
                dials[fam] = float(np.clip(dials[fam], 0, 1))
            log.append({**entry, "id": sc.id, "kept": keep, "solve_rate": res["solve_rate"], "reward": res["reward"],
                        "alerts": res["alerts"], "priority1_share": res["priority1_share"],
                        "reason": "learnable" if keep else ("always solved" if res["solve_rate"] == 1 else "never solved")})
            if verbose:
                print(f"  round {r} {proposer:4s} {fam:10s} {sc.id} solve {res['solve_rate']:.2f} "
                      f"reward {res['reward']:.2f} alerts {res['alerts']} p1 {res['priority1_share']:.0%} "
                      f"{'KEEP' if keep else '-'}", flush=True)
    # Split, per crew type: every 4th kept day goes to "val" (only for gates and final picks, never for learning)
    seen = {}
    for row in bank:
        i = seen[row["spec"]["crew"]] = seen.get(row["spec"]["crew"], -1) + 1
        row["split"] = "val" if i % 4 == 3 else "train"
    return {"bank": bank, "log": log, "dials": dials, "llm": llm_summary(llm), "seconds": round(time.time() - t0, 1),
            "n_tried": len(tried), "n_kept": len(bank), "tried": list(tried.values())}


def save_bank(result: dict, path=settings.BANK_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: result[k] for k in ("bank", "dials", "llm", "seconds", "n_tried", "n_kept", "tried")}
    keep["llm_runs"] = result.get("llm_runs", [result["llm"]])          # cost of every run that built this bank
    path.write_text(json.dumps(keep, indent=1))
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (settings.DATA_DIR / "curriculum_log.json").write_text(json.dumps(result["log"], indent=1, default=str))


def load_bank(path=settings.BANK_PATH, split: str | None = None, crew: str | None = None) -> list[dict]:
    rows = json.loads(path.read_text())["bank"]
    return [r for r in rows if (split is None or r["split"] == split) and (crew is None or r["spec"]["crew"] == crew)]


def bank_scenarios(rows: list[dict]):
    from backend.learning.scenarios import build
    return [build(r["spec"]) for r in rows]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--per-round", type=int, default=10)
    ap.add_argument("--llm", action="store_true", help="use Claude as the proposer (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--fake", action="store_true", help="use the fake LLM (offline dry run)")
    ap.add_argument("--budget", type=float, default=settings.BUDGET_CURRICULUM_USD)
    ap.add_argument("--home-share", type=int, default=60, help="percent of proposed days with the home crew")
    ap.add_argument("--append", action="store_true", help="continue from the saved bank instead of starting over")
    args = ap.parse_args()
    llm = FakeLLM() if args.fake else (get_budget_llm(args.budget, "curriculum") if args.llm else None)
    if args.llm and llm is None:
        print("No ANTHROPIC_API_KEY: using the code proposer.")
    prev = None
    if args.append and settings.BANK_PATH.exists():
        prev = json.loads(settings.BANK_PATH.read_text())
        log_path = settings.DATA_DIR / "curriculum_log.json"
        prev["log"] = json.loads(log_path.read_text()) if log_path.exists() else []
    res = run_curriculum(args.rounds, args.per_round, llm=llm, home_share=args.home_share, previous=prev)
    res["llm_runs"] = ((prev.get("llm_runs") or [prev["llm"]]) if prev else []) + [res["llm"]]
    save_bank(res)
    fams = {}
    for r in res["bank"]:
        fams[r["family"]] = fams.get(r["family"], 0) + 1
    print(f"\nKept {res['n_kept']} of {res['n_tried']} valid days in {res['seconds']:.0f}s. By family: {fams}")
    print(f"LLM: {res['llm']}. Bank -> {settings.BANK_PATH}")
