"""
The teacher: run session B's full planner agent (Claude Opus 5.5 + optimizer + rule checker)
on TRAINING scenarios and save everything the student could learn from.

For each scenario we save (data/local_planner/teacher_runs/<id>.json):
  - the scenario
  - every Claude call: what it was for (ideas / critic / explain), the prompt, Claude's answer, cost
  - the agent's final plan (always passed the rule checker), its score, its reasons
  - every candidate's verdict and score, including Claude's own direct schedule ("llm:direct")

Money: a hard cap (default $30) shared by all threads. Before each call we add up a worst-case
estimate; if it would go over the cap, the call is refused and that scenario is dropped.
Every call is logged to data/local_planner/cost_log.jsonl.

Run:  python -m ml.local_planner.teacher --cap 30 --workers 3
      (re-running skips scenarios that already have a saved run, so it resumes after a stop)
"""

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ml.local_planner.common import DATA_DIR, limit_solver_threads, out_dir

from backend.planner.archon import IDEAS_SYSTEM, AgentSettings, plan_with_agent, score
from backend.planner.generate import assert_not_test
from backend.planner.llm import LLM, MODEL, PRICES
from backend.planner.scenario import Scenario
from ml.local_planner.scenarios import split_of, training_scenarios

RUNS_DIR = DATA_DIR / "teacher_runs"
COST_LOG = DATA_DIR / "cost_log.jsonl"
POOL_PATH = DATA_DIR / "pool.json"
WORST_CASE_OUT_TOKENS = 8000          # thinking + answer, used only for the cap check


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Dollars spent so far, shared by all threads. Refuses calls that could push us over the cap."""

    def __init__(self, cap_usd: float, already_spent: float = 0.0):
        self.cap = cap_usd
        self.spent = already_spent
        self.reserved = 0.0
        self.lock = threading.Lock()

    def reserve(self, est: float) -> None:
        with self.lock:
            if self.spent + self.reserved + est > self.cap:
                raise BudgetExceeded(f"cap ${self.cap:.2f}: spent ${self.spent:.2f}, in flight ${self.reserved:.2f}")
            self.reserved += est

    def settle(self, est: float, actual: float) -> float:
        with self.lock:
            self.reserved -= est
            self.spent += actual
            return self.spent


def call_kind(system: str | None, prompt: str) -> str:
    """Which archon step made this call (so the dataset builder can treat each kind differently)."""
    if prompt.startswith("Write 3 short plain-language bullet points"):
        return "explain"
    if system == IDEAS_SYSTEM and prompt.startswith("Scenario:"):
        return "ideas"
    if system == IDEAS_SYSTEM and "Verified plans:" in prompt:
        return "critic"
    return "other"


class RecordingLLM(LLM):
    """B's Claude wrapper + the shared budget + a record of every prompt and answer."""

    def __init__(self, budget: Budget, scenario_id: str, skip_critic: bool = True, **kw):
        super().__init__(**kw)
        self.skip_critic = skip_critic
        self.budget = budget
        self.scenario_id = scenario_id
        self.records = []

    def text(self, prompt, system=None, cache_key=None, max_tokens=16000, effort=None):
        if self.skip_critic and call_kind(system, prompt) == "critic":
            # The LLM critic is half the cost and its prompts (~9k tokens) are too long for the
            # student anyway. The code critic still runs inside the agent.
            self.records.append({"kind": "critic", "skipped": True, "cost_usd": 0.0})
            return '{"complaints": []}'
        before = self.cost_usd
        p_in, p_out = PRICES.get(self.model, (4.0, 20.0))
        est = ((len(prompt) + len(system or "")) / 3.0 * p_in + WORST_CASE_OUT_TOKENS * p_out) / 1e6
        self.budget.reserve(est)
        t0 = time.time()
        try:
            out = super().text(prompt, system=system, cache_key=None, max_tokens=max_tokens, effort=effort)
        finally:
            cost = self.cost_usd - before
            total = self.budget.settle(est, cost)
        kind = call_kind(system, prompt)
        self.records.append({"kind": kind, "system": system, "prompt": prompt, "response": out,
                             "effort": effort or self.effort, "cost_usd": cost, "latency_s": time.time() - t0})
        with open(COST_LOG, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "scenario": self.scenario_id,
                                "kind": kind, "model": self.model, "cost_usd": round(cost, 5),
                                "total_usd": round(total, 4)}) + "\n")
        return out


def spent_so_far() -> float:
    """Total from the cost log (so a resumed run counts what earlier runs already spent)."""
    if not COST_LOG.exists():
        return 0.0
    return sum(json.loads(line)["cost_usd"] for line in COST_LOG.read_text().splitlines() if line.strip())


def run_one(sc: Scenario, budget: Budget, skip_critic: bool = True) -> dict | None:
    """One full agent run with Claude. Returns the saved record, or None if the budget stopped it."""
    assert_not_test(sc)
    llm = RecordingLLM(budget, sc.id, skip_critic=skip_critic, model=MODEL)
    try:
        r = plan_with_agent(sc, playbook=None, llm=llm)
    except BudgetExceeded:
        return None
    rec = {
        "scenario": sc.to_dict(), "split": split_of(sc.id), "model": MODEL,
        "final": {"assignments": r.plan.assignments, "source": r.plan.source, "ok": r.check.ok,
                  "n_safety": r.check.n_safety, "summary": r.summary, "reasons": r.reasons},
        "candidates": [{"name": c.name, "ok": bool(c.check and c.check.ok),
                        "n_safety": c.check.n_safety if c.check else None,
                        "n_invalid": (len(c.check.violations) - c.check.n_safety) if c.check else None,
                        "rules": sorted({v.rule for v in c.check.violations}) if c.check else [],
                        "score": c.score,
                        "assignments": c.plan.assignments if c.name == "llm:direct" else None}
                       for c in r.candidates],
        "final_score": score(sc, r.plan, r.check, AgentSettings()) if r.check.ok else None,
        "llm_critic": not skip_critic, "log": r.log, "calls": llm.records, "cost_usd": llm.cost_usd, "latency_s": r.latency_s,
    }
    (RUNS_DIR / f"{sc.id}.json").write_text(json.dumps(rec))
    return rec


def load_pool() -> list[Scenario]:
    """The training pool, cached on disk (building F's scenarios takes ~2 min)."""
    if POOL_PATH.exists():
        return [Scenario.from_dict(d) for d in json.loads(POOL_PATH.read_text())]
    pool = training_scenarios()
    POOL_PATH.write_text(json.dumps([sc.to_dict() for sc in pool]))
    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=30.0, help="hard US$ cap for ALL teacher runs together")
    ap.add_argument("--workers", type=int, default=3, help="scenarios planned at once")
    ap.add_argument("--with-critic", action="store_true", help="also ask Claude for the critic step (2x cost)")
    ap.add_argument("--limit", type=int, default=0, help="only run this many new scenarios (0 = all)")
    args = ap.parse_args()

    out_dir(RUNS_DIR)
    limit_solver_threads()
    pool = load_pool()
    todo = [sc for sc in pool if not (RUNS_DIR / f"{sc.id}.json").exists()]
    if args.limit:
        todo = todo[:args.limit]
    budget = Budget(args.cap, spent_so_far())
    print(f"{len(pool)} training scenarios, {len(todo)} to run. Spent so far ${budget.spent:.2f} of ${args.cap:.2f}.",
          flush=True)

    t0, done, stopped = time.time(), 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, sc, budget, not args.with_critic): sc for sc in todo}
        for fut in as_completed(futs):
            sc = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:                       # one bad run never stops the rest
                print(f"  {sc.id}: ERROR {type(e).__name__}: {e}", flush=True)
                continue
            if rec is None:
                stopped += 1
                continue
            done += 1
            direct = next((c for c in rec["candidates"] if c["name"] == "llm:direct"), None)
            print(f"  [{done}] {sc.id:16s} final ok={rec['final']['ok']} "
                  f"claude direct ok={direct['ok'] if direct else None} "
                  f"${rec['cost_usd']:.3f} total ${budget.spent:.2f} ({time.time() - t0:.0f}s)", flush=True)
    print(f"Done: {done} runs saved, {stopped} stopped by the ${args.cap:.2f} cap. Total spent ${budget.spent:.2f}.")


if __name__ == "__main__":
    main()
