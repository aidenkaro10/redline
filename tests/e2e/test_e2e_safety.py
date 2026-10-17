"""
7a. Safety invariants: alert thresholds and safety limits are hard-coded and never change at runtime.

  - No code outside the tests ever assigns to backend.config.
  - The safety numbers are the ones in PROJECT.md (alerts table, lightning rule, Kalman, 911 guard ...).
  - A busy session (heat alerts, man-down, SOS, hazards, demo-threshold toggles, many spot checks,
    requests with sneaky extra fields) leaves every config constant exactly as it was.
  - Personal offsets stay capped, so a run of "cool" thermometer readings can never push a hot
    worker's estimate down past the guardrail.
"""

import math
import re
from pathlib import Path

import pytest

from backend import config
from backend.replay import shifts

REPO = Path(__file__).resolve().parents[2]


def config_values() -> dict:
    """Every UPPERCASE constant in backend/config.py (secrets left out)."""
    secret = re.compile(r"KEY|TOKEN|NUMBER|AGENT_ID|PHONE_NUMBER_ID")
    return {k: v for k, v in vars(config).items() if k.isupper() and not secret.search(k)}


def test_no_code_assigns_to_config_at_runtime():
    """Static check: nothing outside tests writes config.X = ... or setattr(config, ...)."""
    pattern = re.compile(r"\bconfig\.[A-Z_]+\s*=(?!=)|setattr\(\s*(backend\.)?config\b")
    offenders = []
    for folder in ("backend", "ml"):
        for path in (REPO / folder).rglob("*.py"):
            rel = path.relative_to(REPO)
            if "tests" in rel.parts or path.name.startswith("test_"):
                continue
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if pattern.search(line.split("#")[0]):
                    offenders.append(f"{rel}:{n}: {line.strip()}")
    assert offenders == []


def test_safety_numbers_match_the_spec():
    """PROJECT.md's alert table, lightning rule, man-down timing, Kalman parameters and 911 guard."""
    assert (config.HEAT_WARNING_C, config.HEAT_DANGER_C) == (38.0, 38.5)
    assert (config.HEAT_WARNING_HORIZON_MIN, config.HEAT_DANGER_HORIZON_MIN, config.HEAT_ALERT_QUANTILE) == (20, 10, 0.9)
    assert config.DEMO_HEAT_WARNING_C < config.HEAT_WARNING_C and config.DEMO_HEAT_DANGER_C < config.HEAT_DANGER_C
    assert 30 <= config.MANDOWN_STILL_S <= 60 and config.MANDOWN_ESCALATE_S <= 60
    assert config.HEAT_RED_ACK_TIMEOUT_S <= 60 and config.HEAT_RED_REPEAT_S == 10
    assert (config.LIGHTNING_STOP_RADIUS_MI, config.LIGHTNING_ALL_CLEAR_MIN, config.LIGHTNING_MIN_FLASHES) == (10, 30, 2)
    assert (config.KALMAN_GAMMA, config.KALMAN_SIGMA, config.KALMAN_B2, config.KALMAN_B1, config.KALMAN_B0,
            config.KALMAN_CT_START) == (0.022, 18.88, -4.5714, 384.4286, -7887.1, 37.1)
    assert config.SERIAL_BAUD == 115200 and config.BAND_RESEND_S == 20
    assert "911" in config.EMERGENCY_BLOCKED_NUMBERS and config.EMERGENCY_MIN_DIGITS >= 10
    assert config.PLAN_MAX_P90_C == config.HEAT_WARNING_C
    assert config.PLAN_LIGHTNING_RADIUS_MI == config.LIGHTNING_STOP_RADIUS_MI
    assert config.PLAN_LIGHTNING_ALLCLEAR_MIN == config.LIGHTNING_ALL_CLEAR_MIN
    assert set(config.BUZZ_PATTERNS) == {"HEAT_YELLOW", "HEAT_RED", "LIGHTNING", "FLOOD_FIRE", "MANDOWN", "OFF"}


def test_busy_session_never_changes_a_threshold(live_app):
    before = config_values()
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=2))
    app.step(30 * 60)

    # Demo thresholds: switch on (with sneaky extra fields), check, switch off.
    r = app.client.post("/api/demo/threshold", json={"on": True, "warning_c": 50, "danger_c": 60,
                                                     "HEAT_DANGER_C": 99})
    assert r.json()["thresholds"] == {"warning": config.DEMO_HEAT_WARNING_C, "danger": config.DEMO_HEAT_DANGER_C}
    app.step(60)
    app.client.post("/api/demo/threshold", json={"on": False})

    # Lots of "cool" thermometer readings for Jake: the offset is learned but capped.
    for _ in range(12):
        raw = app.hub.pipelines["demo-1"].estimates[-1]["core_temp_raw"]
        app.client.post("/spot-check", json={"worker_id": "demo-1", "thermometer_c": round(raw - 1.4, 2)})
        app.step(60)
    # A wild reading is refused outright.
    raw = app.hub.pipelines["demo-1"].estimates[-1]["core_temp_raw"]
    wild = app.client.post("/spot-check", json={"worker_id": "demo-1", "thermometer_c": round(raw - 3.0, 2)}).json()
    assert wild["accepted"] is False

    # Hazards, a fall, an SOS, planner-style context, the rest of the shift.
    app.client.post("/api/demo/hazard", json={"kind": "lightning", "on": True})
    app.client.post("/api/workers/demo-1/context", json={"workload": 400, "future_workload": [400] * 30})
    app.client.post("/api/demo/sos", json={"worker_id": "demo-3"})
    app.step(45 * 60)
    app.client.post("/api/demo/clear-hazards")

    assert config_values() == before, "a config constant changed during the session"
    est = app.hub.pipelines["demo-1"].estimates[-1]
    assert est["offset_c"] == pytest.approx(0.6)                    # 12 misses of ~1.4 °C, capped at 0.6
    assert est["core_temp"] == pytest.approx(est["core_temp_raw"] - est["offset_c"], abs=0.002)
    for wid, p in app.hub.pipelines.items():
        assert (p.heat.warning_c, p.heat.danger_c) == (config.HEAT_WARNING_C, config.HEAT_DANGER_C), wid
    state = app.client.get("/api/state?view=demo").json()
    assert state["demo"]["thresholds"] == {"warning": 38.0, "danger": 38.5}
    last = state["history"]["demo-1"]["estimates"][-1]
    assert last["thresholds"] == {"warning": 38.0, "danger": 38.5, "demo": False}
    # Jake (hot, red) is still red: the capped offset can't talk the alert away.
    assert app.hub.pipelines["demo-1"].heat.level == "red"
    assert not math.isnan(est["core_temp"])
