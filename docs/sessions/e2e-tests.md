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
3. You should see about `142 passed, 1 skipped, 19 xfailed`. About 2-3 minutes (more while the ML training uses the CPU). One slow test (the no-LLM 6 PM briefing, 30 s to 7 min) is skipped unless you set `E2E_SLOW=1`. "xfailed" = a known bug or gap, listed below.
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

| `test_e2e_acceptance.py` | The acceptance items not covered elsewhere: parser fuzz (10,000 random lines), BTN:1 never escalates, `step_fused` = `step`, corrupt checkpoint -> Kalman-only, exact thresholds and no flicker, demo-threshold flag, router fallback, no command storms, flapping tilt switch, blocked numbers in 16 formats, live dialer never built, one call per incident, facts-only script, lightning edges (9.9/10.1 mi, quality, stale feed), NWS EH/XH, hazard index 0-10, bad requests 4xx, CORS, replay determinism, 6 workers x 8 h load. |

`e2e_support.py` has the tools (stepped clock, recording band, spy phone, app factory); `conftest.py` only exposes the fixtures.

## Fixtures for session E (real backend payloads)

All in `tests/e2e/fixtures/`, recorded from the real merged backend (sim shift + ens5 forecaster, C1 fake hazards, D dry run):

- `tests/e2e/fixtures/live/<type>.json`: a list of up to 8 distinct real examples per `/ws/live` message type: `alert`, `band_command`, `clock`, `demo`, `emergency`, `estimate`, `log`, `mandown` (checking, confirmed, SOS, null), `offset_update` (accepted and rejected, A's flat shape), `report`, `site_status` (C1's flat lightning fields), `sos`, `vitals`, `worker_status`.
- `tests/e2e/fixtures/live/emergency_status.json`: session D's raw step events (A folds these into `emergency` on `/ws/live`, but `lib/emergency.ts` can be tested on them).
- `tests/e2e/fixtures/live/snapshot_crew.json`, `snapshot_worker.json`, `snapshot_demo.json`: the first message per view (lists trimmed to keep them small).
- `tests/e2e/fixtures/rest/<route>.json`: `{request, status, body}` for every REST reply the UI reads, e.g. `get_api_state_crew.json`, `get_api_sites.json`, `get_api_schedule.json` (B's planner), `post_api_spot_check.json`, `post_api_workers_id_sos.json` / `..._again.json` (`ignored_active_incident`), `post_api_emergency_cancel.json`, `get_api_results.json` (404 today).

`test_saved_live_fixtures_match_contract` keeps them honest: every saved example must still match the contract.

## Real bugs found, sorted by demo impact (each has an xfail test)

| # | Demo impact | Bug | Failing test | Owner | Fix idea |
| --- | --- | --- | --- | --- | --- |
| 1 | **Kills the demo.** Server can die at startup with no internet | B's heat model (pythermalcomfort PHS, numba `parallel=True`) aborts the whole process when two threads run it at once (exit 134, "Concurrent access has been detected"). `planner_bridge` runs `plan_now` and `warm_briefing` in parallel at startup; FastAPI also runs planner routes on a thread pool. Offline (replay + fake hazards + planner on) it died within ~10 s. | `test_e2e_planner.py::test_planner_heat_model_survives_two_threads` (3 of 3 runs abort) | B + A | B: one `threading.Lock` around `HeatModel.simulate` / `slot_costs`. A: start `warm_briefing` after the first `plan_now`. Until fixed: run the demo with `--no-planner` if the WiFi is bad. |
| 2 | **On the projector.** Full teammate phone numbers on the man-down screen | D's dry-run/fake phones say "Dry run: would call +1352..." / "Calling +1352..."; A copies it into `EmergencyCall.steps[].text` for every view, crew included. | `test_e2e_privacy.py::test_A29_no_full_phone_number_reaches_any_dashboard` | D (A shows it) | `mask(number)` in `phones.py` lines 81 and 110, `voice_agent.py` line 83. |
| 3 | **Crashes the demo page.** Reload after a spot check | Snapshot `offsets` use A's flat shape; `live.tsx` stores them as `OffsetUpdate` without `toOffsetUpdate()`; `LearningCard` reads `offset.history.length`. | `test_e2e_contract.py::test_snapshot_offsets_are_offset_updates` | E (or A) | E: map snapshot offsets through `toOffsetUpdate()`. |
| 4 | **Visible lag.** "Test lightning" | Bands buzz at once, but the crew's site card (stop-work banner, ring, countdown) waits up to ~75 s (C1's 60 s rebuild + bridge's 15 s poll). | `test_e2e_hazards.py::test_demo_test_alert_shows_on_the_site_card_right_away` | A | After `bridge.test_alert`, rebuild C1's picture on a thread, then `bridge.refresh()`. |
| 5 | Judge waits | No fresh estimate after a spot check; the chart moves at the next minute (6 s at 10x replay, 60 s live). | `test_e2e_spot_check.py::test_fresh_estimate_is_pushed_right_after_a_spot_check` | A | Re-send the last estimate with the new offset. |
| 6 | **Live demo: misleading card.** If Pulsoid drops | No heart rate for 10+ minutes: the Kalman correctly runs predict-only, but the crew card still says "Normal", green. Nothing says heart rate is missing. | `test_e2e_acceptance.py::test_A05_crew_card_says_heart_rate_is_missing` | A | When no HR for 2+ min: `status_reason` "Heart rate missing" (and not a confident green). |
| 7 | **Safety gap; breaks the `--no-ack` demo path** | "Red with no button press for 60 s" starts a man-down check on time, but the next reading's movement cancels it in the same second (the overheating worker is still walking), so it can never escalate. | `test_A14_unacknowledged_red_escalates_while_the_worker_still_moves` | A | For `heat_red_unacknowledged` checks, only a button press cancels (movement shouldn't). |
| 8 | If the server restarts mid-demo | Dashboards lose the alert history (SQLite still has it) and the breaks on worker cards (schedule not saved) until the planner replans. Offsets do come back. | `test_A34_alert_history_comes_back_after_restart`, `test_A34_current_schedule_comes_back_after_restart` | A | Reload recent alerts from SQLite at startup; save/restore `hub.schedule`. |
| 9 | Live band only | The line parser accepts impossible values (`TEMP:nan`, `HR:inf`, `HR:-80`, `TEMP:1e9`): one glitched line can poison a minute or the Kalman. | `test_A01_impossible_values_never_make_readings` | A | Drop non-finite and out-of-range values in `parse_line`. |
| 10 | None in the UI (it sends numbers) | `POST /api/demo/replay {speed: "fast"}` -> 500 (unguarded `float()`). | `test_A30_demo_replay_bad_speed_is_4xx` | A | Validate with a pydantic model. |
| 11 | None visible | Spot checks outside 34-42 °C return 200 (contract: 400); they're only rejected as bad readings. | `test_A30_out_of_range_spot_check_is_4xx` | A | `Field(ge=34, le=42)` on `thermometer_c`. |
| 12 | None visible (privacy) | `GET /api/workers/{id}/history?view=worker&worker_id=me` ignores `worker_id`: any worker view reads anyone's vitals. | `test_A29_worker_view_cannot_read_another_workers_history` | A | 403 unless `worker_id` matches. |
| 13 | None visible | `/api/briefing` and `/api/schedule` don't say they were made without the LLM (only chat has `llm: false`). | `test_e2e_planner.py::test_A27_no_key_schedule_is_labeled_no_llm` | B | Add `llm: false` (or a "no-LLM" note) to both. |
| 14 | Bare chart page only | Raw `/ws` gets D's `emergency_status` before the `mandown_confirmed` that caused it. | `test_e2e_full_chain.py::test_raw_stream_sends_mandown_confirmed_before_its_escalation` | A | Broadcast before calling listeners. |

Contract drift, no demo impact (the UI copes today): `POST /api/workers/{id}/button` returns `{ok}` not `{ok, action}` (A); `POST /api/emergency/cancel` returns `{cancelled}` not `{ok}` (A); `GET /api/results` not served, UI uses its bundled copy (A + ML); C1 site `tech` has `hydro`, `battery`, `biomass_waste` (C1 + E). Tests: `test_e2e_contract.py::test_rest_replies_match_contract[...]`, `test_results_endpoint_is_served`, `test_site_tech_values_are_in_the_contract`.

## Acceptance items (docs/testing/backend-acceptance.md) -> tests

Tests are named after the items they cover (`test_A12_...`). All 36 items have tests.

| Item | Result | Tests |
| --- | --- | --- |
| A01 fuzz parser | pass + xfail (#9) | `test_A01_*` |
| A02 debug/garbage lines | pass | `test_A02_*` (UDP + serial) |
| A03 laptop timestamps, bursts, duplicates | pass | `test_A03_*` |
| A04 two HR sources | pass | `test_A04_*` |
| A05 no heart rate | pass (predict-only, recovery) + xfail (#6) | `test_A05_*` |
| A06 SOS vs button | pass | `test_A06_*` |
| A07 Kalman exactness | pass (Mendeley `ectemp` to 1e-9, `step_fused` = `step`) | `test_A07_*` |
| A08 forecaster fallback | pass | `test_A08_*` |
| A09 forecast sanity | pass (every minute, every worker, 34-42 °C, ordered, model output used exactly once) | `test_A09_*` |
| A10 offset applied once | pass | `test_A10_*` |
| A11 offset safety + restart | pass | `test_A11_*` |
| A12 exact thresholds | pass | `test_A12_*` |
| A13 demo threshold | pass | `test_A13_*` |
| A14 red repeat / ack / timeout | pass + xfail (#7) | `test_A14_*` |
| A15 router priorities | pass | `test_A15_*` |
| A16 commands on change + 20 s re-send | pass | `test_A16_*` |
| A17 man-down | pass | `test_A17_*`, full chain |
| A18 blocked numbers | pass (16 formats) | `test_A18_*` |
| A19 dry run by default | pass | `test_A19_*` |
| A20 one call per incident | pass | `test_A20_*` |
| A21 facts only | pass | `test_A21_*` |
| A22 lightning edges | pass | `test_A22_*` |
| A23 NWS mapping | pass | `test_A23_*` |
| A24 offline feeds | pass (live mode offline: all 9 feeds stale, no crash; last good copy served marked stale) | `test_A24_*` |
| A25 hazard index range | pass | `test_A25_*` |
| A26 rule checker gate | pass | `test_A26_*` |
| A27 no-key mode | pass (schedule, chat; briefing with `E2E_SLOW=1`, passed in 7 min here) + xfail (#13) | `test_A26_A27_*`, `test_A27_*` |
| A28 what-ifs | pass | `test_A28_*` |
| A29 privacy | pass + 2 xfail (#2, #12) | `test_A29_*` |
| A30 bad requests | pass + 2 xfail (#10, #11) | `test_A30_*` |
| A31 CORS | pass | `test_A31_*` |
| A32 replay determinism | pass | `test_A32_*` |
| A33 load | pass (6 x 8 h, every estimate reaches the dashboard, memory bounded) | `test_A33_*` |
| A34 restart | pass (offsets, SQLite alert log) + 2 xfail (#8) | `test_A11_A34_*`, `test_A34_*` |
| A35 config unchanged + no writes | pass | `test_A35_*` |
| A36 learned parts can't move thresholds | pass | `test_A36_*`, `test_A11_A35_A36_*` |

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
- `rest/get_api_briefing.json` is written only with `E2E_SLOW=1 E2E_WRITE_FIXTURES=1` (the briefing is slow).
