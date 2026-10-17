# PlanIT Control frontend

Next.js + Tailwind + Recharts + Leaflet. Runs on its own with a built-in **mock backend**, so every screen works with no Python and no hardware.

## Run it

1. `cd frontend`
2. `npm install` (first time only)
3. `npm run dev`
4. Open http://localhost:3000

## Screens

| URL | What | Who |
| --- | --- | --- |
| `/` | Crew lead dashboard: worker cards, risk-ranked map of 60 Florida sites, hazard panel, schedule, alerts | Crew lead (status only, no raw biometrics) |
| `/demo` | Live heart rate, skin temp, core temp with the forecast band and the "why" line | Judges, with the wearer's consent |
| `/worker/<id>` | Phone view: giant status color, current hazard, break countdown | The worker |
| `/briefing` | 6 PM briefing for tomorrow | Crew lead |
| `/planner` | Chat with the planning agent next to today's schedule | Crew lead |
| `/results` | Our model vs the Kalman baseline (docs/results.md: ensemble, red alerts caught), spot-check personalization, planner safety (AI alone vs with rule checker), the 5 result figures | Judges |
| `/report` | Crew photo report form | Workers |

The man-down screen pops up on every page. Demo controls sit at the top of `/` and `/demo`.

## Spot check ("Learning your body")

On `/demo` (right column) or the phone view, type an ear or mouth thermometer reading and press **Add**. The card shows how much hotter or cooler this person runs than average, the confidence bar and how it changed. The core temp chart shifts right away. On mock data, "Mock: fill in a sample reading" fills in a believable number.

## Play the scripted demo

Click **▶ Play scripted demo**. About 3 minutes: Maria heats up (yellow, then red), a thunderstorm moves in, she collapses, and the emergency call plays out ("Demo: would call 911").

## Switch to the real backend

Tested with session A's backend (replay + forecaster v1): dashboard, demo view and SOS all work. Worker and site ids come from the backend, nothing is hardcoded.

1. Start session A's FastAPI server: `python -m backend.app.main --source replay --speed 10` (port 8000).
2. Create `frontend/.env.local` with one line: `NEXT_PUBLIC_BACKEND_URL=http://localhost:8000`
3. Restart `npm run dev`.

The data contract is in `docs/interfaces/frontend-contract.md` (types: `src/lib/contract.ts`).

## Tests

`npm test` runs the mock backend tests (scripted demo end to end, privacy filter, man-down cancel, lightning rule).

## Where things live

- `src/lib/contract.ts`: every data shape the UI expects
- `src/lib/live.tsx`: live connection (WebSocket for the real backend, Server-Sent Events for the mock) and shared state
- `src/mock/engine.ts`: the mock backend (fake physiology, real Kalman equations, alert rules, man-down, emergency call)
- `src/mock/scenario.ts`: the scripted demo timeline
- `src/lib/sites.ts`: 60 real Florida power plants, approximate coordinates (mock until session C1's `plants.geojson`)
- `src/app/`: one folder per screen. `src/components/`: shared pieces

## Notes

- Map tiles come from OpenStreetMap and need internet. Without it, the site dots still show on a plain background.
- If you edit `src/mock/engine.ts`, restart `npm run dev` (the mock keeps running between hot reloads).

## Scripts

| Command | What it does |
| --- | --- |
| `npm run dev` | Mock backend, http://localhost:3000 |
| `npm run dev:real` | Real backend at http://localhost:8000 (or `BACKEND_URL=... npm run dev:real`). Uses its own build folder, so it can run next to `npm run dev`. If both run, this one takes the next free port (3001): add it to the backend's `FRONTEND_ORIGINS`. |
| `npm run check` | Type check + tests + production build. Run before you commit. |
| `npm test` | Just the tests |

## If something looks stuck

- **Page keeps reloading or says "Reconnecting…" forever:** stop the server (Ctrl+C), run `rm -rf .next`, then `npm run dev` again.
- **Edited `src/mock/engine.ts`:** no restart needed. The mock swaps in a fresh engine and open pages reconnect within a few seconds (the demo restarts at 1:00 PM).
- **Live stream goes quiet:** the page notices after 8 seconds and reconnects by itself.

## Cutting features and restyling: what's safe to touch

Logic and looks are split, so you can delete pages or restyle freely.

**Pure looks (change or delete freely):**
- `src/app/*/page.tsx`: one folder per screen. Delete a folder to drop a screen, then remove its link in `LINKS` in `src/components/Nav.tsx`.
- `src/components/*`: the pieces (cards, map, charts, man-down screen, demo controls). Each one only reads data, nothing else depends on it.
- `src/app/globals.css`: all colors live here as tokens (`--ok`, `--danger`, `--surface`...), light and dark. Change them in one place.

**Logic (keep, or change carefully):**
- `src/lib/contract.ts` and `contract-extra.ts`: the data shapes. Session A's backend follows them.
- `src/lib/live.tsx`: the live connection, reconnects, and the privacy filter (crew view never keeps raw biometrics).
- `src/lib/emergency.ts`, `src/lib/offset.ts`: adapters that turn sessions D's and A's event shapes into ours.
- `src/lib/crew.ts`: worker and site lookups (no hardcoded ids).
- `src/mock/*`: the mock backend and the scripted demo. Only used when `NEXT_PUBLIC_BACKEND_URL` is empty.

**Safe screens to cut** (nothing else depends on them): `/planner`, `/briefing`, `/report`, `/results`. The crew dashboard, demo view, worker phone view and the man-down/SOS screen carry the demo.

After cutting, run `npm run check`. It catches anything that still points at a deleted file.
