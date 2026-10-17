# Session F: agent learning loops

Branch `claude/agent-learning-d87832` (started from B's `claude/planner-e28a9e`). Owns `backend/learning/`. Interface for B and E: [docs/interfaces/agent-learning.md](../interfaces/agent-learning.md). Design: [docs/research/self-improving-design.md](../research/self-improving-design.md).

## What's built (round 1)

| Step | File | Status |
| --- | --- | --- |
| Shared: simulated world | `world.py` | The "real day" the loops learn from: forecast error, per-person and day-to-day variation, and hidden traits the planner isn't told (new hires tire faster from 11:00; Marcus runs hot after lunch; a dead watch hides a warmer worker). Same PHS engine as the planner. |
| Shared: Claude with a hard cap | `llm_budget.py` | Reserves the worst case before each call, refuses calls that could go over the cap, logs every call to `data/learning/cost_log.jsonl`. Errors never crash a loop. `fake_llm.py` for tests. |
| Shared: safety filter | `safety.py` | B's lesson filter + a stricter one (no "push", "used to the heat", alerts, "without a break", ...). Also filters evolved prompt notes. |
| 1. Curriculum (Loop 4) | `scenarios.py`, `curriculum.py` | Proposer (Claude or code) writes bounded scenario specs for 5 stress families, code validates, the agent plans, 6 simulated days give a solve rate, keep 0 < rate < 1 (reward = 1 - rate). |
| 2. Playbook (ACE, Loop 2) | `ace.py` | Generator / reflector / curator / gate, 14 simulated days, every version saved, `restore(v)`. |
| 3. Evolution (GEPA + DGM, Loop 3) | `gepa.py` | Reflective mutation (Claude or code) on training traces, minibatch check, Pareto archive over 5 metrics, novelty + few-children parent selection, final pick on validation only. |
| 4. Demo artifacts | `demo.py`, `report.py` | JSON in `backend/learning/demo/`, shapes in the interface doc. Report next to B's baselines. |

## Results so far

**Curriculum (Loop 4), real Claude proposer:** 27 of 93 valid days kept (solved sometimes, not always), all 5 families: heat dome 6, 2 pm storm 7, new hire 6, call-out 5, dead watch 3. Split: 17 home-crew train, 5 home val, 4 random train, 1 random val. Cost **$0.34** (7 calls, $0.50 cap; the cap blocked the 8th call and the code proposer took over). An earlier run that required 100% of priority-1 work kept only 9 of 82 ($0.31, archived in `data/learning/old/`), so "solved" now = no heat alert and at least 75% of priority-1 work done, and the proposer is told why each day failed.

**Calibration check (before the loops):** with the lesson "new hires: moderate/heavy work before 11:00", hot days gained +0.05 to +0.22 outcome score and lost Kofi's alerts; mild days moved -0.02 to +0.01. So the gate has a real signal to find.

**Not run yet:** ACE 14 days, GEPA, report next to B's baselines, demo JSON. B's baseline numbers don't exist yet either (B's `benchmark.json` hasn't been generated).

## Next (in order)

1. `python -m backend.learning.ace --llm` (14 days, 4 gate days, cap $1.50). Needs 14 home train + 4 home val days: the bank has 17 + 5.
2. `python -m backend.learning.gepa --llm` (Claude mutator, planner without LLM, cap $3; expect well under $1).
3. `python -m backend.learning.report` then `python -m backend.learning.demo`.
4. Fill in this file with the numbers next to B's baselines.


## Requests for B (planner)

1. `AgentSettings` has no field for the planner's LLM system prompt, so Loop 3 adds its evolved "strategy note" by wrapping the LLM (`gepa.NotedLLM`). A `strategy_note: str = ""` field in `AgentSettings` that `llm_ideas` / `llm_critic` append to `IDEAS_SYSTEM` would make that cleaner.
2. `plan_with_agent` returns `llm.cost_usd` (the LLM object's running total), not the cost of this one plan. Loops that reuse one LLM object get inflated per-plan costs. Fix: record `before = llm.cost_usd` at the start and return the difference. (Loop 3 already works around it.)
3. Speed: one agent plan takes 5-20 s, ~70% of it in the PHS ensemble inside `optimize()`'s verify loop and `check_plans`. Caching `simulate()` results per (scenario, plan assignments) would make every loop several times faster.
