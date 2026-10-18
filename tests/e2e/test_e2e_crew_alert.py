"""
Crewmate alert: when a worker's man-down is confirmed or they press SOS, every OTHER band at the
same site plays the man-down pattern, a band at another site doesn't, a crewmate's button never
ends the incident, and the alert clears for everyone when the incident ends.
"""

from backend import config
from backend.replay import shifts


def last_line(app, wid):
    lines = app.band.lines_for(wid)
    return lines[-1] if lines else None


def setup(live_app):
    app = live_app()
    # Jake (demo-1), Maria (demo-2, falls 5 min in), Dev (demo-3), all at the demo site.
    app.start_replay(shifts.synthetic(n_crew=2, fall_worker=1, fall_at_min=5, fall_for_min=4))
    assert app.client.post("/api/sites", json={"site_id": "far", "name": "Far plant", "lat": 27.0,
                                               "lon": -80.5}).status_code == 200
    assert app.client.post("/api/workers", json={"worker_id": "w-far", "name": "Lee", "site_id": "far"}).status_code == 200
    return app


def test_confirmed_mandown_buzzes_crewmates_and_clears_on_resolve(live_app):
    app = setup(live_app)
    app.step(12 * 60, until=lambda: app.of_type("mandown_confirmed", "demo-2"))
    assert app.of_type("mandown_confirmed", "demo-2")
    app.step(1)
    for wid in ("demo-1", "demo-3"):
        assert last_line(app, wid) == "BUZZ:MANDOWN", wid                 # crewmates at the site
    assert "BUZZ:MANDOWN" not in app.band.lines_for("w-far")              # other site: nothing
    log = [e["text"] for e in app.client.get("/api/state?view=crew").json()["log"]]
    assert any(t == "Alerted 2 crewmates: Jake, Dev" for t in log), log

    # A crewmate's short press silences their own band (firmware) but never ends Maria's incident.
    app.client.post("/api/workers/demo-1/button")
    app.step(5)
    assert app.hub.pipelines["demo-2"].mandown.state == "confirmed"
    assert app.frontend.incidents["demo-2"]["stage"] == "confirmed"
    assert last_line(app, "demo-1") == "BUZZ:MANDOWN"                    # still alerted until resolved
    assert not app.of_type("mandown_cancelled", "demo-2")

    # Crew lead resolves Maria's man-down: crewmates' bands go back to their own status.
    md = app.frontend.incidents["demo-2"]["id"]
    app.client.post(f"/api/mandown/{md}/resolve")
    app.step(1)
    for wid in ("demo-1", "demo-3"):
        assert last_line(app, wid) != "BUZZ:MANDOWN", wid
    log = [e["text"] for e in app.client.get("/api/state?view=crew").json()["log"]]
    assert any("Crewmate alert for Maria cleared" in t for t in log)
    # Man-down timers and escalation unchanged: check after MANDOWN_STILL_S, confirm after MANDOWN_ESCALATE_S.
    check, conf = app.of_type("mandown_check", "demo-2")[0], app.of_type("mandown_confirmed", "demo-2")[0]
    assert abs(conf["timestamp"] - check["timestamp"] - config.MANDOWN_ESCALATE_S) <= 2


def test_sos_buzzes_crewmates_and_clears_on_resolve(live_app):
    app = setup(live_app)
    app.step(60)
    assert app.client.post("/api/workers/demo-3/sos").json()["action"] == "escalated"     # Dev
    app.step(1)
    for wid in ("demo-1", "demo-2"):
        assert last_line(app, wid) == "BUZZ:MANDOWN", wid
    assert last_line(app, "demo-3") != "BUZZ:MANDOWN"                     # SOS is never sent to his own band
    assert "BUZZ:MANDOWN" not in app.band.lines_for("w-far")
    app.client.post("/api/workers/demo-2/button")                         # a crewmate's press
    app.step(2)
    assert "demo-3" in app.frontend.incidents                             # Dev's SOS still open
    md = app.frontend.incidents["demo-3"]["id"]
    app.client.post(f"/api/mandown/{md}/resolve")
    app.step(1)
    for wid in ("demo-1", "demo-2"):
        assert last_line(app, wid) != "BUZZ:MANDOWN", wid
