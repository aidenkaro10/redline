# Real backend payloads (for the frontend's tests)

Recorded from the real merged backend by `tests/e2e/test_e2e_contract.py` (and `test_e2e_planner.py`
for the schedule): replay of sim shift 9647 with the ens5 forecaster, C1 fake hazards, D dry run.
Times are replay time. Phone numbers are fake (555-01xx).

- `live/<type>.json`: a list of real `/ws/live` messages of that type (up to 8 different cases each).
  `live/emergency_status.json` holds session D's raw step events (A sends them folded into `emergency`).
- `live/snapshot_<view>.json`: the first message for `view=crew`, `worker` (demo-1), `demo`. Long lists trimmed.
- `rest/<route>.json`: `{request, status, body}` for each REST call the UI makes.

Rewrite them: `E2E_WRITE_FIXTURES=1 pytest tests/e2e/test_e2e_contract.py tests/e2e/test_e2e_planner.py`.
Details and known mismatches: `docs/sessions/e2e-tests.md`.
