# Backend acceptance tests

Written by the main session for session J (tests/e2e/) to turn into automated tests. These go beyond each session's own unit tests: they check the merged system and its safety rules, including edge cases. Every test runs offline (fake hazard mode, dry-run calls, fake phone, no-LLM planner, no network).

Rule: when a test fails because the backend is wrong, don't loosen it. Mark it `xfail(strict=True, reason="<bug> -> session <owner>")` and list it in docs/sessions/e2e-tests.md. The main session routes the fix.

## 1. Band protocol and inputs

1. **Fuzz the line parser.** 10,000 random byte strings and near-miss lines (`TEMP:`, `TEMP:abc`, `ACC:1,2`, `BTN:2`, very long lines, NUL bytes) never crash and never produce a reading. Valid lines always do.
2. **Debug and garbage lines.** Lines starting with `#` and boot noise are ignored; the next valid line still works.
3. **Laptop timestamps.** Readings are stamped on arrival; a band that sends lines in a burst doesn't create out-of-order minutes; duplicate lines don't double-count.
4. **Two heart rate sources.** Watch + band HR in the same minute use `step_fused` with the right sigmas; one source dropping keeps estimates going on the other; a source silent for over 2 minutes is ignored, not reused.
5. **No heart rate at all.** Pulsoid disconnects: the Kalman runs predict-only, the worker's status says heart rate is missing (never shows a confident green), and it recovers when HR returns.
6. **SOS line.** `BTN:SOS` starts escalation immediately; `BTN:1` acknowledges and never starts escalation.

## 2. Models

7. **Kalman exactness.** Matches the Mendeley `ectemp` column (exists) and `step_fused` with one source equals `step`.
8. **Forecaster fallback.** Missing or corrupt checkpoint: the pipeline runs Kalman-only and every estimate is flagged as Kalman-only.
9. **Forecast sanity on real replays.** For every minute: 10th <= median <= 90th, no NaN, no value outside 34 to 42 °C, and the calibration is applied exactly once.
10. **Personal offset applied exactly once.** After a spot check, NOW and every forecast percentile shift by `offset_c` once (not zero times, not twice), for that worker only; another worker's numbers don't move.
11. **Offset safety.** A reading off by more than 1.5 °C is ignored; many readings never push the offset past 0.6 °C; offsets survive a server restart (SQLite).

## 3. Alerts

12. **Exact thresholds.** Yellow and red fire exactly at the config values (38.49 vs 38.50 °C for red) and clear only below the threshold minus the clear margin (no flicker at 38.45 to 38.55).
13. **Demo threshold.** Changes only through the explicit demo control, and every event carries a flag saying the demo threshold is on.
14. **Red repeats until acknowledged.** `BUZZ:HEAT_RED` is re-sent; a button press acknowledges (stops the repeat buzz) but the red status stays while the temp is high; red with no acknowledgment for `HEAT_RED_ACK_TIMEOUT_S` starts a man-down check.
15. **Router priorities.** With several hazards at once, the band shows MANDOWN > HEAT_RED > LIGHTNING > HEAT_YELLOW > FLOOD_FIRE; clearing the top one falls back to the next active one, not to OFF.
16. **Commands only on change, plus the 20 s re-send.** No command storms; the re-send keeps the band's "connected" state alive.
17. **Man-down.** Tipped + no activity for `MANDOWN_STILL_S` -> `BUZZ:MANDOWN`; movement or a button press cancels; not cancelled within `MANDOWN_ESCALATE_S` -> `mandown_confirmed` once; a flapping tilt switch doesn't spam checks.

## 4. Emergency calls

18. **Blocked numbers in every format.** 911, +1 911, 9-1-1, (911), 112, 999, 988, 933, and short codes are refused even if put in .env; only allowlisted numbers can be called.
19. **Dry run by default.** No phone call is ever attempted unless `EMERGENCY_LIVE_CALLS=1`; tests assert the live dialer is never constructed.
20. **One call per incident.** Repeated `mandown_confirmed` / `sos` events for the same incident don't place extra calls; a cancel within the window stops the call; every step is logged.
21. **Facts only.** The call script contains only fields from the event (name, site, GPS, what happened, vitals, time); "Unknown" for anything missing; no invented values.

## 5. Site hazards

22. **Lightning rule edges.** One flash doesn't stop work (two needed within the window); bad-quality flashes are ignored; 9.9 mi stops, 10.1 mi doesn't; each new flash resets the 30-min all-clear; a stale lightning feed never gives the all-clear.
23. **NWS mapping.** EH and XH both map to extreme heat; thunderstorm, flood and red flag map to the right band patterns.
24. **Offline.** With the network off, every feed serves its last good result marked stale, and nothing crashes.
25. **Hazard index.** Every site gets a 0-10 index or None, never out of range; the ranking matches the index (after C1's current round, xfail until then).

## 6. Planner

26. **Rule checker gate.** Every schedule any endpoint returns passes the rule checker. A planted LLM response that violates a rule is rejected and never reaches the API or the UI.
27. **No-key mode.** With no ANTHROPIC_API_KEY, the schedule and briefing endpoints still work, labeled no-LLM.
28. **What-ifs don't change real data.** Running what-ifs leaves the live scenario untouched.

## 7. Server, privacy and robustness

29. **Privacy on every path.** The crew-lead view, on every WebSocket event and every REST endpoint (including `/api/state`), never contains raw heart rate, core temp or personal offsets. The worker's own view and the demo view do.
30. **Bad requests.** Malformed JSON, missing fields, wrong types and out-of-range spot checks (outside 34 to 42 °C) return 4xx with a clear message, never 500.
31. **CORS.** Only the origins in `FRONTEND_ORIGINS` are allowed.
32. **Replay determinism.** The same replay twice gives the same event sequence; changing speed mid-run doesn't skip or repeat minutes.
33. **Load.** 6 workers × a full 8-hour shift at high replay speed finish in reasonable time with no dropped events and steady memory.
34. **Restart.** Kill and restart mid-shift: offsets, alert history and the current schedule come back.

## 8. Safety invariants (run after the full replay)

35. Every constant in `backend/config.py` is unchanged after a full replay, and no code path writes to it (scan for assignments to `config.<NAME>` outside tests).
36. No learned component (twins, offsets, curriculum, agent playbook) can change a threshold: grep plus a runtime check.
