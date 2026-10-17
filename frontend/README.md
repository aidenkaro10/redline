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
| `/results` | Our model next to the Kalman baseline (real numbers from docs/results.md), personal twins panel | Judges |
| `/report` | Crew photo report form | Workers |

The man-down screen pops up on every page. Demo controls sit at the top of `/` and `/demo`.

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
