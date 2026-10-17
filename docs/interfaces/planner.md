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
| GET | `/api/planner/feed?limit=50` | | `planner_change` events, newest first (below) |
| GET | `/api/planner/proposals` | | Changes waiting for the crew lead |
| POST | `/api/planner/proposals/{id}/approve` or `/reject` | | The updated `planner_change`. Approve re-runs the rule checker first; 404 if already decided or expired |

**Acting on forecasts: one line for A.** At startup, on the event loop, after the planner bridge:

```python
from backend.planner.proactive import attach
attach(hub)      # listens to "estimate" events, broadcasts "planner_change" events via hub.dispatch
```

It only reads `estimate` events (`forecast.p90[19]` = the 20-min 90th pct, `core_temp`) and only changes the planner's current plan (so your bridge pushes the new schedule as usual). It never sends band commands and never touches alerts, man-down or emergency. **Privacy:** `planner_change` has `forecast_p90_c`, `peak_before_c`, `peak_after_c`; drop those three for the crew view (the `message` has no body numbers).

- `set_scenario(scenario, offsets=None)`: call when the crew, tasks or forecast change. `offsets` = `{worker_id: PersonalOffset or its to_dict()}` from `backend/models/personal_offset.py`; default reads `data/personal_offsets.json` (same shape) if A saves them there. For a **mid-shift replan**, set each worker's `start_core_c` to their current core temp estimate and cut `weather` to the remaining slots.
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
    "index_notes": ["Trained on placeholder sites; ..."],
    "data_sources": {"index": "C2" | "stub", "hazards": "C1" | "C1 (fake demo data)" | "stub",
                     "weather": "C1" | "C1 (fake demo data)" | "stub", "incidents": "C2" | "stub"}
  }],
  "schedule_suggestions": ["Outdoor work stops 14:00-15:00 for the storm window ...", "..."],
  "schedule": { "...Schedule above..." },
  "plan": { "...plan view below..." },
  "crew_site_id": "eia-663",
  "crew_data_sources": {"...": "same as data_sources, for the crew site"},
  "personalized_workers": ["w1"],
  "stubs": ["0-10 hazard index (session C2 not merged): rough WBGT/storm formula", "..."],
  "cost_usd": 0.0
}
```
Show `stubs` in small print while it isn't empty (it says which data is placeholder). Sites are sorted by index, highest first. `top_hazards`: tomorrow's items first (NWS alerts running into tomorrow, max WBGT, HeatRisk), then current conditions with text starting `Now: `.

Ready-made demo briefings (3 real sites, real C1 + C2 data, no LLM): `backend/planner/demo/briefing_live_2026-10-18.{json,txt}`, and the same crew on C1's fake hot demo day (labeled): `briefing_demo_fake_2026-10-18.{json,txt}`.

**Plan view** (in `/api/planner/plan`, `/api/planner/check`, chat, briefing)
```json
{"source": "agent:fused", "verified": true, "violations": [],
 "workers": [{"id": "w1", "name": "Priya", "peak_p90_c": 37.93,
              "slots": [{"slot": 0, "time": "07:00", "task": "t3", "task_name": "Vegetation clearing"}]}],
 "tasks": [{"id": "t3", "name": "Vegetation clearing", "done": 4, "needed": 4, "priority": 1}]}
```
Privacy: `peak_p90_c` is a planning forecast, not a live reading, but it's still about a body. Keep it out of the crew view if you want to be strict; the `Schedule` shape has no body numbers.

**Agent result** (`POST /api/planner/plan`): `{plan, alternatives[{name, ...plan view, score}], summary, reasons[], lessons_used[], log[], latency_s, cost_usd, candidates[{name, verified, violations, score}]}`. `log` is the pipeline story (candidates, what the rule checker rejected, clusters, critic, fusion), good for a "how the agent decided" panel.

### planner_change (feed event, also over the WebSocket once A calls `attach`)

```json
{"type": "planner_change", "id": "chg-12", "timestamp": 1792334400,
 "worker_id": "w1", "worker_name": "Priya", "kind": "swap",          // swap | break | lighter_task | defer_heavy
 "status": "applied",              // applied | proposed | approved | rejected | expired | rejected_by_rules
 "needs_approval": false, "why_needs_approval": null,               // "loses work" | "pushes work later"
 "message": "Swapped Priya to Control room checks at 9:00 AM; Kofi takes Vegetation clearing. Priya was forecast to run hot in the next 20 min.",
 "slot": 4, "slot_time": "09:00", "others": ["w5"],
 "throughput_delta": 0.0, "pushes_work_later": false, "checked_by_rules": true,
 "sms": {"sent": true, "mode": "dry_run", "to": "••• ••• 0100", "error": null},
 "forecast_p90_c": 37.82, "peak_before_c": 37.9, "peak_after_c": 37.71}     // body numbers: not for the crew view
```

UI: show `message` in a feed card. `status: "proposed"` gets Approve / Reject buttons (the POSTs above); it expires after `PLAN_PROPOSAL_EXPIRE_MIN` (15 min). The same `id` comes again when the status changes (upsert, like Alert). Texts are one-way: approvals only happen in the app.

When it acts: a worker's 20-min 90th pct forecast reaches `PLAN_EARLY_LINE_C` (37.7 °C, below the 38.0 yellow line). At most one change per worker per 20 min, 6 per shift, none while one of theirs is pending. Auto-applied only if safer for that worker, no crewmate ends up over the early line, no work is lost and nothing moves later; everything else is a proposal.

### Storm chances in plans

`SlotWeather` has `storm_prob` and `rain_prob` (0-1); `Task` has `elevated` and `hard_to_stop`. Likely-storm slots cost extra for outdoor work (double for elevated or hard-to-stop) as a SOFT cost, so that work moves earlier; the 10-mile lightning stop-work stays a hard rule. The plan's `reasons` (agent result) say it in words, e.g. "60% storms 1 PM-3 PM: Transformer maintenance at 7:30 AM-12:30 PM." The briefing has `storm_source` (open-meteo, open-meteo+nws, fake, nws, none). Old scenario JSON without these fields still loads (zeros / false).

## For C1 (site hazards) and C2 (hazard index)

Adapters live in `backend/planner/sources.py`. Each returns None on any failure (not merged, feed down, nothing cached) and the briefing falls back to labeled stubs. Tested for real on a throwaway combination of the three branches (C1 29119a1, C2 02a10fa).
- C1: `get_site` (site names), `latest_picture()` or `hazard_picture(write_log=False)` (events, `tomorrow` block), `site_weather([site])` hourly (temperature, humidity, 10 m wind, shortwave, Liljegren `wbgt`) → interpolated to the shift's 30-min slots for the crew's heat plan. NWS alerts (by title) and lightning stop-work events become hazard windows; events with no start/end (current readings) don't.
- C2: `predict_index(site_id, date)` (index, band, top_reasons, notes) and `past_incidents(site_id, limit=3)`.
- Not used yet: C2's `predict_all_sites` (the briefing scores only the sites it shows), `hazard_index` inside C1's picture.

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

**Weight search.** Every soft knob is in `PlannerWeights` (`backend/planner/weights.py`); hard limits are not (they're in `config.py` and the rule checker, and every plan must still pass it):

| Field | Default | What it trades |
| --- | --- | --- |
| `throughput` | 10 | weight on priority-weighted work done (> 0) |
| `margin` | 0 | keep everyone's peak heat low |
| `fairness` | 0 | keep peaks similar across the crew |
| `budget_margin_c` | 0.05 | plan this far under the heat limit, °C (≥ 0) |
| `priority_values` | {1: 10, 2: 5, 3: 2} | value of one work unit by task priority |
| `storm` | 15 | cost of outdoor work × storm chance (×2 elevated / hard-to-stop) |
| `rain` | 0 | cost of outdoor work × rain chance |
| `hot_break_bonus` | 0 | reward per rest slot when WBGT ≥ 30 °C |
| `early_line_c` | 37.7 | act-on-forecast trigger (≤ the 38.0 yellow line, enforced) |
| `early_horizon_min` | 20 | which forecast minute the trigger reads (1-30) |

`validate()` refuses an early line above the yellow line, a negative cushion, negative weights. Defaults give exactly the old behavior. Accepted by `optimize(..., weights=)`, `objectives_for(weights)` (the agent's 4 variants), `AgentSettings(weights=)`, `run_benchmark(..., weights=)`, `ProactiveLoop(weights=)`, `propose(..., weights=)`.

```python
from backend.planner.benchmark import score
from backend.planner.generate import train_scenarios, load_test_set
from backend.planner.weights import PlannerWeights

m = score(PlannerWeights(storm=25, margin=1), train_scenarios(20))          # search on TRAINING scenarios
m = score(best, load_test_set(), act_on_forecasts=True)                      # final report on the test set, once
# m: n_scenarios, tasks_completed_per_shift, throughput, minutes_above_yellow, minutes_above_red,
#    peak_p90_c, fairness_spread, safety_violations (must be 0), invalid, changes, per_scenario[...]
```

`score` uses the deterministic solver (same weights → same numbers) and the simulated true shift from `proactive_eval` (workers hotter than planned, hotter day). About 20-30 s per scenario (one solver thread), or ~2-5 min per scenario with `act_on_forecasts=True`. Live (non-deterministic) solves use `PLANNER_SOLVER_WORKERS` threads (default 8; set 2 when the laptop is shared).

- Read-only use of: `generate.train_scenarios` / `assert_not_test` (never learn from `test-*`), `benchmark.run_benchmark` / `score_plans`, `rules.check_plans`, `archon.plan_with_agent(scenario, playbook=, settings=AgentSettings(...), llm=)`.
- **Loop 2 output:** write a `Playbook` (`playbook.py`) to `backend/learning/playbook.json` (or set `PLANNER_PLAYBOOK`). `Playbook.add()` runs the code safety filter; only `avoid_intensity` hints reach the optimizer. The benchmark's `agent_day14` uses it automatically.
- **Loop 3 knobs:** everything in `archon.AgentSettings` (objectives, candidate counts, fusion, ranking weights, critic instructions). Nothing there touches safety.

## For the ML session: personal offsets and twins

Done: `backend/planner/personal.py` turns a `PersonalOffset` (`offset_c` = model minus thermometer, `confidence`) into the planner's worker fields: `core_offset_c = -offset_c` (note the sign: the planner's + means "runs hotter"), `core_sd_c = 0.15 × sqrt(1 - confidence)`, min 0.05. Guardrail in config: a worker counts as at most `PLAN_MAX_COOLER_OFFSET_C` (0.3 °C) cooler than the model; hotter is never capped. The chat agent's `get_crew_status` reports it in words. The offset was learned against the forecaster and shifts the planning model (same per-person bias idea, different model).

Other twins plug in through `HeatModel` in `backend/planner/heat_model.py` (`simulate(scenario, plans)`, `slot_costs(scenario)`), passed as `heat_model=` to `optimize`, `check_plans` and `plan_with_agent`.

## Safety constants

In `backend/config.py`, block "PLANNER SAFETY LIMITS": `PLAN_MAX_P90_C` (= `HEAT_WARNING_C`, 38.0), break rules, hot-WBGT threshold, lightning 10 mi / 30 min all-clear, unacclimatized rule, `PLAN_STOP_WORK_HAZARDS`, personal-offset caps, the early line + rate limits for acting on forecasts (`PLAN_EARLY_LINE_C`, `PLAN_PROACTIVE_*`, `PLAN_PROPOSAL_EXPIRE_MIN`), SMS limits (`PLAN_SMS_*`). Nothing learns or changes them.

## Env (.env)

`ANTHROPIC_API_KEY` (no key = everything still runs, without the LLM steps), optional `PLANNER_MODEL` (default `claude-opus-5-5`), `PLANNER_LLM=off` to force no-LLM mode. `PLANNER_STORM_MODE=live|fake|off`. SMS: `PLANNER_SMS_MODE=dry_run|twilio` (default dry run) with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`; it only texts `CREW_LEAD_NUMBER`, through session D's 911 guard and allowlist.
