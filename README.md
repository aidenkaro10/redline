# Redline

**A wristband that warns outdoor workers before they overheat, plans their day around the heat, and calls for help when someone goes down.** Built for SwampHacks XII (Sperry Tech PlanIT Control track).

- Estimates each worker's core body temperature every minute from heart rate (Kalman filter, US Army ECTemp).
- Forecasts it 30 minutes ahead with calibrated ranges (GRU neural network, 5-model ensemble, conformal prediction).
- Builds the crew's schedule around heat and weather (Google OR-Tools solver + Claude, every plan checked by a code rule checker).
- Learns from past days (self-improving agent: ACE playbook, GEPA strategy evolution, self-play curriculum).
- Runs an offline AI planner with no internet (Qwen3-1.7B fine-tuned with LoRA on Claude's examples).
- The band is wireless (Bluetooth LE) and the emergency call is an AI voice agent (ElevenLabs + Twilio) that never calls 911 in the demo.

**Rule behind everything:** AI predicts and suggests. Plain code makes every safety decision.

## Read more

| | |
| --- | --- |
| **Full write-up** (how we built it, with all the numbers) | [docs/writeup.md](docs/writeup.md) |
| **Technical write-up** (equations, architecture, results tables) | [docs/technical-overview.md](docs/technical-overview.md) |
| **Every technique we used, by its standard name** | [docs/techniques.md](docs/techniques.md) |
| **Model results** (scored once on 10 locked test people) | [docs/results.md](docs/results.md) |
| **Key decisions** | [docs/decisions.md](docs/decisions.md) |
| **Band firmware + upload steps** | [firmware/band/](firmware/band/) |
| Original spec | [PROJECT.md](PROJECT.md) |

## Headline results

| | |
| --- | --- |
| 20-min core temp forecast error, 10 unseen people | **0.370 °C** vs 0.481 for the published Army method (23% better) |
| Heat warnings caught early | **88%**, 16.9 min ahead |
| AI-made schedules breaking a safety rule (simulated days) | **0%** with our rule checker vs 92% for Claude alone |
| Offline AI explaining plans | **0** made-up facts in 86 claims (Claude: 0 in 108) |
| Tomorrow's hazard prediction (held-out 2023-2024) | 2-4× better than normal rates |
| Automated tests | 729, all passing |

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # then fill in keys; the demo runs without any
.venv/bin/python -m backend.app.main --source replay --speed 10     # demo shift, no hardware
cd frontend && npm install && npm run dev -- -p 3001                # dashboards at http://localhost:3001
```

With the real band (Bluetooth): `.venv/bin/python -m backend.app.main --source live --band ble --no-ble --record`

Tests: `PLANIT_BLE=off .venv/bin/python -m pytest backend ml tests/e2e` and `cd frontend && npm test`

## Layout

```text
firmware/band/    band program (ESP32-S3 Feather, Arduino C++)
backend/app/      FastAPI server, WebSockets, data contract
backend/ingest/   band over Bluetooth / USB / WiFi, heart rate, pulse processing
backend/models/   Kalman filter, forecaster, personal offset
backend/alerts/   alert router, man-down
backend/hazards/  weather, lightning, floods, fires, hazard index
backend/planner/  CP-SAT optimizer, rule checker, Claude agent, 6 PM briefing
backend/learning/ self-improving loops (curriculum, ACE, GEPA, nightly loop)
backend/emergency/ escalation + voice agent (911 blocked in code)
ml/               simulator, training, calibration, evaluation, offline LLM
frontend/         Next.js crew, worker and judges' dashboards
docs/             write-ups, results, decisions, session notes
```

MIT license. The ECTemp method is patented by the US Army; fine for a hackathon, would need a license for a product.
