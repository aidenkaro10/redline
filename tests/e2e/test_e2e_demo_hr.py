"""
The labeled demo heart rate input (POST /api/demo/heart_rate), end to end in the real app:
ranges and gradual ramp, off stops it, the worker's other heart rate is ignored while it's on,
every heart rate message says "demo_input", and the crew view still never gets raw heart rate.
"""

from backend import config
from backend.replay import shifts

REST, ACTIVE, RAMP = config.DEMO_HR_REST_BPM, config.DEMO_HR_ACTIVE_BPM, config.DEMO_HR_RAMP_S


def demo_readings(app, since=0):
    return [e for e in app.events[since:] if e["type"] == "reading" and e.get("source") == "demo_input"]


def test_demo_heart_rate_end_to_end(live_app):
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=0, fall_worker=None))        # Jake, with his own replay HR
    app.step(60)
    assert app.client.get("/api/demo/heart_rate").json()["mode"] == "off"   # never on by itself
    assert demo_readings(app) == []

    with app.client.websocket_connect("/ws/live?view=crew") as crew, \
            app.client.websocket_connect("/ws/live?view=demo") as demo:
        crew.receive_json(), demo.receive_json()

        switched_at = app.clock.now()
        r = app.client.post("/api/demo/heart_rate", json={"mode": "rest", "worker_id": "demo-1"})
        assert r.status_code == 200 and r.json()["mode"] == "rest" and r.json()["demo"] is True
        start = len(app.events)
        app.step(3 * 60)
        rest = demo_readings(app, start)
        assert len(rest) >= 170                                            # one per second
        hrs = [e["hr"] for e in rest]                                      # ramps down from Jake's real HR...
        assert max(abs(y - x) for x, y in zip(hrs, hrs[1:])) < 6           # ...without jumps...
        assert all(REST[0] <= v <= REST[1] for v in hrs[RAMP + 2:])         # ...then stays in 65-75

        # Jake's replay heart rate (~95-175) is ignored while the demo input is on.
        p = app.hub.pipelines["demo-1"]
        est = [e for e in app.of_type("estimate", "demo-1") if e["timestamp"] > rest[RAMP + 60]["timestamp"]]
        assert est and all(e["hr_sources"] == ["demo_input"] and e["hr_source"] == "demo_input" for e in est)
        assert all(REST[0] <= e["hr"] <= REST[1] for e in est)

        # Rest -> active climbs over ~45 s, never jumps, then stays in 105-115.
        app.client.post("/api/demo/heart_rate", json={"mode": "active", "worker_id": "demo-1"})
        start = len(app.events)
        app.step(3 * 60)
        act = [e["hr"] for e in demo_readings(app, start)]
        assert act[0] < ACTIVE[0] and act[RAMP // 2] < ACTIVE[0]            # still climbing half way
        assert max(abs(b - a) for a, b in zip(act, act[1:])) < 6            # no jumps
        assert all(ACTIVE[0] <= v <= ACTIVE[1] for v in act[RAMP + 2:])

        msgs = {"crew": app.drain(crew), "demo": app.drain(demo)}

    # Labels: every heart rate message for Jake says demo_input; the demo event carries the flag.
    vit = [m["vitals"] for m in msgs["demo"] if m["type"] == "vitals" and m["vitals"]["worker_id"] == "demo-1"]
    assert vit and all(v["hr_source"] == "demo_input" for v in vit)
    assert all(v["hr"] is None or ACTIVE[0] - 90 <= v["hr"] <= ACTIVE[1] for v in vit)   # never Jake's real HR
    # Every minute with demo data in it says so (a minute that ended before the switch was all real HR).
    est = [m["estimate"] for m in msgs["demo"] if m["type"] == "estimate" and m["estimate"]["timestamp"] > switched_at + 1]
    assert est and all(e["hr_source"] == "demo_input" for e in est)
    for view in ("crew", "demo"):
        modes = [m for m in msgs[view] if m["type"] == "demo_heart_rate"]
        assert [m["mode"] for m in modes] == ["rest", "active"] and all(m["demo"] is True for m in modes)
        state = [m["demo"] for m in msgs[view] if m["type"] == "demo"][-1]
        assert state["demo_heart_rate"]["mode"] == "active" and state["demo_heart_rate"]["source"] == "demo_input"
    # Crew privacy unchanged: no raw heart rate, no vitals or estimates at all.
    assert not [m for m in msgs["crew"] if m["type"] in ("vitals", "estimate")]
    assert all("hr" not in m.get("demo_heart_rate", {}) for m in msgs["crew"])
    snap = app.client.get("/api/state?view=crew").json()
    assert snap["history"] == {} and snap["demo"]["demo_heart_rate"]["mode"] == "active"
    assert any("demo heart rate input ACTIVE" in e["text"] for e in snap["log"])

    # Off stops the injection, and Jake's own heart rate counts again.
    app.client.post("/api/demo/heart_rate", json={"mode": "off", "worker_id": "demo-1"})
    start = len(app.events)
    app.step(3 * 60)
    assert demo_readings(app, start) == []
    later = [e for e in app.events[start:] if e["type"] == "estimate" and e["worker_id"] == "demo-1"]
    assert later and later[-1]["hr_source"] == "watch"
    assert app.client.get("/api/demo/heart_rate").json()["mode"] == "off"


def test_demo_heart_rate_bad_input_is_422(live_app):
    c = live_app().client
    for body in ({"mode": "sprint"}, {}, {"mode": "rest", "worker_id": ""}, {"mode": 3}):
        assert c.post("/api/demo/heart_rate", json=body).status_code == 422, body
    assert c.get("/api/demo/heart_rate").json()["mode"] == "off"


def test_demo_heart_rate_is_off_after_a_restart(live_app):
    app = live_app()
    app.client.post("/api/demo/heart_rate", json={"mode": "active"})
    assert app.client.get("/api/demo/heart_rate").json()["worker_id"] == config.WORKER_ID
    live_app.stop(app)
    again = live_app()
    assert again.client.get("/api/demo/heart_rate").json()["mode"] == "off"
