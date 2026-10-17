# Session J: end-to-end and contract tests

Branch `claude/e2e-tests-e56bb9`. All code in `tests/e2e/` (plus `tests/e2e` added to `pytest.ini`).

These tests start the **real** merged app in-process (FastAPI's TestClient, no real ports) and drive
it like the demo does: replay, the band's line protocol, the dashboards' WebSockets, the REST
buttons. Time is moved by hand with a stepped clock, so a 45-minute replay takes seconds and runs
the same way every time.

Safety in the tests themselves: no real phone call (session D's dry-run phone with a spy, fake
555-01xx numbers only, whatever `.env` says), no internet (every off-machine connection fails the
test), C1 in fake mode, planner with `PLANNER_LLM=off`, the database and hazard logs in temp folders.

## How to run

1. Open a terminal in the repo (or this worktree).
2. Run the e2e suite:
   ```bash
   /Users/Annabelle/Documents/projects/planit-control/.venv/bin/python -m pytest tests/e2e -q
   ```
3. You should see about `91 passed, 11 xfailed` in about a minute. "xfailed" = a known bug or gap, listed below.
4. The real-demo test needs `data/sim/sim.npz` and `ml/checkpoints/ens5.pt`. They're gitignored; the tests find them in the main checkout on their own, and skip that one test if they're missing.
5. To see why one known bug fails, run it with `--runxfail`, for example:
   ```bash
   /Users/Annabelle/Documents/projects/planit-control/.venv/bin/python -m pytest tests/e2e -q --runxfail -k phone_number
   ```
6. To rewrite the JSON fixtures for the frontend (only when the backend's shapes change on purpose):
   ```bash
   E2E_WRITE_FIXTURES=1 /Users/Annabelle/Documents/projects/planit-control/.venv/bin/python -m pytest tests/e2e/test_e2e_contract.py tests/e2e/test_e2e_planner.py -q
   ```

One test to check it works: `pytest tests/e2e/test_e2e_full_chain.py -q` should say `2 passed, 1 xfailed`.

## What each file proves

| File | What it proves |
| --- | --- |
| `test_e2e_full_chain.py` | The scripted demo, start to finish, in the real app. Real version (sim shift 9647 + ens5): Jake yellow at replay min 5-15 (it's 9), red at 30-40 (it's 38), his button 20 s later acknowledges it; Maria falls at min 40, `BUZZ:MANDOWN` 45 s later, `mandown_confirmed` 60 s after that with location, vitals and crewmates; session D's escalation runs in dry run in the exact documented order (crew lead, safety officer, 30 s cancel window, then "Demo: would call 911"), only our fake numbers are ever dialed, never a blocked one; the crew dashboard gets status, alerts, man-down and the call, and no raw vitals. Also runs on the built-in synthetic shift with Kalman only (no data files). |
| `test_e2e_band_protocol.py` | The test acts as the band over real UDP and over a pty serial port: `TEMP`, `TILT/ACT`, `HR` become readings stamped by the laptop; `#` lines and 12 kinds of garbage are ignored; `BUZZ:` commands come back down the same link by priority (MANDOWN beats LIGHTNING), `BTN:1` cancels a man-down; the first keep-alive arrives within a tick of joining and repeats after 20 s (not sooner, never to replay workers); `BTN:SOS` escalates at once, can be cancelled by the crew lead, and is never sent to the band. |
| `test_e2e_privacy.py` | Four dashboards at once (crew, worker's own phone, demo, worker with no id) through heat alerts, a spot check, a fall and an SOS. The crew view never gets vitals, core temp, forecast, personal offset or band commands (live, snapshot, REST); `history?view=crew` is 403; the worker view only gets its own body data; the demo view gets everyone's. The man-down `last_vitals` exception for responders works. |
| `test_e2e_contract.py` + `contract_schemas.py` | Records every `/ws/live` message type and every REST reply the UI uses, and checks each against `frontend/src/lib/contract.ts` (written as small Python schemas, accepting the looser shapes the UI already normalizes). Checks every path the real UI calls exists. Saves the real payloads as fixtures for session E. |
| `test_e2e_spot_check.py` | Thermometer spot check -> `offset_update` (miss, offset = misses / (checks + 2), confidence) -> the next estimate AND every forecast percentile shift by exactly the offset (two identical runs compared) -> thresholds untouched -> the offset comes back after a server restart on the same SQLite file. |
| `test_e2e_hazards.py` | C1 fake mode with an explicit clock: 2 test strikes 3 mi from Deerhaven -> stop work, `BUZZ:LIGHTNING` to both bands there and not to a band 80+ mi away, the crew card shows the strikes and an all-clear at +30 min; a new strike at +20 min resets it to +50; still stopped at +49, all clear (`BUZZ:OFF`) at +50.5. One flash only, or a storm 12 mi away, does not stop work. Flood test alert -> `BUZZ:FLOOD_FIRE` and back. |
| `test_e2e_safety.py` | No code outside tests writes to `backend.config`; the safety numbers match PROJECT.md (38.0/38.5, 10 mi/30 min/2 flashes, Kalman parameters, 115200 baud, 911 blocked ...); a busy session (demo-threshold toggles with sneaky extra fields, 12 "cool" spot checks, a wild reading, hazards, SOS) changes no config constant; the personal offset stops at the 0.6 °C cap and a hot worker stays red. |
| `test_e2e_planner.py` | B's real endpoints on a small 3-hour, 2-person day with a lightning window: `/api/planner/plan` returns a verified plan, we re-check it ourselves (in-process and via `/api/planner/check`), no outdoor work in the stop-work slots, `/api/schedule` says `checked_by_rules: true`; a black-flag day still gets a verified plan; on a day where nothing passes, the planner says so (`checked_by_rules: false`, "No verified plan") instead of pretending; an all-day heavy plan through the lightning is rejected. |

`e2e_support.py` has the tools (stepped clock, recording band, spy phone, app factory); `conftest.py` only exposes the fixtures.

## Fixtures for session E (real backend payloads)

All in `tests/e2e/fixtures/`, recorded from the real merged backend (sim shift + ens5 forecaster, C1 fake hazards, D dry run):

- `tests/e2e/fixtures/live/<type>.json`: a list of up to 8 distinct real examples per `/ws/live` message type: `alert`, `band_command`, `clock`, `demo`, `emergency`, `estimate`, `log`, `mandown` (checking, confirmed, SOS, null), `offset_update` (accepted and rejected, A's flat shape), `report`, `site_status` (C1's flat lightning fields), `sos`, `vitals`, `worker_status`.
- `tests/e2e/fixtures/live/emergency_status.json`: session D's raw step events (A folds these into `emergency` on `/ws/live`, but `lib/emergency.ts` can be tested on them).
- `tests/e2e/fixtures/live/snapshot_crew.json`, `snapshot_worker.json`, `snapshot_demo.json`: the first message per view (lists trimmed to keep them small).
- `tests/e2e/fixtures/rest/<route>.json`: `{request, status, body}` for every REST reply the UI reads, e.g. `get_api_state_crew.json`, `get_api_sites.json`, `get_api_schedule.json` (B's planner), `post_api_spot_check.json`, `post_api_workers_id_sos.json` / `..._again.json` (`ignored_active_incident`), `post_api_emergency_cancel.json`, `get_api_results.json` (404 today).

`test_saved_live_fixtures_match_contract` keeps them honest: every saved example must still match the contract.

## Real bugs found (each has an xfail test naming it)

| # | Bug | Failing test | Owner | Fix idea |
| --- | --- | --- | --- | --- |
| 1 | **Server crash.** B's heat model (pythermalcomfort PHS, numba `parallel=True`) aborts the whole Python process when two threads run it at once ("Numba workqueue threading layer is terminating: Concurrent access", exit 134). A's server does exactly that: at startup `planner_bridge` runs `plan_now` and `warm_briefing` in parallel, and FastAPI runs the planner's sync routes on a thread pool. I hit it starting the app offline (replay + fake hazards + planner on): dead within ~10 s. With internet the briefing is slow to reach the model, so it's rarer but still possible. | `test_e2e_planner.py::test_planner_heat_model_survives_two_threads` (3 of 3 runs abort) | B (heat model) + A (bridge) | B: one `threading.Lock` around `HeatModel.simulate` / `slot_costs`. A: start `warm_briefing` only after the first `plan_now` finishes. |
| 2 | **Full phone numbers reach every dashboard, crew included.** D's dry-run and fake phones say "Dry run: would call +1352..." / "Calling +1352..." with the whole number; A copies that message into `EmergencyCall.steps[].text`. Only `number_masked` should go out. | `test_e2e_privacy.py::test_no_full_phone_number_reaches_any_dashboard` | D (`phones.py` lines 81 and 110, `voice_agent.py` line 83) | Use `mask(number)` in those messages. |
| 3 | **A worker view can read another worker's raw vitals.** `GET /api/workers/{id}/history?view=worker&worker_id=me` ignores `worker_id`. | `test_e2e_privacy.py::test_worker_view_cannot_read_another_workers_history` | A | Take `worker_id` and return 403 unless it matches (as `/ws/live` already does). |
| 4 | **Demo page crash after a reload.** The snapshot's `offsets` use A's flat shape (no `history`, `checks`, `last_check`), but `live.tsx` stores them as `OffsetUpdate` without `toOffsetUpdate()`, and `LearningCard` reads `offset.history.length`. | `test_e2e_contract.py::test_snapshot_offsets_are_offset_updates` | E (or A) | E: map snapshot offsets through `toOffsetUpdate()`. Or A: send the contract shape. |
| 5 | "Test lightning" buzzes the bands at once, but the crew's site card (stop-work banner, ring, countdown) waits for C1's next 60 s rebuild plus the bridge's 15 s poll. | `test_e2e_hazards.py::test_demo_test_alert_shows_on_the_site_card_right_away` | A | After `bridge.test_alert`, rebuild C1's picture on a thread and refresh the bridge. |
| 6 | No fresh estimate after a spot check: the contract asks for one "so the chart moves while the judge watches"; A only re-checks the alert, so the chart waits for the next minute (60 s live). | `test_e2e_spot_check.py::test_fresh_estimate_is_pushed_right_after_a_spot_check` | A | Re-send the last estimate with the new offset applied. |
| 7 | Raw `/ws` gets D's `emergency_status` events before the `mandown_confirmed` that caused them (listeners run before the broadcast, D emits synchronously). Bare chart page only. | `test_e2e_full_chain.py::test_raw_stream_sends_mandown_confirmed_before_its_escalation` | A (minor) | Broadcast before calling listeners, or queue nested dispatches. |

Contract drift (UI copes today, but the contract and backend disagree):

| What | Test | Owner |
| --- | --- | --- |
| `POST /api/workers/{id}/button` returns `{ok}`; contract says `{ok, action}` | `test_rest_replies_match_contract[POST /api/workers/{id}/button]` | A |
| `POST /api/emergency/cancel` returns `{cancelled}`; contract says `{ok}` / `{ok: false, error}` | `test_rest_replies_match_contract[POST /api/emergency/cancel]` | A |
| `GET /api/results` not served (UI falls back to a bundled copy) | `test_results_endpoint_is_served` | A + ML session |
| C1's site `tech` has `hydro`, `battery`, `biomass_waste`; contract.ts lists only 6 values (shown as text, cosmetic) | `test_site_tech_values_are_in_the_contract` | C1 + E |

## Other things noticed (no test, worth a look)

- D's escalation runs on the wall clock while worker events are replay time (A's doc says to give D the replay clock; not done). The UI copes with two clocks. The tests swap in the replay clock.
- `/ws` and `/api/raw/*` have no privacy filter (debug tools), and `view=` is whatever the browser asks for. Fine for a hackathon prototype; say it if a judge asks about privacy.
- `/ws/live` messages share mutable dicts (the emergency call, alerts), so a queued message can show a later state than when it was sent; the UI may skip the "notifying" stage. Harmless today.
- The `schedule` event A sends on `/ws/live` isn't in contract.ts's `LiveEvent` (the UI ignores it). E can add it if wanted.
- On a day where even resting fails the heat rule, B's fallback is "everyone rests in the shade", labeled unverified. "Stop work, go indoors" may be the safer wording (B's call).
- The planner is slow on a busy laptop: one `GET /api/schedule` for B's full demo day took 112 s here while the ML training ran (B says ~5 s idle); the offline briefing took 18 s.

## Not covered

- The real Next.js app in a browser (would need Playwright and a browser download: ask Aiden first). The contract tests and fixtures stand in for it.
- Real hardware, real Pulsoid, live hazard feeds, the LLM planner, live phone calls (all by design).
- `ml/` tests weren't rerun here (the ML session is training on the CPU).
