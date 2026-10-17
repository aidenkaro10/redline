# Planner + 6 PM briefing (session B) interface

Code: `backend/planner/`. Progress and results: `docs/sessions/planner.md`.

The planner makes the crew's shift schedule in 30-minute slots and the 6 PM briefing. **Every schedule it returns has passed the code rule checker.** The LLM can propose, never approve. Alerts never go through the planner.

## For session A (live system): mount the router

```python
from backend.planner.api import router as planner_router, set_scenario
app.include_router(planner_router)
```

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/schedule` | | Frontend `Schedule` (below). Cached until the scenario changes. |
| GET | `/api/briefing?date=YYYY-MM-DD` | | Frontend `Briefing` + extras (below). Default date: tomorrow. Cached per date. |
| POST | `/api/planner/plan` | `{scenario?: Scenario, use_llm?: bool}` | Full agent result (below). Also becomes the current plan. |
| POST | `/api/planner/check` | `{scenario?: Scenario, plan: {assignments}}` | Rule checker verdict for any plan |
| POST | `/api/planner/chat` | `{message, session_id?}` | `{session_id, reply, tool_calls[], plan, llm, cost_usd}` |
| GET | `/api/planner/scenario` | | The Scenario the planner is working on |

- `set_scenario(scenario)`: call when the crew, tasks or forecast change. For a **mid-shift replan**, set each worker's `start_core_c` to their current core temp estimate and cut `weather` to the remaining slots.
- The agent takes ~5 s without the LLM, ~30-60 s with it. Run `/api/planner/plan` off the main loop (FastAPI runs sync endpoints in a thread pool).
- Live crew status in chat: `PlannerSession(scenario, crew_status_provider=fn)` where `fn(worker_id) -> dict` (e.g. `{"core_c": 37.6, "status": "yellow"}`). Without it the tool says "stub".
- Lightning stop-work counts as a break in `WorkerStatus.break_at`: the schedule marks those slots `stop_work`.

## For session E (frontend)

`/api/schedule` and `/api/briefing` match `Schedule` and `Briefing` in `frontend/src/lib/contract.ts`.

**Schedule**
```json
{
  "date": "2026-10-18", "slot_minutes": 30, "day_start": 1792321200,
  "rows": [{"worker_id": "w1", "name": "Priya",
            "slots": [{"start": 1792321200, "task": "heavy", "label": "Vegetation clearing"},
                      {"start": 1792323000, "task": "break", "label": "Shade break"},
                      {"start": 1792344600, "task": "stop_work", "label": "Stop work (lightning/storm)"}]}],
  "notes": ["Recommended: optimizer:balanced. Peak 90th pct core temp 37.93 °C (limit 38.0) ..."],
  "checked_by_rules": true
}
```
`task`: `heavy` (also very heavy), `moderate`, `light`, `break`, `stop_work`, `off`.

**Briefing** (frontend fields first, then extras)
```json
{
  "generated_at": 1792274400, "for_date": "2026-10-18", "summary": "Tomorrow: highest hazard index is ...",
  "sites": [{
    "site_id": "eia-663", "name": "Deerhaven Generating Station", "index": 6.4, "band": "extreme",
    "top_hazards": [{"kind": "heat", "level": "extreme", "text": "..."}],
    "windows": [{"kind": "lightning", "start_hour": 14.0, "end_hour": 15.0, "text": "Lightning 2 PM-3 PM (stop outdoor work)"}],
    "past_incidents": [{"date": "2024-07-18", "text": "...", "source": "..."}],
    "index_reasons": [{"hazard": "heat", "text": "..."}],
    "data_sources": {"index": "C2" | "stub", "hazards": "C1" | "stub", "incidents": "stub"}
  }],
  "schedule_suggestions": ["Outdoor work stops 14:00-15:00 for the storm window ...", "..."],
  "schedule": { "...Schedule above..." },
  "plan": { "...plan view below..." },
  "crew_site_id": "eia-663",
  "stubs": ["0-10 hazard index (session C2 not merged): rough WBGT/storm formula", "..."],
  "cost_usd": 0.0
}
```
Show `stubs` in small print while it isn't empty (it says which data is placeholder). Sites are sorted by index, highest first.

**Plan view** (in `/api/planner/plan`, `/api/planner/check`, chat, briefing)
```json
{"source": "agent:fused", "verified": true, "violations": [],
 "workers": [{"id": "w1", "name": "Priya", "peak_p90_c": 37.93,
              "slots": [{"slot": 0, "time": "07:00", "task": "t3", "task_name": "Vegetation clearing"}]}],
 "tasks": [{"id": "t3", "name": "Vegetation clearing", "done": 4, "needed": 4, "priority": 1}]}
```
Privacy: `peak_p90_c` is a planning forecast, not a live reading, but it's still about a body. Keep it out of the crew view if you want to be strict; the `Schedule` shape has no body numbers.

**Agent result** (`POST /api/planner/plan`): `{plan, alternatives[{name, ...plan view, score}], summary, reasons[], lessons_used[], log[], latency_s, cost_usd, candidates[{name, verified, violations, score}]}`. `log` is the pipeline story (candidates, what the rule checker rejected, clusters, critic, fusion), good for a "how the agent decided" panel.

## For C1 (site hazards) and C2 (hazard index)

The briefing calls, inside try/except, so nothing breaks before you merge:
- C2: `ml.hazard_index.predict.predict_index(site_id, date, offline=True)`. Uses `index`, `band`, `top_reasons[].text`.
- C1: `backend.hazards.picture.latest_picture()` (or `hazard_picture()`). Uses `sites[].site` (site list), `sites[].events[]` (`hazard`, `level`, `title`) for top hazards.
- Still a stub on both sides: **past incidents near a site** (OSHA + Storm Events). If either of you exposes `incidents_near(site_id, radius_mi) -> [{date, text, source}]`, tell me and I'll wire it.
- Still a stub: tomorrow's per-slot weather for the crew site comes from the planner's scenario. Next step: build it from C1's `site_weather([site])` hourly WBGT and NWS windows.

## Python API (no HTTP)

```python
from backend.planner.generate import demo_scenario, load_test_set
from backend.planner.archon import plan_with_agent          # full agent
from backend.planner.optimizer import optimize              # CP-SAT + verify loop
from backend.planner.rules import check_plan                # the rule checker
from backend.planner.briefing import make_briefing, render_text
from backend.planner.agent import PlannerSession            # chat + tools

r = plan_with_agent(demo_scenario())       # r.plan, r.check (ok, violations, peak_p90), r.reasons, r.log
```

**Scenario JSON** (`backend/planner/scenario.py`): `id, site{id,name,lat,lon}, date, shift_start "07:00", slot_min 30, weather[{tdb, rh, solar, wind, wbgt}] (one per slot), crew[{id, name, weight, height, age, acclim 0-1, clo, skills[], start_core_c?, available? [slots], core_offset_c, core_sd_c}], tasks[{id, name, intensity light|moderate|heavy|very_heavy, units, outdoor, priority 1-3, earliest, deadline?, skill?}], hazards[{kind, start_slot, end_slot, source, note}], tags[]`.

**Plan JSON**: `{"assignments": {"w1": ["t3", "rest", ...one per slot]}, "source", "notes", "lessons_used"}`.

## For session F (learning loops)

- Read-only use of: `generate.train_scenarios` / `assert_not_test` (never learn from `test-*`), `benchmark.run_benchmark` / `score_plans`, `rules.check_plans`, `archon.plan_with_agent(scenario, playbook=, settings=AgentSettings(...), llm=)`.
- **Loop 2 output:** write a `Playbook` (`playbook.py`) to `backend/learning/playbook.json` (or set `PLANNER_PLAYBOOK`). `Playbook.add()` runs the code safety filter; only `avoid_intensity` hints reach the optimizer. The benchmark's `agent_day14` uses it automatically.
- **Loop 3 knobs:** everything in `archon.AgentSettings` (objectives, candidate counts, fusion, ranking weights, critic instructions). Nothing there touches safety.

## For the ML session: personal twins

The heat model is behind `HeatModel` in `backend/planner/heat_model.py` (two methods: `simulate(scenario, plans)` and `slot_costs(scenario)`). Twins plug in either way:
1. Easy: set each worker's `core_offset_c` (runs hotter/cooler than the model) and `core_sd_c` (smaller once the twin is confident).
2. Full: a new class with the same two methods, passed as `heat_model=` to `optimize`, `check_plans` and `plan_with_agent`.

## Safety constants

In `backend/config.py`, block "PLANNER SAFETY LIMITS": `PLAN_MAX_P90_C` (= `HEAT_WARNING_C`, 38.0), break rules, hot-WBGT threshold, lightning 10 mi / 30 min all-clear, unacclimatized rule, `PLAN_STOP_WORK_HAZARDS`. Nothing learns or changes them.

## Env (.env)

`ANTHROPIC_API_KEY` (no key = everything still runs, without the LLM steps), optional `PLANNER_MODEL` (default `claude-opus-5-5`), `PLANNER_LLM=off` to force no-LLM mode.
