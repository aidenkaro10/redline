# Frontend acceptance tests

Written by the main session for session E to turn into automated tests (plus a short manual browser pass at the end). Each test says what to feed the UI and what must happen. Feed real backend payloads wherever possible: session J records them in `tests/e2e/fixtures/` (merge main to get them).

Rule for every test: if the UI shows something different from what the spec (PROJECT.md) or the backend contract says, the test fails. Don't loosen a test to make it pass; write the gap in `docs/sessions/frontend.md`.

## Contract

1. **Every recorded fixture parses.** Each JSON file in `tests/e2e/fixtures/` goes through the frontend's types and `src/lib/normalize.ts` without throwing, and no field the UI reads comes out `undefined` when the fixture has it.
2. **Unknown event types are ignored.** An event with an unknown `type` doesn't crash anything and doesn't change what's on screen.
3. **Missing optional fields.** `why: null`, `hazard_index: null`, `break_at: null`, `minutes_to_danger: null` each render as "--" or are hidden. No crash, no "NaN", no "undefined".

## Crew lead app

4. **Status colors.** Worker status green / yellow / red maps to the right card color and label, from the event, not computed in the UI.
5. **Privacy (most important).** Feed a crew-view payload that wrongly includes heart rate, core temp and a personal offset: none of those numbers appear anywhere on the crew screens (check the rendered text). Status, minutes to danger and hazards still show.
6. **Minutes to danger.** Shows the number from the event; `0` shows as "now" (red); `null` shows "--".
7. **Lightning stop-work.** A lightning event for a site shows the stop-work banner, the 10-mile ring on the map, and a 30-minute countdown from the event's all-clear time measured on the wall clock. A second strike event resets the countdown to 30:00.
8. **Hazard index ranking.** Sites are ordered by tomorrow's 0-10 index, highest first; sites with no index go last and show "--".
9. **Man-down.** A `mandown_confirmed` event shows the full-screen alert on every page (crew tab, sites tab, details), with the worker's name, site, time since the fall and last status.
10. **SOS.** An `sos` event shows the same full-screen alert reading "SOS from <worker>".
11. **Emergency call sequence.** Feed `emergency_status` events in order: notifying crew lead, cancel window (countdown), "Demo: would call 911", connected, completed. Each step shows in order. A `failed` status shows the large "Call 911 yourself now" box.
12. **Cancel false alarm.** Pressing cancel during the window sends the right request once (not twice on double-click) and the screen moves to "cancelled".

## Worker app

13. **Main screen.** Giant status color, current hazard tile, next break countdown from `break_at` (planner schedule).
14. **Hold-to-SOS.** A 3-second hold sends SOS. A short tap does nothing (or only shows "hold for 3 seconds"). Releasing at 2.5 s sends nothing.
15. **Spot check input.** A valid reading (e.g. 37.4) posts `{worker_id, thermometer_c}` once. Rejected before sending: empty, non-numbers, and values outside 34.0 to 42.0 °C, with a plain-language message.
16. **Learning card.** An `offset_update` event updates the "Learning your body" card: offset with sign ("+0.2 °C hotter than average"), confidence bar, number of checks. After a page reload, the card comes back from the `/api/state` snapshot.
17. **Why line.** Shown under the chart when present, hidden when `null`.
18. **Forecast chart.** The median line, the shaded 10-90 band, and the 38.5 °C danger line. With the demo threshold on, the danger line is at 37.5 °C and clearly labeled "Demo threshold".

## Judges' demo page

19. **Results numbers.** Every number on the results page comes from one data file, and matches `docs/results.md` on main (today: +20 min error 0.230 vs 0.456 °C; red alerts caught 88% vs 75%; planner 0 violations vs 92% for AI alone). When the final model's test numbers land, only that file changes.
20. **Demo controls.** Test hazard, replay on/off and replay speed each send the right request and the UI reflects the new state.

## Robustness

21. **Connection drop.** When the WebSocket closes, the UI shows "reconnecting", retries, and after reconnecting rebuilds its state from `/api/state` (no duplicate workers, no stale alerts).
22. **Replay speed.** At 10x replay, worker data uses replay time while lightning and emergency countdowns use the wall clock (the two-clock fix stays fixed).
23. **Phone layout.** At 375 px wide: no horizontal scrolling, nothing cut off, tap targets at least 44 px. Same in dark mode.

## Manual pass (in the browser, against the real backend replay)

24. Run `python -m backend.app.main --source replay --speed 10` and the app. Watch the scripted demo end to end: Jake yellow, then red, a button press, Maria's fall, man-down, the emergency sequence in dry run. Then enter a spot check. Note anything that looks wrong or confusing in `docs/sessions/frontend.md`.
