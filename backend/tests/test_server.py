"""FastAPI server: page, snapshot, WebSocket, demo controls, plug-ins, SQLite."""

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.schemas import Reading
from backend.storage.db import Database


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = main.parse_args(["--source", "none", "--db", str(tmp_path / "t.db"), "--no-forecaster"])
    monkeypatch.setattr(main, "SETTINGS", settings)
    with TestClient(main.app) as c:
        yield c


def add_readings(hub, worker_id="w1", n=30, hr=170):
    for i in range(n):
        hub.handle_input(Reading(worker_id=worker_id, timestamp=1000.0 + i * 10, hr=hr, tilt=0, activity=3))


def test_page_and_state(client):
    assert "chart.umd" in client.get("/").text
    add_readings(main.app.state.hub)
    s = client.get("/api/raw/state").json()
    assert s["type"] == "snapshot" and s["workers"][0]["worker_id"] == "w1"
    assert s["workers"][0]["history"]


def test_websocket_snapshot_then_events(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        client.post("/api/demo/threshold", json={"on": True})
        assert ws.receive_json()["type"] == "demo"


def test_demo_controls(client):
    hub = main.app.state.hub
    add_readings(hub)
    r = client.post("/api/demo/hazard", json={"kind": "lightning", "on": True})
    assert r.json()["pattern"] == "LIGHTNING"
    assert hub.pipelines["w1"].router.current == "LIGHTNING"
    assert client.post("/api/demo/hazard", json={"kind": "tornado"}).status_code == 400
    client.post("/api/demo/fall", json={"worker_id": "w1"})
    assert hub.pipelines["w1"].mandown.state == "checking"
    client.post("/api/demo/button", json={"worker_id": "w1"})
    assert hub.pipelines["w1"].mandown.state == "ok"
    assert client.post("/api/demo/fall", json={"worker_id": "nobody"}).status_code == 404
    assert client.post("/api/replay/speed", json={"speed": 5}).status_code == 409


def test_replay_toggle(client):
    r = client.post("/api/replay", json={"on": True, "source": "synthetic", "speed": 20})
    assert r.json()["on"] and r.json()["speed"] == 20
    assert client.post("/api/replay/speed", json={"speed": 50}).json()["speed"] == 50
    assert client.post("/api/replay", json={"on": False}).json() == {"on": False}
    assert not main.app.state.hub.pipelines


def test_plugins_sites_workers_plans(client):
    client.post("/api/sites", json={"site_id": "p1", "name": "Plant 1", "lat": 28.0, "lon": -81.0})
    assert client.post("/api/workers", json={"worker_id": "w5", "name": "Ana", "site_id": "p1"}).json()["site_id"] == "p1"
    client.post("/api/sites/p1/weather", json={"wbgt": 30.5})
    assert main.app.state.hub.pipelines["w5"].context["wbgt"] == 30.5
    assert client.post("/api/plans", json={"slots": []}).json()["type"] == "plan"
    assert client.post("/api/workers", json={"worker_id": "w6", "site_id": "nope"}).status_code == 404


def test_database_tables(tmp_path):
    from backend.app.hub import Hub
    from backend.alerts.band_output import MultiBand
    db = Database(tmp_path / "x.db")
    hub = Hub(band=MultiBand([]), db=db)
    add_readings(hub, n=200)
    hub.add_plan({"slots": [1, 2]})
    db.commit()
    assert db.query("SELECT COUNT(*) FROM readings")[0][0] == 200
    assert db.query("SELECT COUNT(*) FROM estimates")[0][0] > 20
    assert db.query("SELECT name FROM workers")[0][0] == "w1"
    kinds = {r[0] for r in db.query("SELECT type FROM alerts")}
    assert {"heat_alert", "band_command"} <= kinds
    assert db.query("SELECT COUNT(*) FROM plans")[0][0] == 1
    db.close()


def test_recorder_roundtrip(tmp_path):
    from backend.replay.shifts import from_jsonl
    from backend.storage.recorder import Recorder
    rec = Recorder(tmp_path / "s.jsonl")
    rec.write(Reading(worker_id="w1", timestamp=5.0, hr=90))
    rec.write(Reading(worker_id="w1", timestamp=10.0, skin_temp=34.0))
    rec.close()
    plan = from_jsonl(tmp_path / "s.jsonl")
    assert [i.data.get("hr") for i in plan.items] == [90, None]


def test_database_logs_hr_sources(tmp_path):
    from backend.app.hub import Hub
    from backend.alerts.band_output import MultiBand
    from backend.app.schemas import events_from_line
    db = Database(tmp_path / "s.db")
    hub = Hub(band=MultiBand([]), db=db)
    for i in range(20):
        for ev in events_from_line("HR:120", "w1", 1000.0 + i * 5):
            hub.handle_input(ev, source="serial")
    db.commit()
    assert db.query("SELECT DISTINCT source FROM readings") == [("band",)]
    assert db.query("SELECT hr_band, hr_sources FROM estimates LIMIT 1") == [(120.0, "band")]
    db.close()


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY, worker_id TEXT, timestamp REAL, hr REAL, "
                "skin_temp REAL, tilt INTEGER, activity REAL, ax REAL, ay REAL, az REAL)")
    con.commit()
    con.close()
    db = Database(path)
    assert "source" in {r[1] for r in db.query("PRAGMA table_info(readings)")}
    db.close()


def test_spot_check_route_persists_offset(tmp_path, monkeypatch):
    path = tmp_path / "o.db"
    monkeypatch.setattr(main, "SETTINGS", main.parse_args(["--source", "none", "--db", str(path), "--no-forecaster"]))
    with TestClient(main.app) as c:
        hub = main.app.state.hub
        add_readings(hub, n=90, hr=130)
        raw = hub.pipelines["w1"].estimates[-1]["core_temp_raw"]
        with c.websocket_connect("/ws") as ws:
            ws.receive_json()
            r = c.post("/spot-check", json={"worker_id": "w1", "thermometer_c": raw - 0.3}).json()
            assert r["accepted"] and r["offset_c"] > 0
            seen = [ws.receive_json()["type"] for _ in range(3)]
            assert "offset_update" in seen
        assert c.post("/api/spot-check", json={"worker_id": "nobody", "thermometer_c": 37}).status_code == 404
    # A new server run loads the saved offset for the same worker.
    with TestClient(main.app) as c:
        hub = main.app.state.hub
        add_readings(hub, n=5)
        assert hub.pipelines["w1"].offset_status()["n_checks"] == 1
