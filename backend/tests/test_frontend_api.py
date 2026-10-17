"""The Next.js dashboard adapter: contract shapes, privacy filter, REST paths."""

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.app import main
from backend.app.schemas import Reading

# Keys every contract shape must have (frontend/src/lib/contract.ts).
WORKER_KEYS = {"worker_id", "name", "role", "site_id", "status", "minutes_to_danger", "status_reason", "hazard",
               "alert_acknowledged", "on_break", "break_at", "band_connected", "updated_at"}
ESTIMATE_KEYS = {"worker_id", "timestamp", "core", "kalman", "forecast", "why", "thresholds", "model"}
SNAPSHOT_KEYS = {"type", "view", "sim_time", "workers", "sites", "alerts", "mandown", "emergency", "demo",
                 "reports", "log", "history", "band"}
MANDOWN_KEYS = {"id", "worker_id", "worker_name", "site_id", "site_name", "lat", "lon", "fell_at",
                "check_started_at", "confirm_at", "confirmed_at", "stage", "reason", "last_vitals"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "SETTINGS", main.parse_args(["--source", "none", "--hazards", "off", "--db", "", "--no-forecaster"]))
    with TestClient(main.app) as c:
        yield c


def hot_worker(hub, minutes=60):
    for i in range(minutes * 6):
        hub.handle_input(Reading(worker_id="w1", timestamp=1000.0 + i * 10, hr=175, tilt=0, activity=4))


def test_snapshot_shapes_and_privacy(client):
    hot_worker(main.app.state.hub)
    crew = client.get("/api/state?view=crew").json()
    assert SNAPSHOT_KEYS <= crew.keys()
    assert WORKER_KEYS <= crew["workers"][0].keys()
    assert crew["workers"][0]["status"] == "red"
    assert crew["history"] == {} and crew["band"] == []           # no raw biometrics for crew leads
    demo = client.get("/api/state?view=demo").json()
    est = demo["history"]["w1"]["estimates"][-1]
    assert ESTIMATE_KEYS <= est.keys() and est["core"]["p10"] <= est["core"]["mean"] <= est["core"]["p90"]
    assert {a["kind"] for a in demo["alerts"]} >= {"HEAT_YELLOW", "HEAT_RED"}
    assert any(b["command"] == "BUZZ:HEAT_RED" for b in demo["band"])
    assert client.get("/api/workers/w1/history?view=crew").status_code == 403
    assert client.get("/api/workers/w1/history?view=worker&minutes=10").json()["vitals"]


def test_ws_live_filters_by_view(client):
    hub = main.app.state.hub
    with client.websocket_connect("/ws/live?view=crew") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        hub.handle_input(Reading(worker_id="w1", timestamp=1000.0, hr=100))
        hub.set_site_hazard(None, "lightning", True)
        seen = set()
        for _ in range(6):
            msg = ws.receive_json()
            seen.add(msg["type"])
            if msg["type"] == "alert":
                break
        assert "vitals" not in seen and "alert" in seen


def test_mandown_flow_and_resolve(client):
    hub = main.app.state.hub
    hub.handle_input(Reading(worker_id="w1", timestamp=1000.0, hr=100, tilt=0, activity=3))
    client.post("/api/demo/mandown", json={"worker_id": "w1"})
    md = client.get("/api/state").json()["mandown"]
    assert MANDOWN_KEYS <= md.keys() and md["stage"] == "checking" and md["reason"] == "demo"
    assert md["confirm_at"] - md["check_started_at"] == config.MANDOWN_ESCALATE_S
    client.post(f"/api/mandown/{md['id']}/resolve")
    assert client.get("/api/state").json()["mandown"] is None


def test_demo_rest_paths(client):
    hub = main.app.state.hub
    hub.handle_input(Reading(worker_id="w1", timestamp=1000.0, hr=100))
    client.post("/api/demo/test-alert", json={"kind": "LIGHTNING"})
    assert hub.pipelines["w1"].router.current == "LIGHTNING"
    client.post("/api/demo/clear-hazards")
    assert hub.pipelines["w1"].router.current == "OFF"
    d = client.post("/api/demo/threshold", json={"on": True}).json()
    assert d["demo_threshold"] and d["thresholds"]["danger"] == config.DEMO_HEAT_DANGER_C
    d = client.post("/api/demo/replay", json={"on": True, "speed": 20}).json()
    assert d["source"] == "replay" and d["speed"] == 20
    assert client.post("/api/demo/replay", json={"speed": 40}).json()["speed"] == 40
    assert client.post("/api/demo/scenario", json={"action": "stop"}).json()["source"] == "live"
    assert client.post("/api/workers/w1/button").json() == {"ok": True}


def test_reports_sites_schedule(client):
    r = client.post("/api/reports", json={"kind": "snake", "note": "under the trailer"}).json()
    hub = main.app.state.hub
    assert r["report"]["kind"] == "snake" and r["report"]["lat"] == hub.sites[hub.default_site_id]["lat"]
    assert client.get("/api/reports").json()[0]["note"] == "under the trailer"
    assert client.get("/api/sites").json()[0]["id"] == "site-gnv"
    assert client.get("/api/schedule").status_code == 404
    client.post("/api/plans", json={"date": "2026-10-17", "rows": []})
    assert client.get("/api/schedule").json()["date"] == "2026-10-17"
    client.post("/api/sites/site-gnv/status", json={"hazards": [{"kind": "heat", "level": "high"}], "index_tomorrow": 7})
    s = client.get("/api/state").json()["sites"][0]
    assert s["index_tomorrow"] == 7 and s["hazards"][0]["level"] == "high"


def test_cors_allows_dashboard(client):
    r = client.options("/api/demo/threshold", headers={"Origin": "http://localhost:3000",
                                                       "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_sos_incident_flow(client):
    hub = main.app.state.hub
    hub.handle_input(Reading(worker_id="w1", timestamp=1000.0, hr=100, tilt=0, activity=3))
    with client.websocket_connect("/ws/live?view=crew") as ws:
        ws.receive_json()
        assert client.post("/api/workers/w1/sos").json()["action"] == "escalated"
        sos = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "sos":
                sos = msg["sos"]
                break
        assert sos["source"] == "phone" and sos["escalated"] is True
    st = client.get("/api/state").json()
    assert st["mandown"]["reason"] == "sos" and st["mandown"]["stage"] == "confirmed"
    site = next(s for s in st["sites"] if s["site_id"] == st["workers"][0]["site_id"])
    assert st["workers"][0]["hazard"] == "SOS" and site["worker_flag"] == "sos"
    assert client.post("/api/workers/w1/sos").json()["action"] == "ignored_active_incident"
    client.post(f"/api/mandown/{st['mandown']['id']}/resolve")
    assert client.get("/api/state").json()["mandown"] is None


def test_report_photo_too_big(client):
    assert client.post("/api/reports", json={"kind": "snake", "photo": "x" * 2_000_001}).status_code == 413


def test_offset_update_is_body_data_not_for_crew(client):
    hub = main.app.state.hub
    hot_worker(hub, minutes=5)
    raw = hub.pipelines["w1"].estimates[-1]["core_temp_raw"]
    sub = lambda f: [msg for msg in [{"type": "offset_update", "worker_id": "w1", "offset_c": 0.1}]
                     if main.app.state.frontend.can_see(f, msg)]
    assert sub({"view": "crew"}) == []
    assert sub({"view": "worker", "worker_id": "w2"}) == []
    assert len(sub({"view": "worker", "worker_id": "w1"})) == 1 and len(sub({"view": "demo"})) == 1
    # End to end: a crew socket sees the spot check's other effects, never the offset itself.
    with client.websocket_connect("/ws/live?view=crew") as crew, client.websocket_connect("/ws/live?view=demo") as demo:
        crew.receive_json(), demo.receive_json()
        client.post("/api/spot-check", json={"worker_id": "w1", "thermometer_c": raw - 0.3})
        hub.set_site_hazard(None, "lightning", True)             # a marker event both views get
        crew_types, demo_types = [], []
        for ws, out in ((crew, crew_types), (demo, demo_types)):
            while True:
                m = ws.receive_json()
                out.append(m["type"])
                if m["type"] == "alert" and m["alert"]["kind"] == "LIGHTNING":
                    break
        assert "offset_update" not in crew_types and "offset_update" in demo_types
