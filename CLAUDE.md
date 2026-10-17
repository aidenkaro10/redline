# PlanIT Control

Heat strain forecasting wearable for SwampHacks XII. **Read PROJECT.md for the full spec.** Build order and status: PLAN.md.

## Who owns what
- Teammates own hardware and firmware (`firmware/`). Don't write firmware unless asked.
- Aiden owns all software, and that's our job: backend, Kalman filter, forecaster, ML training and eval, alerts, replay mode, frontend, stretch features.

## Rules
- Build in the MVP order from PROJECT.md. Don't start a stretch feature until everything above it works.
- Fake data and replay mode first. Every module must run and test without hardware.
- Implement these exactly as written in PROJECT.md: the data contract, the serial line protocol (115200 baud), the Kalman equations and parameters, the alert thresholds.
- All alert thresholds and safety limits live as named constants in `backend/config.py`. Safety limits are hard-coded, never learned. The LLM never makes safety calls.
- Timestamp every reading on the laptop when it arrives.
- Always report ML results next to the Kalman baseline, split train/test by person, never by time.
- Keep demo controls (test alert, replay toggle, demo threshold) one click away on the dashboard.
- Secrets go in `.env`, never in code. `.env.example` lists every key.

## How we work
- Ask Aiden before any big decision: new libraries, changing the spec, changing the structure.
- Write small tests as you go (`pytest`). Commit after each working step.
- After each piece, say how to run it and one test to verify it.
- Keep explanations short and in plain language. Comment code so a beginner can follow it.
- Python, minimal dependencies.
