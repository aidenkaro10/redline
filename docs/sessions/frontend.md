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
- Session D's `emergency_status` → `EmergencyCall` adapter: being built in a side session (its own branch).
- A needs to serve `GET /api/results` (and later `/api/twins`), and mount B's router so `/api/planner/chat`, `/api/schedule`, `/api/briefing` work on the real backend.
- When A's replay runs at 10x, the SOS/man-down timers also run at 10x (A doesn't drop to 1x like the mock). Fine for now; say it out loud in the demo or ask A to slow down on incidents.
