"""
Bluetooth heart rate must never take the server down, and the tests must never touch Bluetooth.

On a Mac, a process whose app has no Bluetooth permission is KILLED the moment CoreBluetooth is
used (TCC privacy violation). So: the server reads Bluetooth in a helper process, and nothing in
the test suite imports bleak.
"""

import socket
import sys
import time

from backend.ingest import ble_hr


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def live(live_app):
    return live_app("--source", "live", "--udp-port", str(free_udp_port()), "--serial", "", "--no-pulsoid")


def test_live_mode_in_tests_never_imports_bleak(live_app):
    app = live(live_app)
    assert app.client.get("/api/state").status_code == 200
    assert "bleak" not in sys.modules                       # importing bleak loads CoreBluetooth
    assert ble_hr.safe_to_scan() is False
    assert app.of_type("hr_link") == []                     # no Bluetooth reader was started


# A fake helper: says it's connected, sends one heart rate, then dies the way macOS kills a
# process for a Bluetooth privacy violation (SIGABRT).
ABORTING_HELPER = (
    "import json, os\n"
    "print(json.dumps({'kind': 'link', 'state': 'connected', 'device': 'HeartCast', 'message': 'receiving'}), flush=True)\n"
    "print(json.dumps({'kind': 'hr', 'hr': 97}), flush=True)\n"
    "os.abort()\n"
)


def test_helper_killed_by_sigabrt_leaves_the_server_up(live_app, monkeypatch):
    monkeypatch.setattr(ble_hr, "safe_to_scan", lambda: True)
    monkeypatch.setattr(ble_hr, "helper_command", lambda wanted=None: [sys.executable, "-c", ABORTING_HELPER])
    app = live(live_app)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not any(e["state"] == "lost" for e in app.of_type("hr_link")):
        time.sleep(0.1)
    links = app.of_type("hr_link")
    lost = [e for e in links if e["state"] == "lost"]
    assert lost, links
    assert "unavailable" in lost[0]["message"] and "signal 6" in lost[0]["message"]
    # The heart rate it sent before dying arrived, tagged as Bluetooth.
    assert any(e.get("hr") == 97 and e.get("source") == "ble" for e in app.of_type("reading"))
    # The server is still up and serving.
    assert app.client.get("/api/state").status_code == 200
    assert app.client.get("/api/workers").status_code == 200
    # The dashboard got a log line saying Bluetooth heart rate was lost.
    snap = app.client.get("/api/state?view=crew").json()
    assert any("Bluetooth heart rate lost" in entry["text"] for entry in snap["log"])
    assert "bleak" not in sys.modules                       # the parent never loaded it
