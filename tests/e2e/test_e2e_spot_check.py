"""
5. Spot check -> personal offset -> applied to the next estimate AND forecast -> survives a restart.

A judge takes one ear-thermometer reading and types it in (POST /spot-check). The model's raw
estimate minus the thermometer is the "miss"; the personal offset is learned from it, then
subtracted from the NOW estimate and every forecast percentile (never from a threshold).

We play the same synthetic shift twice, with and without the spot check, so the only
difference between the two runs is the offset. Then we restart the server on the same
SQLite file and check the offset comes back.
"""

import pytest

from backend import config
from backend.replay import shifts
from e2e_support import ENS5_PATH, StepClock

START = 1_792_000_000.0
SPOT_AT_S = 20 * 60 + 30          # 30 s into replay minute 20


def run(live_app, spot_c_below_raw: float | None):
    """Play 20.5 replay minutes, maybe do a spot check, then one more minute. Returns (app, update, est)."""
    extra = ["--checkpoint", str(ENS5_PATH)] if ENS5_PATH else []
    app = live_app(*extra, clock=StepClock(start=START))
    app.start_replay(shifts.synthetic(n_crew=0, fall_worker=None))
    app.step(SPOT_AT_S)
    update = None
    if spot_c_below_raw is not None:
        raw = app.hub.pipelines["demo-1"].estimates[-1]["core_temp_raw"]
        r = app.client.post("/spot-check", json={"worker_id": "demo-1", "thermometer_c": round(raw - spot_c_below_raw, 3)})
        assert r.status_code == 200
        update = r.json()
    app.step(60)                                   # the next minute's estimate
    est = app.hub.pipelines["demo-1"].estimates[-1]
    return app, update, est


@pytest.fixture
def runs(live_app):
    base_app, _, base = run(live_app, None)
    live_app.stop(base_app)
    app, update, est = run(live_app, 0.4)          # thermometer 0.4 °C below the model
    return {"live_app": live_app, "app": app, "update": update, "est": est, "base": base}


def test_A10_spot_check_learns_an_offset(runs):
    u = runs["update"]
    assert u["type"] == "offset_update" and u["worker_id"] == "demo-1"
    assert u["accepted"] is True and u["n_checks"] == 1
    assert u["miss_c"] == pytest.approx(0.4, abs=0.002)
    # offset = sum(misses) / (checks + 2): one check of 0.4 -> 0.133 (frontend contract math)
    assert u["offset_c"] == pytest.approx(0.4 / 3, abs=0.002)
    assert u["confidence"] == pytest.approx(1 / 3, abs=0.002)


def test_A10_offset_is_applied_to_the_next_estimate(runs):
    est, base, off = runs["est"], runs["base"], runs["update"]["offset_c"]
    assert est["offset_c"] == pytest.approx(off, abs=1e-4)
    assert est["core_temp_raw"] == pytest.approx(base["core_temp_raw"], abs=1e-6)     # same data, same model
    assert est["core_temp"] == pytest.approx(base["core_temp"] - off, abs=0.002)
    assert est["kalman"] == pytest.approx(base["kalman"], abs=1e-6)                   # the baseline is untouched


@pytest.mark.skipif(ENS5_PATH is None, reason="needs ml/checkpoints/ens5.pt for a forecast")
def test_A10_offset_is_applied_to_every_forecast_percentile(runs):
    est, base, off = runs["est"], runs["base"], runs["update"]["offset_c"]
    assert est["forecast"] and base["forecast"]
    for q in ("p10", "p50", "p90"):
        for a, b in zip(est["forecast"][q], base["forecast"][q]):
            assert a == pytest.approx(b - off, abs=0.002)


def test_A36_offset_reaches_the_dashboard_but_not_the_thresholds(runs):
    app = runs["app"]
    snap = app.client.get("/api/state?view=demo").json()
    assert snap["offsets"]["demo-1"]["offset_c"] == pytest.approx(runs["update"]["offset_c"], abs=1e-4)
    est = snap["history"]["demo-1"]["estimates"][-1]
    assert est["core"]["mean"] == pytest.approx(runs["est"]["core_temp"], abs=1e-6)
    assert est["thresholds"] == {"warning": config.HEAT_WARNING_C, "danger": config.HEAT_DANGER_C, "demo": False}
    assert (config.HEAT_WARNING_C, config.HEAT_DANGER_C) == (38.0, 38.5)


def test_A11_A34_offset_survives_a_server_restart(runs):
    live_app, app = runs["live_app"], runs["app"]
    off = runs["update"]["offset_c"]
    live_app.stop(app)
    again = live_app(clock=StepClock(start=START + 3600))      # same SQLite file, new process state
    again.start_replay(shifts.synthetic(n_crew=0, fall_worker=None))
    status = again.hub.pipelines["demo-1"].offset_status()
    assert status["n_checks"] == 1 and status["offset_c"] == pytest.approx(off, abs=1e-4)
    again.step(3 * 60)
    est = again.hub.pipelines["demo-1"].estimates[-1]
    assert est["offset_c"] == pytest.approx(off, abs=1e-4)
    assert est["core_temp"] == pytest.approx(est["core_temp_raw"] - off, abs=0.002)
    snap = again.client.get("/api/state?view=worker&worker_id=demo-1").json()
    assert snap["offsets"]["demo-1"]["n_checks"] == 1


@pytest.mark.xfail(strict=True, reason="gap (session A): the frontend contract asks for a fresh estimate right "
                   "after an accepted spot check 'so the chart moves while the judge watches'; A only re-checks "
                   "the heat alert, so the chart waits for the next minute (60 s live).")
def test_fresh_estimate_is_pushed_right_after_a_spot_check(live_app):
    app = live_app(clock=StepClock(start=START))
    app.start_replay(shifts.synthetic(n_crew=0, fall_worker=None))
    app.step(5 * 60 + 30)
    raw = app.hub.pipelines["demo-1"].estimates[-1]["core_temp_raw"]
    with app.client.websocket_connect("/ws/live?view=demo") as ws:
        ws.receive_json()
        app.client.post("/spot-check", json={"worker_id": "demo-1", "thermometer_c": round(raw - 0.4, 2)})
        msgs = app.drain(ws)
    assert any(m["type"] == "estimate" for m in msgs)
