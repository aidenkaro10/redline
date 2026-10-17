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

## Results (round 1)

All numbers are from **simulated** days. "Simulated score" = share of priority-weighted work done, minus 0.25 per worker who would have hit a heat alert, minus 0.5 per °C the hottest worker ran above 37.5 (`settings.py`; a learning score, not a safety limit). Safety violations come from B's rule checker.

### Next to B's baselines, on B's 12 held-out test scenarios (never learned from)

| Planner | Passes rule checker | Safety violations | Throughput | Peak p90 (°C) | Simulated score | Workers alerted / day | Simulated peak (°C) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fixed (today's practice) | 0% | 107 | 0.86 | 40.48 | -0.091 | 1.94 | 38.43 |
| optimizer only | 100% | 0 | 0.68 | 37.99 | 0.437 | 0.19 | 37.90 |
| LLM only, no checker (B's cached Claude answers) | 8% | 38 | 0.86 | 38.66 | 0.138 | 1.67 | 38.11 |
| agent, day 1 | 100% | 0 | 0.67 | 37.99 | 0.445 | 0.17 | 37.86 |
| **agent, day 14 (learned playbook)** | 100% | **0** | 0.67 | 37.99 | **0.477** | **0.11** | 37.83 |
| **agent, evolved (GEPA) + playbook** | 100% | **0** | 0.61 | 37.94 | 0.467 | **0.03** | 37.77 |

B's own run on the same set: optimizer and full agent 0 safety violations; LLM only had violations in 92% of runs. Our table re-scores B's run-0 LLM answers from B's cache (no new calls).

Home-crew validation days (5): day 1 0.333 → day 14 0.349 → evolved 0.478, alerts 0.80 → 0.67 → 0.13 per day. Caveat: these days were also the ACE gate and the GEPA selection set, so B's test set above is the clean number.

Trade-off to say out loud: the evolved agent does less work (0.61 vs 0.67) to cut alerts by 80%. The day-14 playbook keeps the same throughput and still cuts alerts by a third.

### Loop by loop

| Loop | Result | Claude cost |
| --- | --- | --- |
| Curriculum (Loop 4) | 27 of 93 valid days kept (sometimes solved), all 5 families. Bank: 17 home train, 5 home val, 4 random train, 1 random val | $0.34 (final run); $0.76 incl. a first run that kept 9 of 82 under a stricter "solved" |
| ACE playbook (Loop 2), 14 days | Gate days 0.273 → 0.318, alerts 1.0 → 0.75 per day, 3 of 18 updates kept, 0 violations. 2 lessons (one by Claude, one by the facts-based code curator), each used 5 times | $0.14 (final run); $0.56 incl. 3 earlier runs |
| GEPA + DGM (Loop 3), 8 rounds | Validation 0.378 → 0.452 (best = a5, generation 2), 4 of 8 children accepted, Pareto front a0, a1, a5, a7, a8, 0 violations | $0.32 (final run); $0.63 incl. 1 earlier run |
| **Total, all of session F** | | **$1.95** (`data/learning/cost_log.jsonl`) |

The 2 learned lessons:
- L1 (Claude, day 2): "Don't give a new hire like Kofi the full moderate, heavy, very heavy run alone. Mix his shop repair work with lighter tasks or pair him with an experienced worker."
- L2 (code curator day 2, sharpened by Claude day 8): "Marcus heats up faster than forecast on moderate and heavy work from 10:00, even though he is acclimatized. Give him jobs like transformer maintenance before 10:00."

GEPA's best agent (a5): ranks plans with much more weight on predicted peak heat (`w_peak` 0.5 → 2.5) and on confirmed critic complaints (0.03 → 0.1), 4 s solves, plus an evolved strategy note ("Give unacclimatized new hires light or moderate tasks; heavy ones only in the earliest slots..."). The note only matters when the planner runs with Claude. This run evolved with the planner in no-LLM mode (Claude was the mutator only), which keeps a run under $0.50. Evolving with Claude inside the planner too (`--llm-planner`) would cost roughly $0.15 per plan, about $15 for the same 8 rounds.

### Demo artifacts (`backend/learning/demo/`, shapes in the interface doc)

- `playbook_growth.json`: 14 days, 18 gated updates (3 kept), safety-filter rejections, versions v0 → v3.
- `family_tree.json`: 9 agent versions (a0-a8), 4 accepted children, Pareto front, every edit's reason in Claude's words.
- `day1_vs_day14.json`: held-out heat-wave day `cur-7927950f` (WBGT up to 32.9 °C). Day 14 cites L1 and L2 (learned day 2), moves Kofi's 11:30 moderate work away, and his simulated peak drops **0.18 °C** (37.92 → 37.74). Score 0.302 → 0.355, same throughput, 0 violations both.
- **Read this before pitching it:** that's the clearest of 8 extra-hot held-out heat-wave days we tried. Across all 8, day 14 was better on 1, worse on 3 (one with a new alert), and the same on 4: mean 0.314 → 0.266. The evolved agent wasn't better there either (0.261). The gains on B's test set and the home validation days do **not** carry over to extreme heat waves yet. All 8 are in the JSON (`candidates`, `candidates_summary`). Next step: put extreme heat-wave days into the gate set and learn over more than 14 days.
- `summary.json`: zero safety violations in every version (ACE days, ACE gates, GEPA versions, demo days).

### Weight search: the most productive safe workday (`weights_search.py`)

B's `PlannerWeights` (merged from main) tuned by random search (21 settings + defaults on 6 training days), successive halving (best 6 on all 21 training days), then 4 local nudges. Hard constraint: zero rule-checker violations. Soft constraint: no more worker-minutes above the yellow line than the defaults. Goal: most tasks finished per shift. Scoring = B's `score()` pieces (optimizer, rule checker, B's simulated true shift, plan only), with a heat-model cache (weights don't change the heat tables) and 2 s deterministic solves. **$0 Claude** (no API calls), 28 min on 3 processes × 1 thread.

| | Tasks per shift | Priority-weighted work | Worker-min above yellow | Min above red | Fairness spread | Safety violations |
| --- | --- | --- | --- | --- | --- | --- |
| Training days (21), defaults | 3.76 | 0.737 | 56.6 | | 28.7 | 0 |
| Training days (21), tuned | 3.81 | 0.720 | 47.4 | | 37.0 | 0 |
| **Held-out (B's 12, scored once), defaults = optimizer-only baseline** | **4.08** | 0.683 | 97.4 | 0.67 | 35.4 | 0 |
| **Held-out, tuned** | **3.83** | 0.649 | **89.3** | 0.75 | 41.4 | 0 |

Tuned vs default: `margin` 0 → 4, `budget_margin_c` 0.05 → 0.075, priority values {10, 5, 2} → {16, 6, 1}, `storm` 15 → 22, `rain` 0 → 10, `hot_break_bonus` 0 → 4.

**Honest read:** on training days the tuned weights looked like a free win (slightly more tasks, 16% fewer heat minutes). On the held-out days they are a **safer, less productive** trade: 8% fewer heat minutes, 6% fewer tasks, slightly less fair. So we did NOT find a more productive safe day that holds up on held-out data. What holds up is the heat reduction. The training Pareto front (8 safe points, in `weights_tuning.json`) shows the real trade-off: the most productive safe setting (4.05 tasks) costs 21% more heat minutes than the defaults, and the coolest (32.9 min) gives up 11% of the tasks. Pick by what the site lead values; the defaults sit in the middle. Next step: a bigger search on more training days (each day takes ~25 s, so 4 settings × 21 days is about 6 minutes on 3 threads).

**In the self-improving story:** GEPA's genome now includes `PlannerWeights` (validated by `PlannerWeights.validate()`, clamped, mutated by Claude or the code mutator, gated on held-out days like every other knob), and `python -m backend.learning.gepa --start-weights` starts evolution from the tuned weights. `python -m backend.learning.ace --weights` runs the 14-day playbook loop on top of them. Neither has been rerun with weights yet. The weights can never move a safety limit: `validate()` refuses a negative budget margin or an early line above the yellow line, and the rule checker still gates every plan.

### What I learned building it (honest notes)

1. **Solver noise fooled the first gates.** B's CP-SAT stops on a wall-clock limit, so under a busy laptop the same day gave slightly different plans. My first ACE run "kept" updates that a deterministic rerun showed were noise (+0.026 → +0.000). Fix: inside the loops, the solver uses a deterministic work limit and 1 thread (`parallel.py`, no change to B's code). Every number above is from deterministic runs. The wall-clock runs are archived in `data/learning/old/wallclock/`.
2. **Gate each update alone.** Claude proposed 2-3 lessons per day; gated as a batch, a weak one sank a good one. Each update is now gated on its own.
3. **Claude + code curator.** Claude's lessons were often well worded but aimed at the wrong work (heavy only, when the measured problem was moderate work too). The curator now adds one lesson written in code from the measured facts. Both compete at the gate, and the kept lessons are a mix.
4. **The filter was too strict at first.** `\bharder\b` rejected harmless lessons like "the job gets harder once it ramps up". It now only rejects "work/push/go harder".
5. **"Solved" = no heat alert + at least 75% of priority-1 work.** Requiring 100% made most hot days unsolvable for reasons no lesson can fix (not enough hours).

### How to run each loop

```bash
python -m backend.learning.curriculum --llm --rounds 8 --per-round 12 --home-share 75   # ~15 min, ~$0.35
python -m backend.learning.ace --llm                                                   # ~55 min, ~$0.15
python -m backend.learning.gepa --llm --iterations 8 --n-train 21 --n-val 6           # ~30 min, ~$0.30
PLANNER_LLM_CACHE=<B's data/planner/llm_cache> python -m backend.learning.report      # ~25 min, $0
python -m backend.learning.weights_search                                             # ~30 min, $0 (search + held-out once)
python -m backend.learning.demo                                                        # ~15 min, $0 (DEMO_REUSE_DAY=1: 1 min)
```
Without `--llm` (or without a key) every loop still runs, with its plain-code fallback, for $0. `LEARNING_WORKERS=4` (default) uses 4 processes × 1 solver thread. Test: `python -m pytest backend/learning -q` (offline, fake Claude).

## Requests for B (planner)

1. `AgentSettings` has no field for the planner's LLM system prompt, so Loop 3 adds its evolved "strategy note" by wrapping the LLM (`gepa.NotedLLM`). A `strategy_note: str = ""` field in `AgentSettings` that `llm_ideas` / `llm_critic` append to `IDEAS_SYSTEM` would make that cleaner.
2. `plan_with_agent` returns `llm.cost_usd` (the LLM object's running total), not the cost of this one plan. Loops that reuse one LLM object get inflated per-plan costs. Fix: record `before = llm.cost_usd` at the start and return the difference. (Loop 3 already works around it.)
3. Speed: one agent plan takes 5-20 s, ~70% of it in the PHS ensemble inside `optimize()`'s verify loop and `check_plans`. Caching `simulate()` results per (scenario, plan assignments) would make every loop several times faster.
