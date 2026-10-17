"""
3. Privacy: crew leads see status only, never raw biometrics (PROJECT.md, frontend contract).

We play a synthetic shift with heat alerts, a spot check (personal offset), a fall and an SOS,
with four dashboards connected at once, then look at every message each one received:
  - crew:                 no vitals, no core temp, no forecast, no personal offset, no band commands
  - worker (own id):      their own body data only
  - demo (judges' view):  everyone's body data
The one allowed exception: a man-down / SOS incident carries last_vitals, because responders need them.
"""

import pytest

from backend.replay import shifts
from e2e_support import TEST_NUMBERS, AppFactory

# Keys that hold body data. They must never appear in the crew view (outside last_vitals).
BODY_KEYS = {"hr", "skin_temp", "core_temp", "core", "kalman", "forecast", "offset_c", "thermometer_c",
             "model_c", "miss_c", "hr_bpm", "core_temp_c", "skin_temp_c", "core_temp_sd", "kalman_sd",
             "core_temp_raw", "true_core_temp", "activity", "tilt", "last_reading", "p10", "p50", "p90",
             "hr_watch", "hr_band", "offset_applied_c", "last_check", "confidence"}
ALLOWED_UNDER = "last_vitals"           # responders' exception (contract: Privacy)


def body_paths(obj, path=""):
    """Every place body data shows up in a message, e.g. 'snapshot.history.demo-1.vitals'."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            if ALLOWED_UNDER in here.split("."):
                continue
            if k in BODY_KEYS and v not in (None, [], {}):
                found.append(here)
            found += body_paths(v, here)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += body_paths(v, f"{path}[{i}]")
    return found


def owner(msg):
    """Which worker a body-data message is about."""
    if msg["type"] == "offset_update":
        return msg["worker_id"]
    return msg[msg["type"]]["worker_id"]


@pytest.fixture(scope="module")
def streams(tmp_path_factory):
    """
    One shared run for every test in this file (it's the slow part): four dashboards
    connected while a synthetic shift plays with alerts, a spot check, a fall and an SOS.
    """
    mp = pytest.MonkeyPatch()
    factory = AppFactory(tmp_path_factory.mktemp("privacy"), mp)
    app = factory.start()
    app.start_replay(shifts.synthetic(n_crew=3))
    views = {"crew": "view=crew", "worker1": "view=worker&worker_id=demo-1", "demo": "view=demo",
             "worker_no_id": "view=worker"}
    got = {name: [] for name in views}
    sockets = {name: app.client.websocket_connect(f"/ws/live?{q}") for name, q in views.items()}
    try:
        live = {name: s.__enter__() for name, s in sockets.items()}
        for name, ws in live.items():
            got[name].append(ws.receive_json())                 # the snapshot

        def drain_all():
            for name, ws in live.items():
                got[name] += app.drain(ws)

        app.step(20 * 60)
        drain_all()
        # A thermometer spot check for Jake: his personal offset is body data.
        raw = app.hub.pipelines["demo-1"].estimates[-1]["core_temp_raw"]
        r = app.client.post("/spot-check", json={"worker_id": "demo-1", "thermometer_c": round(raw - 0.3, 2)})
        assert r.status_code == 200 and r.json()["accepted"]
        app.step(25 * 60)                       # Maria falls at minute 40; her escalation runs
        app.client.post("/api/workers/demo-3/sos")        # Dev holds his SOS button (phone)
        app.step(40)
        drain_all()
        # A fresh connection after all that: the snapshots must be filtered too.
        for name, q in views.items():
            with app.client.websocket_connect(f"/ws/live?{q}") as ws:
                got[name + "_late_snapshot"] = [ws.receive_json()]
        rest = {name: app.client.get(f"/api/state?{q}").json() for name, q in views.items()}
        history = {view: app.client.get(f"/api/workers/demo-2/history?view={view}") for view in
                   ("crew", "worker", "demo")}
        own_history = app.client.get("/api/workers/demo-1/history?view=worker&worker_id=demo-1")
        other_history = app.client.get("/api/workers/demo-2/history?view=worker&worker_id=demo-1")
        yield {"ws": got, "rest": rest, "history": history, "own_history": own_history,
               "other_history": other_history, "app": app}
    finally:
        for s in sockets.values():
            try:
                s.__exit__(None, None, None)
            except Exception:
                pass
        factory.close()
        mp.undo()


def test_A29_scenario_really_produced_body_data(streams):
    """Guard: the demo view got all the sensitive stuff, so the crew checks below mean something."""
    demo = streams["ws"]["demo"]
    types = {m["type"] for m in demo}
    assert {"vitals", "estimate", "offset_update", "band_command", "mandown", "sos", "emergency"} <= types
    assert {owner(m) for m in demo if m["type"] in ("vitals", "estimate")} == {"demo-1", "demo-2", "demo-3",
                                                                                "demo-4"}


@pytest.mark.parametrize("stream", ["crew", "crew_late_snapshot", "worker_no_id"])
def test_A29_crew_view_never_gets_body_data(streams, stream):
    msgs = streams["ws"][stream]
    assert not {m["type"] for m in msgs} & {"vitals", "estimate", "offset_update", "why"}
    if stream.startswith("crew"):
        assert "band_command" not in {m["type"] for m in msgs}     # contract: band commands not to crew
    leaks = [(m["type"], p) for m in msgs for p in body_paths(m)]
    assert leaks == [], f"body data reached the {stream} view: {leaks[:10]}"
    snap = msgs[0]
    assert snap["type"] == "snapshot" and snap["history"] == {} and snap["band"] == []
    assert snap.get("offsets", {}) == {}


def test_A29_crew_still_gets_status_alerts_and_incidents(streams):
    """Privacy must not blind the crew lead: status, alerts, man-down and SOS all arrive."""
    crew = streams["ws"]["crew"]
    types = {m["type"] for m in crew}
    assert {"worker_status", "alert", "mandown", "sos", "emergency", "site_status"} <= types
    md = [m["mandown"] for m in crew if m["type"] == "mandown" and m["mandown"]]
    assert any(x["last_vitals"]["core_temp"] is not None for x in md)     # responders' exception
    for m in crew:
        if m["type"] == "worker_status":
            reason = m["worker"]["status_reason"]
            assert "°" not in reason and "bpm" not in reason          # plain words, no body numbers


def test_A29_worker_view_gets_only_their_own_body_data(streams):
    msgs = streams["ws"]["worker1"]
    body = [m for m in msgs if m["type"] in ("vitals", "estimate", "offset_update")]
    assert body, "the worker's own phone should get their vitals, estimate and offset"
    assert {owner(m) for m in body} == {"demo-1"}
    assert any(m["type"] == "offset_update" for m in body)
    snap = streams["ws"]["worker1_late_snapshot"][0]
    assert set(snap["history"]) == {"demo-1"} and set(snap.get("offsets", {})) == {"demo-1"}
    assert snap["offsets"]["demo-1"]["n_checks"] == 1


def test_A29_demo_view_gets_everyones_body_data(streams):
    snap = streams["ws"]["demo_late_snapshot"][0]
    assert set(snap["history"]) == {"demo-1", "demo-2", "demo-3", "demo-4"}
    assert set(snap["offsets"]) == {"demo-1", "demo-2", "demo-3", "demo-4"}
    assert snap["band"], "the demo view shows band commands"


def test_A29_rest_state_and_history_follow_the_same_rules(streams):
    rest, hist = streams["rest"], streams["history"]
    assert body_paths(rest["crew"]) == []
    assert rest["crew"]["history"] == {} and rest["crew"].get("offsets", {}) == {}
    assert set(rest["worker1"]["history"]) == {"demo-1"}
    assert hist["crew"].status_code == 403                      # contract: crew can't read history
    assert hist["demo"].status_code == 200 and hist["demo"].json()["vitals"]
    assert streams["own_history"].status_code == 200 and streams["own_history"].json()["vitals"]


@pytest.mark.xfail(strict=True, reason="BUG (session A): /api/workers/{id}/history ignores worker_id, so "
                   "view=worker can read ANOTHER worker's raw vitals. Expected 403.")
def test_A29_worker_view_cannot_read_another_workers_history(streams):
    assert streams["other_history"].status_code == 403


@pytest.mark.xfail(strict=True, reason="BUG (session D, shown by A): DryRunPhone/FakePhone status messages say "
                   "'Dry run: would call +1352...' with the FULL number; A copies the message into "
                   "EmergencyCall.steps[].text for every view, crew included. Only number_masked should go out.")
def test_A29_no_full_phone_number_reaches_any_dashboard(streams):
    """Phone numbers on screen are masked (+1 ••• ••• 0101). The full number never goes out."""
    for name, msgs in streams["ws"].items():
        text = str(msgs)
        for number in TEST_NUMBERS.values():
            assert number not in text and number[1:] not in text, f"{number} reached the {name} view"
