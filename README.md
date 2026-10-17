# PlanIT Control

A wristband that forecasts heat strain in outdoor workers and buzzes them before they overheat. Built for SwampHacks XII (Sperry Tech PlanIT Control track).

It estimates core body temperature live from heart rate (Kalman filter), forecasts it 10 to 30 minutes ahead (neural network), and alerts the worker and their crew lead.

Full spec: [PROJECT.md](PROJECT.md)

## Layout

```text
firmware/         band firmware (hardware team)
backend/app/      FastAPI server + data contract
backend/ingest/   serial, WiFi and Pulsoid readers
backend/models/   Kalman filter, forecaster
backend/hazards/  NWS feed, WBGT
backend/alerts/   alert router, man-down
backend/replay/   replay mode (recorded or fake data, with speed-up)
backend/storage/  SQLite
backend/tests/    tests
ml/               simulation, training, evaluation
frontend/         dashboard
data/recordings/  recorded sessions for replay
```

## Setup

1. `python3 -m venv .venv`
2. `source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env`, then fill in your Pulsoid token.

## Run tests

```bash
pytest
```
