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
