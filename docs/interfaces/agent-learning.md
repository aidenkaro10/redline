# Agent learning loops (session F) interface

Code: `backend/learning/`. Progress and results: `docs/sessions/agent-learning.md`.

Three offline loops make the planning agent better without ever touching safety:

| Loop | What changes | Learns from | Output |
| --- | --- | --- | --- |
| 4. Curriculum | The training scenarios | Where the current agent sometimes fails | `backend/learning/bank/train_bank.json` |
| 2. Playbook (ACE) | Lessons the planner reads | Plan vs simulated outcome, day by day | `backend/learning/playbook.json` |
| 3. Evolution (GEPA + DGM) | The agent's strategy settings + prompt note (+ PlannerWeights) | Traces on training scenarios | `backend/learning/best_agent.json` |
| Weight search | B's PlannerWeights (soft optimizer weights) | Training scenarios, B's simulated shift | `backend/learning/best_weights.json` |

Hard lines: limits live in `backend/config.py` and nothing here reads-and-changes them. Every plan from every version goes through B's rule checker. Lessons and evolved prompt notes pass a code safety filter (`safety.py`). Every playbook version is saved and can be restored. The 14 days are **simulated**, and every artifact says so.

## For session B (planner)

- The planner already reads `backend/learning/playbook.json` (B's `playbook.py`, `PLANNER_PLAYBOOK` overrides). Same format as B's `Playbook.save()`: `{"version", "lessons": [{id, text, hint, source, uses, helped}], "history"}`.
- Loop 3's best settings: `backend/learning/best_agent.json` = `{"id", "genome": {"settings": <AgentSettings fields>, "strategy_note": str}, "val", "why"}`. Load with `backend.learning.gepa.to_settings(genome)` and pass as `plan_with_agent(..., settings=...)`.
- Requests for B (not urgent): see "Requests for B" in `docs/sessions/agent-learning.md`.

## For session E (frontend): demo JSON

All in `backend/learning/demo/`. Serve them as static files, or A can add `GET /api/learning/{name}` that returns the file. Temperatures in °C. Every file is about **simulated** days: show the `label` / `note` text.

### `playbook_growth.json`: the playbook over 14 simulated days

```json
{
  "label": "Simulated days (not real shifts)",
  "days": [{
    "day": 3, "scenario": "cur-1a2b3c4d", "tags": ["heat_dome", "new_hire"],
    "outcome_score": 0.41, "workers_alerted": 1, "lessons_used": ["L1"],
    "root_cause": "Kofi (not acclimatized) heated up faster than forecast on moderate and heavy work from 11:00 ...",
    "reflection_by": "llm" | "code",
    "proposed": [{"op": "add" | "edit" | "merge", "text": "...", "result": "applied" | "rejected" | "skipped" | "merged near-duplicate", "why": "learning filter: ..."}],
    "gate": {"kept": true, "gain": 0.031, "why": "better on held-out days, zero violations",
             "score_before": 0.42, "score_after": 0.45, "safety_violations": 0} | null,
    "updates": [{"op": "add", "text": "...", "kept": true, "gain": 0.026}],
    "playbook_version": 2, "n_lessons": 2, "safety_violations": 0
  }],
  "versions": [{"version": 0, "day": 0, "gate_score": 0.42, "gate_safety_violations": 0,
                "lessons": [{"id": "L1", "text": "...", "source": "day 3"}]}],
  "final_lessons": [{"id": "L1", "text": "...", "hint": {"type": "avoid_intensity", "who": "unacclimatized",
                     "intensities": ["moderate", "heavy", "very_heavy"], "from_hour": 11, "penalty": 15},
                     "learned": "day 3", "uses": 9, "helped": 8}],
  "llm": {"model": "claude-opus-5-5", "calls": 12, "cost_usd": 0.31, "cap_usd": 1.5}
}
```
Suggested view: a line of `n_lessons` (or `playbook_version`) by `day`, a dot per day colored by `gate.kept` (green kept, grey rolled back, none = nothing to learn), and a red mark on any proposed lesson with `result: "rejected"` (the safety filter at work). `gate.score_before → score_after` is the evidence. Each lesson update is gated on its own: `updates[]` says which were kept.

### `family_tree.json`: agent versions from Loop 3

```json
{
  "nodes": [{
    "id": "a3", "parent": "a1", "generation": 2, "accepted": true, "pareto": true, "best": false,
    "edit": "Afternoon alerts on new hires: weigh peak heat more when ranking plans",
    "mutator": "llm" | "code" | "seed",
    "minibatch": {"parent": 0.31, "child": 0.36, "scenarios": ["cur-..."]},
    "scores": {"score": 0.44, "strain_margin_c": 0.21, "throughput": 0.52, "fairness": -24.1,
               "latency": -9.8, "cost": 0.0, "workers_alerted": 0.17, "safety_violations": 0} | null,
    "settings": {"objectives": ["balanced", "margin"], "fuse": true, "w_peak": 1.1, "w_fair": 0.002,
                 "w_complaint": 0.03, "n_llm_ideas": 3},
    "strategy_note": ""
  }],
  "best": "a3", "seed": "a0", "pareto_front": ["a0", "a3"], "gain_val": 0.02,
  "llm": {...}, "metrics_note": "..."
}
```
`accepted: false` = the child lost to its parent on the minibatch and was never added (draw it as a dead branch, `scores` is null). Tree edges are `parent → id`. Higher is better for every metric (`fairness`, `latency`, `cost` are negated).

### `day1_vs_day14.json`: same heat-wave day, two agents

```json
{
  "scenario": {"id": "cur-...", "site": "Deerhaven Generating Station", "date": "2026-07-15",
               "tags": ["heat_dome", "home_crew"], "max_wbgt_c": 33.9, "max_air_c": 38.2, "hazards": ["Heat Advisory ..."]},
  "note": "Simulated day, held out from all learning. ...",
  "day1": {
    "label": "Day 1 agent (empty playbook)",
    "schedule": [{"worker_id": "w6", "name": "Kofi (new hire)",
                  "slots": [{"slot": 0, "time": "07:00", "task": "light|moderate|heavy|break|stop_work|off", "label": "Pipe insulation"}],
                  "planned_peak_p90_c": 37.9, "simulated_peak_c": 38.2, "simulated_alert_minutes": 34}],
    "lessons_cited": [{"id": "L1", "text": "...", "learned": "day 3"}],
    "outcome": {"score": 0.17, "throughput": 0.75, "actual_peak_c": 38.4, "workers_alerted": 1, "priority1_done": true},
    "safety_violations": 0, "rule_checker_ok": true,
    "focus_worker": {"id": "w6", "name": "Kofi (new hire)", "heavy_or_moderate_after_11": ["13:00", "14:00"],
                     "simulated_core_c_every_10_min": [37.0, ...], "planned_p90_c_every_10_min": [37.1, ...], "peak_c": 38.42},
    "reasons": ["..."]
  },
  "day14": { "...same shape..." },
  "day14_evolved": { "...same shape, optional..." },
  "difference": {"focus_peak_drop_c": 0.4, "score_gain": 0.2, "alerts_avoided": 1},
  "candidates": [{"id": "cur-...", "spec": {...}, "day1": {"score": 0.37, "workers_alerted": 1, "focus_peak_c": 38.04,
                  "safety_violations": 0}, "day14": {...}, "day14_evolved": {...}}],
  "pick_rule": "Shown: the clearest of 8 held-out heat-wave days ... All candidates are listed in 'candidates'."
}
```
We tried 8 held-out heat-wave days and show the clearest one where the day-14 plan used a lesson. Show `pick_rule` in small print, and if there's room, a small table of `candidates` (honesty: most days change little).
`schedule[].slots[].task` uses the same words as B's `Schedule` (`heavy` includes very heavy). Chart idea: Kofi's `simulated_core_c_every_10_min` for day 1 vs day 14 with a line at 38.0 °C; under it, both schedules; on the day-14 side the cited lesson and the day it was learned.

### `weights_tuning.json`: default vs tuned planner weights

```json
{"label": "Simulated shifts. Weights picked on training days only; held-out days scored once.",
 "changed_weights": {"margin": {"default": 0, "tuned": 4}, "storm": {"default": 15, "tuned": 22}},
 "tuned_is_default": false,
 "train": {"n_days": 21, "default": {<metrics>}, "tuned": {<metrics>}},
 "held_out": {"n_days": 12, "default_optimizer_only": {<metrics>}, "tuned": {<metrics>}},
 "pareto_train": [{<metrics>, "weights": {...}}], "all_train": [{<metrics>}], "claude_cost_usd": 0.0}
```
`<metrics>` = `tasks_per_shift, throughput, minutes_above_yellow, minutes_above_red, peak_p90_c, fairness_spread, safety_violations, invalid`. Suggested view: a scatter of `pareto_train` (tasks per shift vs minutes above yellow, dot size = fairness spread) with default and tuned highlighted, and a small held-out table under it.

### `summary.json`

```json
{"safety_violations": {"ace_days": 0, "ace_gates": 0, "gepa_versions": 0, "demo_days": 0, "weight_search": 0}, "all_zero": true,
 "weights": {"n_days": 12, "default_optimizer_only": {...}, "tuned": {...}},
 "curriculum": {"kept": 20, "tried": 84, "llm": {...}},
 "ace": {"days": 14, "final_version": 3, "gate_score_start": 0.40, "gate_score_end": 0.46, "llm": {...}},
 "gepa": {"versions": 7, "best": "a3", "gain_val": 0.02, "llm": {...}},
 "held_out": {"test": {"agent_day1": {"success_rate": 1.0, "safety_violations": 0, "throughput": 0.6,
                                      "world_score": 0.4, "world_alerted_per_day": 0.2}, "...": {}}}}
```
Show `all_zero` as the "zero safety violations in every version" badge.

## Python API

```python
from backend.learning.curriculum import run_curriculum, load_bank, bank_scenarios
from backend.learning.ace import run_ace, restore            # restore(version) -> Playbook
from backend.learning.gepa import run_gepa, to_settings
from backend.learning.world import outcomes                  # simulated outcome of any plan
from backend.learning.demo import build_all
```
Every loop takes `llm=`: a `BudgetLLM` (real Claude with a hard dollar cap and a cost log), the `FakeLLM` (tests), or `None` (plain-code fallback).

## Env (.env)

`ANTHROPIC_API_KEY` (no key = every loop still runs with its code fallback), `LEARNING_BUDGET_CURRICULUM` / `_ACE` / `_GEPA` (hard caps in $, defaults 0.50 / 1.50 / 3.00), `LEARNING_WORKERS` (parallel processes, default 4). Every Claude call is logged to `data/learning/cost_log.jsonl`.
