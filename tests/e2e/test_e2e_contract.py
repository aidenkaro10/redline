"""
4. Contract tests: record every live message and REST reply the real backend sends, and check
   each one against the shapes the frontend expects (frontend/src/lib/contract.ts, written as
   Python schemas in contract_schemas.py).

The recorded payloads are saved as JSON fixtures under tests/e2e/fixtures/ so the frontend
(session E) can test against real backend output instead of its mock:
    tests/e2e/fixtures/live/<type>.json     a few real examples of each /ws/live message type
    tests/e2e/fixtures/rest/<route>.json    one real reply per REST route the UI uses

Fixtures are rewritten only when you ask (they would churn on every run otherwise):
    E2E_WRITE_FIXTURES=1 pytest tests/e2e/test_e2e_contract.py
Without it, the tests check that the saved fixtures still match the contract.
"""

import json
import os
import re
import time
from pathlib import Path

import pytest

import contract_schemas as C
from backend.replay import shifts
from e2e_support import ENS5_PATH, FIXTURES, SIM_PATH, AppFactory

WRITE = os.getenv("E2E_WRITE_FIXTURES") == "1"
TINY_JPEG = "data:image/jpeg;base64," + "A" * 64            # a stand-in photo (small data URL)

# Paths the real frontend calls (grep of frontend/src, mock-only helpers left out). Each must exist.
UI_CALLS = [
    ("GET", "/api/sites", None), ("GET", "/api/state?view=crew", None),
    ("POST", "/api/workers/demo-1/button", {}), ("POST", "/api/reports", {"kind": "snake", "note": "ui path check"}),
    ("POST", "/api/emergency/cancel", {"worker_id": "demo-1"}), ("POST", "/api/emergency/inc-x/cancel", {}),
    ("POST", "/api/spot-check", {"worker_id": "demo-1", "thermometer_c": 37.0}),
    ("POST", "/api/demo/test-alert", {"kind": "FLOOD_FIRE"}), ("POST", "/api/demo/clear-hazards", {}),
    ("POST", "/api/demo/threshold", {"on": False}), ("POST", "/api/demo/replay", {"speed": 10}),
    ("POST", "/api/demo/mandown", {"worker_id": "demo-1"}), ("POST", "/api/demo/sos", {"worker_id": "demo-4"}),
    ("POST", "/api/mandown/demo-4/resolve", {}), ("POST", "/api/workers/demo-1/sos", {}),
]


def variant(msg: dict):
    """What makes two messages of one type worth keeping as separate examples."""
    t = msg["type"]
    if t == "mandown":
        md = msg["mandown"]
        return None if md is None else (md["stage"], md["reason"])
    if t == "emergency":
        return msg["emergency"] and msg["emergency"]["stage"]
    if t == "alert":
        a = msg["alert"]
        return a["kind"], a["acknowledged_at"] is not None, a["cleared_at"] is not None
    if t == "band_command":
        return msg["band"]["command"]
    if t == "worker_status":
        return msg["worker"]["status"], msg["worker"]["hazard"]
    if t == "offset_update":
        return msg.get("accepted")
    if t == "sos":
        return msg["sos"]["source"], msg["sos"]["escalated"]
    if t == "estimate":
        return bool(msg["estimate"]["forecast"])
    if t == "emergency_status":
        return msg["stage"], msg["status"], msg["target"]
    if t == "site_status":
        return tuple(h["level"] for h in msg["site"]["hazards"])
    return 0


def trim_snapshot(snap: dict) -> dict:
    """Snapshots are huge (79 sites, hours of history). Keep a few of each list for the fixture."""
    s = dict(snap)
    s["sites"] = s["sites"][:4]
    s["log"] = s["log"][-5:]
    s["history"] = {w: {"vitals": h["vitals"][-3:], "estimates": h["estimates"][-2:]} for w, h in s["history"].items()}
    return s


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    """
    One full scripted session with every kind of event, recorded from three dashboards
    (crew, Jake's phone, demo) plus every REST reply the UI reads.
    """
    mp = pytest.MonkeyPatch()
    factory = AppFactory(tmp_path_factory.mktemp("contract"), mp)
    real_demo = SIM_PATH is not None and ENS5_PATH is not None
    app = factory.start("--hazards", "fake", *(["--checkpoint", str(ENS5_PATH)] if real_demo else []))
    rest: dict[str, tuple[int, object]] = {}

    def call(key, method, path, body=None):
        r = app.client.request(method, path, json=body)
        rest.setdefault(key, (r.status_code, r.json() if r.content else None))
        return r

    try:
        app.start_replay(shifts.from_sim(path=SIM_PATH) if real_demo else shifts.synthetic(n_crew=3))
        from backend.hazards import picture
        deadline = time.monotonic() + 30
        while picture.latest_picture() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        bridge = app.app.state.sites
        app.on_loop(bridge.refresh)                     # C1's real SiteStatus shapes onto the dashboard

        views = {"crew": "view=crew", "worker": "view=worker&worker_id=demo-1", "demo": "view=demo"}
        msgs = {v: [] for v in views}
        sockets = {v: app.client.websocket_connect(f"/ws/live?{q}") for v, q in views.items()}
        live = {v: s.__enter__() for v, s in sockets.items()}
        for v, ws in live.items():
            msgs[v].append(ws.receive_json())

        def drain():
            for v, ws in live.items():
                msgs[v] += app.drain(ws)

        app.step(20 * 60)
        drain()
        raw = app.hub.pipelines["demo-1"].estimates[-1]["core_temp_raw"]
        call("POST /api/spot-check", "POST", "/api/spot-check", {"worker_id": "demo-1", "thermometer_c": round(raw - 0.2, 2)})
        app.client.post("/api/spot-check", json={"worker_id": "demo-1", "thermometer_c": round(raw + 3.0, 2)})  # rejected
        call("POST /api/demo/test-alert", "POST", "/api/demo/test-alert", {"kind": "LIGHTNING"})
        picture.hazard_picture()        # C1 rebuilds every 60 s; do it now so the test strike is in it
        app.on_loop(bridge.refresh)
        call("POST /api/reports", "POST", "/api/reports",
             {"kind": "snake", "note": "Rattlesnake by gate 3", "worker_id": "demo-1", "photo": TINY_JPEG})
        app.step(23 * 60 + 40)                           # red, ack, Maria's fall, confirmation, her call
        drain()
        call("POST /api/workers/{id}/sos", "POST", "/api/workers/demo-3/sos")
        rest["POST /api/workers/{id}/sos (again)"] = (lambda r: (r.status_code, r.json()))(
            app.client.post("/api/workers/demo-3/sos"))
        call("POST /api/emergency/cancel", "POST", "/api/emergency/cancel", {"worker_id": "demo-3"})
        call("POST /api/demo/sos", "POST", "/api/demo/sos", {"worker_id": "demo-4"})
        inc = next(e["incident_id"] for e in reversed(app.events)
                   if e["type"] == "emergency_status" and e["worker_id"] == "demo-4")
        call("POST /api/emergency/{id}/cancel", "POST", f"/api/emergency/{inc}/cancel")
        lightning = next(m["alert"] for m in msgs["crew"] if m["type"] == "alert" and m["alert"]["kind"] == "LIGHTNING")
        call("POST /api/alerts/{id}/ack", "POST", f"/api/alerts/{lightning['id']}/ack")
        call("POST /api/workers/{id}/button", "POST", "/api/workers/demo-1/button")
        md = app.frontend.incidents["demo-2"]["id"]
        call("POST /api/mandown/{id}/resolve", "POST", f"/api/mandown/{md}/resolve")
        for wid in ("demo-3", "demo-4"):
            app.client.post(f"/api/mandown/{wid}/resolve")
        call("POST /api/demo/threshold", "POST", "/api/demo/threshold", {"on": True})
        app.client.post("/api/demo/threshold", json={"on": False})
        call("POST /api/demo/replay", "POST", "/api/demo/replay", {"speed": 30})
        call("POST /api/demo/mandown", "POST", "/api/demo/mandown", {"worker_id": "demo-1"})
        call("POST /api/demo/clear-hazards", "POST", "/api/demo/clear-hazards")
        app.step(5)
        drain()
        for v, q in views.items():
            call(f"GET /api/state ({v})", "GET", f"/api/state?{q}")
        call("GET /api/workers", "GET", "/api/workers")
        call("GET /api/workers/{id}/history", "GET", "/api/workers/demo-1/history?view=demo&minutes=5")
        call("GET /api/sites", "GET", "/api/sites")
        call("GET /api/reports", "GET", "/api/reports")
        call("GET /api/results", "GET", "/api/results")
        ui_paths = {f"{m} {p}": app.client.request(m, p, json=b).status_code for m, p, b in UI_CALLS}
        call("POST /api/demo/scenario (stop)", "POST", "/api/demo/scenario", {"action": "stop"})
        for s in sockets.values():
            s.__exit__(None, None, None)
        raw_emergency = [e for e in app.events if e["type"] == "emergency_status"]
        yield {"msgs": msgs, "rest": rest, "ui_paths": ui_paths, "raw_emergency": raw_emergency,
               "real_demo": real_demo}
    finally:
        factory.close()
        mp.undo()


def all_live(rec) -> list[dict]:
    return [m for view in rec["msgs"].values() for m in view if m["type"] != "e2e_marker"]


# ---------------------------------------------------------------------------
# Live messages
# ---------------------------------------------------------------------------
def test_every_contract_message_type_was_recorded(recording):
    seen = {m["type"] for m in all_live(recording)}
    # emergency_status: A folds D's raw events into "emergency" on /ws/live (contract allows either).
    assert set(C.LIVE_EVENTS) - {"emergency_status"} <= seen
    assert recording["raw_emergency"], "session D's raw emergency_status events should have run too"


def test_backend_only_sends_types_the_ui_knows(recording):
    unknown = {m["type"] for m in all_live(recording)} - set(C.LIVE_EVENTS)
    assert unknown == set(), f"/ws/live sent types missing from contract.ts: {unknown}"


@pytest.mark.parametrize("kind", sorted(C.LIVE_EVENTS))
def test_live_messages_match_contract(recording, kind):
    examples = [m for m in all_live(recording) if m["type"] == kind] or \
               (recording["raw_emergency"] if kind == "emergency_status" else [])
    assert examples, f"no {kind} recorded"
    problems = {p for m in examples for p in C.check(m, C.LIVE_EVENTS[kind], kind)}
    assert not problems, "\n".join(sorted(problems)[:20])


def test_estimates_carry_the_whole_forecast(recording):
    ests = [m["estimate"] for m in all_live(recording) if m["type"] == "estimate"]
    if recording["real_demo"]:
        assert all(len(e["forecast"]) == 30 for e in ests)
        assert [p["minutes_ahead"] for p in ests[-1]["forecast"]] == list(range(1, 31))
    assert all(e["core"]["p10"] <= e["core"]["mean"] <= e["core"]["p90"] for e in ests)


def test_site_status_has_six_hazards_in_order(recording):
    sites = [m["site"] for m in all_live(recording) if m["type"] == "site_status"]
    for s in sites:
        assert [h["kind"] for h in s["hazards"]] == ["heat", "lightning", "flood", "fire", "soil", "wildlife"]
    crew_site = [s for s in sites if s["site_id"] == "eia-663"][-1]
    lightning = crew_site["hazards"][1]
    assert lightning["stop_work"] and lightning["band_pattern"] == "LIGHTNING"     # the test strike
    assert (lightning.get("lightning") or lightning)["all_clear_at"] is not None


@pytest.mark.xfail(strict=True, reason="BUG (session E, or A): the snapshot's offsets use A's flat shape "
                   "(n_checks, no history/last_check) but live.tsx stores them as OffsetUpdate without "
                   "toOffsetUpdate(); LearningCard then reads offset.history.length and crashes the demo "
                   "page on reload after a spot check.")
def test_snapshot_offsets_are_offset_updates(recording):
    snaps = [m for m in all_live(recording) if m["type"] == "snapshot"] + \
            [recording["rest"][f"GET /api/state ({v})"][1] for v in ("worker", "demo")]
    offsets = [o for s in snaps for o in (s.get("offsets") or {}).values()]
    assert offsets
    problems = {p for o in offsets for p in C.check(o, C.OFFSET_UPDATE, "offset")}
    assert not problems, sorted(problems)[:10]


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
KNOWN_REST_GAPS = {
    "POST /api/workers/{id}/button": "contract says {ok, action}; A returns {ok} only (UI ignores the reply today)",
    "POST /api/emergency/cancel": "contract says {ok} or {ok: false, error}; A returns {cancelled: bool} "
                                  "(UI ignores the reply today)",
}


@pytest.mark.parametrize("route", sorted(set(C.REST) - {"GET /api/schedule"}))   # schedule: planner test
def test_rest_replies_match_contract(recording, route, request):
    if route in KNOWN_REST_GAPS:
        request.applymarker(pytest.mark.xfail(strict=True, reason=f"contract drift (session A): {KNOWN_REST_GAPS[route]}"))
    replies = [(k, v) for k, v in recording["rest"].items() if k == route or k.startswith(route + " (")]
    assert replies, f"{route} was not called"
    for key, (status, body) in replies:
        assert status == 200, f"{key}: HTTP {status} {body}"
        problems = C.check(body, C.REST[route], key)
        assert not problems, "\n".join(problems[:20])


def test_sos_twice_is_ignored_not_called_twice(recording):
    assert recording["rest"]["POST /api/workers/{id}/sos"][1]["action"] == "escalated"
    assert recording["rest"]["POST /api/workers/{id}/sos (again)"][1]["action"] == "ignored_active_incident"


def test_every_path_the_ui_calls_exists(recording):
    missing = {k: s for k, s in recording["ui_paths"].items() if s in (404, 405) and "inc-x" not in k
               and "mandown/demo-4" not in k}
    assert missing == {}


@pytest.mark.xfail(strict=True, reason="gap (session A + ML session): GET /api/results (ModelResults) is not "
                   "served; the results page falls back to a bundled copy of docs/results.md")
def test_results_endpoint_is_served(recording):
    assert recording["rest"]["GET /api/results"][0] == 200


@pytest.mark.xfail(strict=True, reason="cosmetic contract drift (C1 + E): C1's site tech includes "
                   "battery, hydro and biomass_waste; contract.ts Site.tech only lists gas|nuclear|solar|coal|oil|biomass")
def test_site_tech_values_are_in_the_contract(recording):
    sites = recording["rest"]["GET /api/sites"][1]
    assert not [p for s in sites for p in C.check(s["tech"], C.SITE_TECH, s["id"])]


# ---------------------------------------------------------------------------
# Fixtures for the frontend
# ---------------------------------------------------------------------------
def slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def build_fixtures(rec) -> dict[str, object]:
    """{relative path: JSON content}. A few distinct real examples per message type."""
    out = {}
    by_type: dict[str, dict] = {}
    for m in all_live(rec) + rec["raw_emergency"]:
        if m["type"] == "snapshot":
            continue
        kept = by_type.setdefault(m["type"], {})
        v = variant(m)
        if v not in kept and len(kept) < 8:
            kept[v] = m
    for t, kept in by_type.items():
        out[f"live/{t}.json"] = list(kept.values())
    for view in ("crew", "worker", "demo"):
        out[f"live/snapshot_{view}.json"] = [trim_snapshot(rec["rest"][f"GET /api/state ({view})"][1])]
    for key, (status, body) in rec["rest"].items():
        if key.startswith("GET /api/state"):
            body = trim_snapshot(body)
        elif key == "GET /api/sites":
            body = body[:6]
        out[f"rest/{slug(key)}.json"] = {"request": key, "status": status, "body": body}
    return out


def test_fixtures_for_the_frontend(recording):
    fixtures = build_fixtures(recording)
    if WRITE:
        for rel, content in fixtures.items():
            path = FIXTURES / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content, indent=1, ensure_ascii=False) + "\n")
    saved = {str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*.json")}
    missing = set(fixtures) - saved
    assert not missing, f"fixtures missing, run with E2E_WRITE_FIXTURES=1: {sorted(missing)}"


@pytest.mark.parametrize("path", sorted(str(p.relative_to(FIXTURES)) for p in FIXTURES.glob("live/*.json")))
def test_saved_live_fixtures_match_contract(path):
    """The fixtures E tests against must themselves follow the contract."""
    kind = Path(path).stem.removeprefix("snapshot_") if path.startswith("live/snapshot_") else Path(path).stem
    schema = C.SNAPSHOT if path.startswith("live/snapshot_") else C.LIVE_EVENTS[kind]
    for i, msg in enumerate(json.loads((FIXTURES / path).read_text())):
        assert not C.check(msg, schema, f"{path}[{i}]")
