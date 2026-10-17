# Session E: frontend progress notes

## Round 1 (2026-10-17): everything on mock data

Done:
- Next.js 16 + Tailwind 4 + Recharts 3 + Leaflet 1.9 app in `frontend/` (no other libraries).
- Draft data contract: `docs/interfaces/frontend-contract.md`, types in `frontend/src/lib/contract.ts`.
- Mock backend inside Next.js (`/api/...` + Server-Sent Events at `/api/stream`). Same events the real WebSocket should send.
- Scripted demo, about 3 minutes: yellow, red + ack, lightning (10-mile ring, 30-min countdown), man-down, emergency call with transcript.
- Screens: crew dashboard, demo view, worker phone view, man-down overlay on every page, 6 PM briefing, photo report form, demo controls.
- Privacy enforced server-side by `view` (crew never gets vitals or core temp; tested).
- `npm test`: 8 tests on the mock backend.

Decisions made (small, can be changed):
- Plain Leaflet (no react-leaflet wrapper) to avoid an extra library.
- OpenStreetMap tiles. CARTO's free tiles now need an API key.
- Mock man-down/call timings are shortened for the demo (10 s still, 15 s to confirm, 15 s cancel window).
- Mock site list: 60 real Florida plants typed in by hand, approximate coordinates. Swap for C1's `plants.geojson`.

Next:
- Align with session A's API once `docs/interfaces/` has their version (open questions at the bottom of the contract doc).
- Read C1/C2/D interface docs as they appear and adjust the hazard, index and call shapes.
- Results page (harness report next to Kalman), twin learning panel, agent chat panel (PLAN.md Phase 9 items 3, 4, 8).

## Round 2 (2026-10-17): SOS + one-LED band (decided by Aiden)

- SOS (band `BTN:SOS`, button held 3 s): new `sos` event + `SOS` alert kind; the incident is a `ManDown` with `reason: "sos"` that skips "checking" and escalates right away (matches session D's `sos` input). Full-screen purple "SOS from <worker>" on every page.
- Phone view: hold-3-s SOS button (same as the band). Demo controls: "Trigger SOS". REST: `POST /api/workers/{id}/sos`, `POST /api/demo/sos`.
- Scripted demo: Jake holds SOS at replay min 14; the crew lead cancels it as a false alarm 5 s into the cancel window. Maria's man-down call still completes. About 2 min 55 s total.
- Band light in the demo view now shows the single LED from docs/firmware-contract.md (green, yellow, flashing red, white flashes, blue, red breathing, purple blink for SOS, slow cyan for no link).
- Mock cancel window is now 10 s (= session D's `EMERGENCY_DEMO_CANCEL_WINDOW_S`).

Next: fold session D's `emergency_status` events into the UI's `EmergencyCall` (see "Known gap" in the contract doc).

## Round 3 (2026-10-17): works against the real backend, results, planner

- Ran the frontend against session A's branch (replay + forecaster v1+conformal, port 8010 in a throwaway checkout): dashboard, demo view, SOS all work. A serves this contract as written (answers to the open questions in A's interface doc §4).
- Removed every hardcoded worker/site id (mock uses worker-1/fl-deerhaven, A uses demo-1/site-gnv, C1 uses eia-663). New `src/lib/crew.ts`: shared site list, crew helpers.
- Friendly empty states when the planner hasn't posted a schedule or briefing (A returns 404 until B posts).
- C1's requests: stale hazards greyed, fake ones get a TEST badge, 30-mile lightning watch ring, data credits under the map.
- New pages: `/results` (real numbers from docs/results.md vs Kalman; twins panel shows MOCK placeholder until Phase 6) and `/planner` (chat with B's `POST /api/planner/chat` + schedule).
- No horizontal scroll at phone width on any page.

Still open:
- Session D's `emergency_status` adapter: done (round 4).
- A needs to serve `GET /api/results` (and later `/api/twins`), and mount B's router so `/api/planner/chat`, `/api/schedule`, `/api/briefing` work on the real backend.
- When A's replay runs at 10x, the SOS/man-down timers also run at 10x (A doesn't drop to 1x like the mock). Fine for now; say it out loud in the demo or ask A to slow down on incidents.

## Round 4 (2026-10-17): session D's `emergency_status`

- The UI now accepts D's flat `emergency_status` events straight from session A and folds them into the `EmergencyCall` view (`frontend/src/lib/emergency.ts`). New stage `failed`: huge "Emergency call FAILED. Call 911 yourself now." box on the man-down screen (can't be minimized), and "Automatic call failed. Call 911 now." on the SOS phone screen.
- "Cancel, false alarm" now calls `POST /api/emergency/cancel {worker_id}` (D's suggested route). The mock supports both cancel routes.
- Mock: `MOCK_EMERGENCY_EVENTS=status` in `frontend/.env.local` (or `POST /api/demo/emergency {"format": "status"}`) sends D-style events. `{"fail_next": true}` makes the next call fail. 7 new tests.

## Round 5 (2026-10-17): spot checks, nullable why, results for judges, C2 index

- Spot checks: `POST /api/spot-check {worker_id, thermometer_c}` + `offset_update` event (math from backend/models/personal_offset.py). "Learning your body" card on the demo view and phone view: offset, confidence bar, number of checks, step chart of how it changed. The estimate shifts right away. Mock has hidden per-worker offsets and a "fill in a sample reading" helper.
- `Estimate.why` can be null.
- /results: ens5 headline (+20 min 0.230 vs 0.456 °C, red caught 88% vs 75%), personalization table, planner safety table (AI alone 92% of runs break a rule, 0% with the rule checker), session H's 5 figures (copied from docs/figures by a predev/prebuild script; shows a placeholder until merged). Removed the heart-rate twins panel (twins were a negative result).
- C2 hazard index: SiteStatus.hazard_index carries the full predict_index() result; hazard panel shows per-hazard bars vs a usual day + reasons; map tooltip shows band + top reason. Mock data until wired.
- Fixed: dev server reload loop after the merge (stale .next cache; `rm -rf frontend/.next` fixes it).

## Round 6 (2026-10-17): technical hardening before the UI pass

- Tested against session A's latest branch (18497d9, ens5 forecaster): every page works or shows a clear "not posted yet" state. Spot check works end to end with A's flat `offset_update` (new adapter `src/lib/offset.ts` + tests).
- Found and reported to A: `offset_update` reached `view=crew` on `/ws/live`. Fixed by A at 8ca5784 (with a test). Frontend now drops anything a view may not see (canSee) and strips raw history from crew snapshots, so the crew page never holds it.
- Mock engine is replaced automatically after code edits (no more stale engine); old streams are closed and pages reconnect.
- Live connection: 8 s silence watchdog reconnects; a malformed message is skipped instead of breaking the page.
- `npm run dev:real` (separate build folder, runs next to the mock) and `npm run check` (types + tests + build).
- Production build (`next build` + `next start`) checked in mock mode: all pages, stream, spot check, scripted demo.
- README: troubleshooting + which files are looks vs logic for cutting features.

## Round 7 (2026-10-17): cut down to phone-first (Aiden's call)

- Two phone apps: worker app (`/worker/<id>`, simple main screen + Details) and crew lead app (`/`, Crew and Sites tabs). Laptop page for judges stays at `/demo` (+ `/demo/results`), to be trimmed last.
- Removed: planner chat page, 6 PM briefing page (Sites tab's 0-10 index covers the brief), report page (moved into worker Details), schedule grid, crew-side alert log, laptop dashboard layout. Mock planner chat removed.
- Crew banners only for real alerts (stop work, flood/fire warnings), not everyday heat. Site panel shows active hazards first, "Show all" for the rest.
- No data or safety logic changed; still works with A's backend.

## Round 8 (2026-10-17): merged main, tested against the real merged backend

Merged main (all sessions except F) into this branch, ran the UI against `python -m backend.app.main --source replay --speed 10` from the main checkout (port 8000).

Works on the real backend: crew tab (statuses, lightning stop-work banner + countdown), sites tab (79 real plants, live lightning rings), worker app (status, hazard tile, SOS, details chart + session H why-line, spot check), SOS and man-down screens with session D's call, crew lead "close alert", judges' /demo.

Fixed in the frontend:
- Session C1's SiteStatus sends lightning/heat numbers flat on the hazard (`all_clear_at`, `strikes`, `wbgt_c`...) with null `detail`/`source`/`updated_at`. `src/lib/normalize.ts` turns them into the contract's nested shape (tested). The lightning countdown and 10-mile ring work again.
- `index_tomorrow` can be null: chips show "--", sorting and map colors handle it.
- Two clocks during replay: lightning all-clear (live feeds) and session D's call deadlines are real-clock, worker data is replay-clock. Countdowns now use whichever clock the time belongs to (`secondsUntil`).

Backend gaps (not frontend code, for the main session / A):
1. Session B's planner router isn't mounted and nothing posts to `/api/plans` or `/api/briefing`: `/api/schedule` and `/api/briefing` are 404, so "Next break" shows "--" (worker app + crew rows).
2. Session C2's index isn't wired into C1's picture (`backend/hazards/picture.py` sets `hazard_index: None`): every site's 0-10 index is "--", so "Riskiest tomorrow" isn't ranked.
3. `/api/state` snapshot has no `offsets`, so the "Learning your body" card resets to "Not personalized yet" on page reload even though A saved the offset (SQLite). Add `offsets: {worker_id: offset_update}` for demo/worker views.
4. Old man-down incidents stay open until a crew lead closes them (replay falls stack up); only one shows at a time. Fine for now.

## Round 9 (2026-10-17): backend edits reverted

Commit 84b3310 edited backend files owned by sessions A and C1. Reverted in 1fb24f0 at the main session's request (A merged its own offsets fix, C1 is wiring the index). This session stays inside `frontend/` and its own notes.

## Round 10 (2026-10-17): merged main (b680a29), aligned with A's offsets and break times

Tested against the merged backend (replay, own port):
- Break times (A's planner bridge): worker app shows "Next break in 45 min", crew rows show "On break". The break tile still hides when a worker has no schedule.
- Saved offsets: A's snapshot sends `offsets` in the flat offset_update shape; `src/lib/offset.ts` converts it. Learning card survives a page reload (checked: "+0.04 °C hotter than average", 1 check). Crew snapshot has none.
- No frontend code changes were needed for these; the adapters from round 8 already covered A's shapes.

Remaining gaps for the owners:
1. **C1 (index):** until C1 lands the C2 wiring, A's `site_status` sends `index_tomorrow: 0` (not null) and no `hazard_index`, so every site shows "0.0" and the "Riskiest tomorrow" list isn't really ranked. Ask: send `null` until the index exists (the UI then shows "--"), and pass C2's full result as `hazard_index` (the hazard panel shows the per-hazard breakdown and reasons from it).
2. **A (snapshot offsets):** the snapshot's offset has no history or last reading, so after a reload the learning card's little chart restarts at the current value. Optional: include the per-check history.
