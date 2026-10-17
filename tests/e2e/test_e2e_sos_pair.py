"""
Two SOS presses from different workers a few seconds apart must never get mixed up:
each call keeps its own worker, incident and texts, the dashboard always shows a man-down
next to ITS OWN call, and "Cancel, false alarm" stops only the call it was pressed for.
(Found in the recorded fixtures: the screen showed "SOS from Dev" beside Tasha's call.)
"""

from backend.replay import shifts


def calls_by_worker(app) -> dict:
    return {c["worker_id"]: c for c in app.frontend.calls.values()}


def statuses(app, incident_id: str) -> list[tuple]:
    return [(e["stage"], e["status"]) for e in app.of_type("emergency_status") if e["incident_id"] == incident_id]


def test_two_sos_presses_stay_separate_and_cancel_hits_only_one(live_app):
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=2, fall_worker=None))       # Jake, Maria, Dev
    app.step(60)
    names = {w: app.hub.workers[w]["name"] for w in ("demo-2", "demo-3")}

    with app.client.websocket_connect("/ws/live?view=crew") as crew:
        crew.receive_json()
        assert app.client.post("/api/workers/demo-2/sos").json()["action"] == "escalated"     # Maria
        app.step(3)
        assert app.client.post("/api/workers/demo-3/sos").json()["action"] == "escalated"     # Dev, 3 s later
        app.step(2)
        msgs = app.drain(crew)

        # One call per incident, each wired to its own worker's incident.
        calls = calls_by_worker(app)
        assert set(calls) == {"demo-2", "demo-3"}
        for wid, call in calls.items():
            incident = app.frontend.incidents[wid]
            assert call["mandown_id"] == incident["id"] and incident["worker_id"] == wid
            other = names["demo-3" if wid == "demo-2" else "demo-2"]
            assert not any(other in step["text"] for step in call["steps"]), (wid, call["steps"])
        # Every emergency message the crew screen got is matched to its own man-down.
        md_worker = {}
        for m in msgs:
            if m["type"] == "mandown" and m["mandown"]:
                md_worker[m["mandown"]["id"]] = m["mandown"]["worker_id"]
            if m["type"] == "emergency" and m["emergency"]:
                e = m["emergency"]
                assert md_worker.get(e["mandown_id"]) == e["worker_id"], e

        # The snapshot pairs the man-down on screen with its own call.
        snap = app.client.get("/api/state?view=crew").json()
        assert snap["mandown"] and snap["emergency"]
        assert snap["emergency"]["mandown_id"] == snap["mandown"]["id"]
        assert snap["emergency"]["worker_id"] == snap["mandown"]["worker_id"]

        # Cancel Maria's call by ITS incident id: only Maria's stops.
        maria, dev = calls["demo-2"]["id"], calls["demo-3"]["id"]
        assert app.client.post(f"/api/emergency/{maria}/cancel").json() == {"ok": True}
        app.step(1)
        assert ("done", "cancelled") in statuses(app, maria)
        assert ("done", "cancelled") not in statuses(app, dev)
        assert not any(i.done for i in app.emergency.incidents if i.event.worker_id == "demo-3")
        assert calls_by_worker(app)["demo-3"]["stage"] != "cancelled"
        assert app.client.post("/api/emergency/no-such-call/cancel").status_code == 404
