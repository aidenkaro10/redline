"""
1. Full-chain acceptance test: the scripted replay demo, start to finish, in the real app.

What happens in the demo (docs/sessions/live-system.md), in replay minutes:
    Jake (demo-1) heats up: yellow, then red, then he presses his band button 20 s later.
    Maria (demo-2) falls at minute 40: still for 45 s -> BUZZ:MANDOWN -> 60 s -> mandown_confirmed
    -> session D's escalation in dry run: crew lead, safety officer, cancel window, "Demo: would call 911".

We run it two ways:
  - the real demo: sim shift 9647 + the ens5 forecaster (skipped if those gitignored files are missing)
  - a built-in synthetic shift with the Kalman filter only (always runs, no data files needed)
and check the timeline, the band commands, the emergency chain, and what the dashboard's
WebSocket (/ws/live) actually received.
"""

import pytest

from backend.emergency.guard import is_emergency_number
from backend.replay import shifts
from e2e_support import ENS5_PATH, SIM_PATH, TEST_NUMBERS

FALL_AT_S = 40 * 60          # shifts.from_sim / synthetic: crewmate falls 40 replay minutes in

# The escalation, step by step: (stage, status, target). Session D's documented sequence.
EXPECTED_ESCALATION = [
    ("notify", "calling", None),
    ("notify", "calling", "crew_lead"),
    ("notify", "completed", "crew_lead"),
    ("notify", "calling", "safety_officer"),
    ("notify", "completed", "safety_officer"),
    ("cancel_window", "waiting", None),
    ("emergency_call", "calling", "emergency"),
    ("emergency_call", "completed", "emergency"),
    ("done", "completed", None),
]


def run_demo(app, minutes: float):
    """Open a crew and a demo dashboard, play `minutes` of replay, return what each one received."""
    with app.client.websocket_connect("/ws/live?view=crew") as crew, \
            app.client.websocket_connect("/ws/live?view=demo") as demo:
        first_crew, first_demo = crew.receive_json(), demo.receive_json()
        assert first_crew["type"] == "snapshot" and first_demo["type"] == "snapshot"
        crew_msgs, demo_msgs = [], []
        done = 0.0
        while done < minutes * 60:          # drain every 20 min so no queue ever fills up
            done += app.step(min(20 * 60, minutes * 60 - done))
            crew_msgs += app.drain(crew)
            demo_msgs += app.drain(demo)
    return crew_msgs, demo_msgs


def at(app, ev) -> float:
    """Replay seconds since the start for one event."""
    return ev["timestamp"] - app.replay.start


def check_chain(app, crew_msgs, demo_msgs):
    """Everything both demo variants must show, whatever the exact timings are."""
    jake, maria = "demo-1", "demo-2"

    # --- estimates stream: every worker, every minute ---------------------------------
    for wid in app.replay.workers:
        ests = app.of_type("estimate", wid)
        assert len(ests) >= 40, f"{wid} should get one estimate per replay minute"
        assert all(e["core_temp"] is not None and e["kalman"] is not None for e in ests)

    # --- Jake: yellow, then red, then his button press acknowledges it ------------------
    levels = [e["level"] for e in app.of_type("heat_alert", jake)]
    assert levels[:2] == ["yellow", "red"], f"Jake's heat alerts in order: {levels}"
    jake_band = app.band.lines_for(jake)
    assert jake_band.index("BUZZ:HEAT_YELLOW") < jake_band.index("BUZZ:HEAT_RED")
    red = app.of_type("heat_alert", jake)[1]
    ack = app.of_type("heat_ack", jake)
    assert ack, "Jake's replayed button press should acknowledge the red alert"
    assert at(app, ack[0]) - at(app, red) == pytest.approx(20.0, abs=1.5)

    # --- Maria: the fall -> BUZZ:MANDOWN -> mandown_confirmed -----------------------------
    check = app.of_type("mandown_check", maria)
    assert check and check[0]["reason"] == "fall"
    assert at(app, check[0]) - FALL_AT_S == pytest.approx(45.0, abs=2.0)      # MANDOWN_STILL_S
    assert "BUZZ:MANDOWN" in app.band.lines_for(maria)
    confirmed = app.of_type("mandown_confirmed", maria)
    assert len(confirmed) == 1
    c = confirmed[0]
    assert at(app, c) - at(app, check[0]) == pytest.approx(60.0, abs=2.0)    # MANDOWN_ESCALATE_S
    assert c["trigger"] == "fall" and c["worker_name"] == "Maria"
    assert c["lat"] is not None and c["lon"] is not None
    assert c["core_temp_c"] is not None and c["hr_bpm"] is not None
    assert set(c["crewmates"]) == {"demo-1", "demo-3", "demo-4"}

    # --- the emergency chain, in dry run, in order ----------------------------------------
    steps = app.of_type("emergency_status", maria)
    assert [(s["stage"], s["status"], s["target"]) for s in steps] == EXPECTED_ESCALATION
    assert all(s["mode"] == "dry_run" for s in steps)
    emergency_leg = [s for s in steps if s["target"] == "emergency"]
    assert all(s["demo_label"] == "Demo: would call 911" for s in emergency_leg)
    deadline = steps[5]["cancel_deadline"]
    assert deadline == pytest.approx(steps[5]["timestamp"] + 30.0)            # EMERGENCY_CANCEL_WINDOW_S
    assert emergency_leg[0]["timestamp"] >= deadline                          # no call before the window ends
    # Only our three fake numbers were ever asked for, and never a blocked one.
    assert app.phone.asked == list(TEST_NUMBERS.values())
    assert not any(is_emergency_number(n) for n in app.phone.asked + app.phone.dialed)

    # --- it all reached the crew lead's dashboard -----------------------------------------
    crew_types = {m["type"] for m in crew_msgs}
    assert {"worker_status", "alert", "mandown", "emergency", "clock"} <= crew_types
    stages = [m["mandown"]["stage"] for m in crew_msgs
              if m["type"] == "mandown" and m["mandown"] and m["mandown"]["worker_id"] == maria]
    assert stages[:2] == ["checking", "confirmed"]
    md = next(m["mandown"] for m in crew_msgs if m["type"] == "mandown" and m["mandown"]
              and m["mandown"]["stage"] == "confirmed")
    assert md["reason"] == "fall" and md["last_vitals"]["core_temp"] is not None   # responders get vitals
    kinds = [m["alert"]["kind"] for m in crew_msgs if m["type"] == "alert"]
    assert {"HEAT_YELLOW", "HEAT_RED", "MANDOWN"} <= set(kinds)
    call = [m["emergency"] for m in crew_msgs if m["type"] == "emergency"][-1]
    assert call["stage"] == "done" and call["demo"] is True
    assert call["target_label"] == "Demo: would call 911"
    jake_status = [m["worker"]["status"] for m in crew_msgs
                   if m["type"] == "worker_status" and m["worker"]["worker_id"] == jake]
    assert "yellow" in jake_status and "red" in jake_status
    assert not crew_types & {"vitals", "estimate", "band_command"}          # privacy: status only

    # --- the demo view got the raw stream too ---------------------------------------------
    demo_types = {m["type"] for m in demo_msgs}
    assert {"vitals", "estimate", "band_command", "mandown", "emergency"} <= demo_types
    est_workers = {m["estimate"]["worker_id"] for m in demo_msgs if m["type"] == "estimate"}
    assert est_workers == set(app.replay.workers)
    assert {"worker_id": maria, "command": "BUZZ:MANDOWN"}.items() <= next(
        m["band"] for m in demo_msgs if m["type"] == "band_command" and m["band"]["command"] == "BUZZ:MANDOWN").items()


@pytest.mark.skipif(SIM_PATH is None or ENS5_PATH is None,
                    reason="needs data/sim/sim.npz and ml/checkpoints/ens5.pt (gitignored, main checkout)")
def test_A09_A17_A20_real_demo_replay_sim_and_ens5(live_app, monkeypatch):
    """The demo exactly as judges see it: sim shift 9647, ens5 forecaster, 3 crewmates."""
    import backend.app.pipeline as pipeline_module
    calls = []                                   # every forecaster output, in order (A09: applied once)
    real_predict_at = pipeline_module.predict_at
    monkeypatch.setattr(pipeline_module, "predict_at",
                        lambda *a: calls.append(real_predict_at(*a)) or calls[-1])
    app = live_app("--checkpoint", str(ENS5_PATH))
    assert app.hub.predictor is not None
    app.start_replay(shifts.from_sim(path=SIM_PATH))
    crew_msgs, demo_msgs = run_demo(app, minutes=43)

    check_chain(app, crew_msgs, demo_msgs)
    jake = app.of_type("heat_alert", "demo-1")
    # Timings from docs/sessions/live-system.md (replay minutes): yellow ~9, red ~38, before the fall.
    assert 5 * 60 <= at(app, jake[0]) <= 15 * 60
    assert 30 * 60 <= at(app, jake[1]) < FALL_AT_S
    assert jake[0]["rule"] == "forecast" and jake[1]["rule"] == "forecast"
    # The forecaster's whole 30-minute curve reaches the dashboard.
    est = next(m["estimate"] for m in demo_msgs if m["type"] == "estimate")
    assert len(est["forecast"]) == 30 and est["model"]
    # A09: every minute of every worker (pre-roll included) is sane, and the pipeline uses the model's
    # calibrated output exactly as returned: no second calibration, no lost offset.
    ests = [e for e in app.events if e["type"] == "estimate"]
    assert len(ests) == len(calls) > 600
    for e, (kal, now_delta, now_var, fc) in zip(ests, calls):
        f = e["forecast"]
        assert e["core_temp"] == pytest.approx(kal + now_delta - e["offset_c"], abs=1e-3)
        for q, col in (("p10", 0), ("p50", 1), ("p90", 2)):
            assert f[q] == pytest.approx([kal + v - e["offset_c"] for v in fc[:, col]], abs=1e-3)
        assert all(a <= b <= c for a, b, c in zip(f["p10"], f["p50"], f["p90"])), e["minute"]
        values = f["p10"] + f["p50"] + f["p90"] + [e["core_temp"]]
        assert all(v is not None and 34.0 <= v <= 42.0 for v in values), e["minute"]
    # Crewmates who aren't hot stay green the whole time.
    for calm in ("demo-3", "demo-4"):
        assert app.of_type("heat_alert", calm) == []


def test_A08_A17_A20_synthetic_demo_replay_kalman_only(live_app):
    """Same chain on the built-in fake shift with no data files and no forecaster."""
    app = live_app()
    assert app.hub.predictor is None
    plan = shifts.synthetic(n_crew=3)
    app.start_replay(plan)
    crew_msgs, demo_msgs = run_demo(app, minutes=60)

    check_chain(app, crew_msgs, demo_msgs)
    jake = app.of_type("heat_alert", "demo-1")
    assert jake[0]["rule"] == "kalman"
    # Kalman rule: yellow at 38.0, red at 38.5 (backend/config.py).
    assert jake[0]["value"] >= 38.0 and jake[1]["value"] >= 38.5


def test_raw_stream_sends_mandown_confirmed_before_its_escalation(live_app):
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=1))
    app.step(FALL_AT_S)
    with app.client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        app.step(2 * 60)
        types = [m["type"] for m in app.drain(ws, raw=True) if m.get("worker_id") == "demo-2"]
    assert "mandown_confirmed" in types and "emergency_status" in types
    assert types.index("mandown_confirmed") < types.index("emergency_status")
