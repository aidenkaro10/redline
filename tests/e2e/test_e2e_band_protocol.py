"""
2. Band protocol: the test pretends to be the wristband and talks to the real server
   over the two real links, UDP (WiFi) and a pty (stands in for the USB serial cable).

It sends the exact lines from docs/firmware-contract.md (TEMP, TILT/ACT, HR, BTN:1,
BTN:SOS, "#" debug lines) plus garbage, and checks:
  - each good line becomes a reading or button press, stamped with the laptop's clock
  - garbage and "#" lines are ignored
  - BUZZ commands come back down the same link, by router priority
  - the 20 s keep-alive re-sends the current command (and not sooner, and not to replay workers)
  - BTN:SOS starts session D's escalation right away, and is never sent to the band
"""

import os
import select
import socket
import time
import tty

import pytest

from backend import config

# Lines the band must ignore: firmware debug prints, boot noise, broken values, unknown keys.
GARBAGE = ["# fake band booting", "# TEMP:99.9", "hello", "TEMP:", "TEMP:abc", ",,,", ":::",
           "BTN:2", "FOO:1", "ACC:1,2", "\x00\x7f junk", "temp34"]


def wait_for(pred, timeout=5.0, what="condition"):
    """Poll until pred() is truthy (the server runs on its own thread)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def readings(app, wid):
    return app.of_type("reading", wid)


def join(app, band, *lines):
    """
    Band says hello. Within one server tick (0.5 s) the laptop sends it its current status
    (keep-alive), so a fresh band never shows "no connection". Then start with a clean list.
    """
    band.send(*(lines or ("TEMP:34.0",)))
    wait_for(lambda: len(readings(app, band.worker_id)) >= max(1, len(lines)), what="band joined")
    wait_for(lambda: "BUZZ:OFF" in band.commands(), timeout=3, what="first keep-alive after joining")
    band.got.clear()


# ---------------------------------------------------------------------------
# A UDP band
# ---------------------------------------------------------------------------
class UdpBand:
    """The test's fake band on WiFi: sends lines, collects BUZZ commands sent back."""

    def __init__(self, port: int, worker_id: str):
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.got: list[str] = []
        self.worker_id = worker_id
        self.send(f"ID:{worker_id}")           # which worker this band is (accepted extra line)

    def send(self, *lines: str) -> None:
        self.sock.sendto(("\n".join(lines) + "\n").encode("utf-8", errors="replace"), ("127.0.0.1", self.port))

    def commands(self) -> list[str]:
        """Everything the laptop has sent us so far."""
        while True:
            try:
                data = self.sock.recv(1024)
            except (socket.timeout, BlockingIOError):
                return self.got
            self.got += [ln for ln in data.decode().splitlines() if ln]

    def close(self):
        self.sock.close()


@pytest.fixture
def udp_app(live_app, monkeypatch):
    port = free_udp_port()
    app = live_app("--source", "live", "--udp-port", str(port), "--serial", "", "--no-pulsoid")
    band = UdpBand(port, "band-7")
    yield app, band
    band.close()


def test_A03_udp_lines_become_readings_with_laptop_timestamps(udp_app):
    app, band = udp_app
    before = time.time()
    band.send("TEMP:34.62", "TILT:1,ACT:7", "HR:121")
    wait_for(lambda: len(readings(app, "band-7")) >= 3, what="3 readings")
    after = time.time()
    r = readings(app, "band-7")
    assert r[0]["skin_temp"] == 34.62
    assert (r[1]["tilt"], r[1]["activity"]) == (1, 7.0)
    assert r[2]["hr"] == 121.0
    for x in r:
        assert x["source"] == "band"                       # band lines are tagged "band"
        assert before <= x["timestamp"] <= after           # stamped by the laptop on arrival
    assert app.hub.workers["band-7"]["source"] == "udp"


def test_A02_udp_ignores_debug_lines_and_garbage(udp_app):
    app, band = udp_app
    band.send("TEMP:33.00")
    wait_for(lambda: readings(app, "band-7"), what="first reading")
    n_events = len(app.events)
    band.send(*GARBAGE)
    band.send("TEMP:33.33")                                  # a good line after the junk
    wait_for(lambda: any(e.get("skin_temp") == 33.33 for e in app.events), what="reading after garbage")
    new = app.events[n_events:]
    data = [e for e in new if e["type"] in ("reading", "button", "sos")]
    assert [e.get("skin_temp") for e in data] == [33.33], f"garbage leaked through: {data}"


def test_A15_A17_udp_commands_come_back_by_priority_and_button_cancels(udp_app):
    app, band = udp_app
    join(app, band)

    # Lightning at the band's site -> BUZZ:LIGHTNING down the same UDP link.
    assert app.client.post("/api/demo/hazard", json={"kind": "lightning", "on": True}).status_code == 200
    wait_for(lambda: "BUZZ:LIGHTNING" in band.commands(), what="BUZZ:LIGHTNING")

    # A man-down check outranks lightning (MANDOWN > HEAT_RED > LIGHTNING > ...).
    app.client.post("/api/demo/fall", json={"worker_id": "band-7"})
    wait_for(lambda: band.commands()[-1:] == ["BUZZ:MANDOWN"], what="BUZZ:MANDOWN")

    # The worker's short press (BTN:1) cancels the check; the band goes back to lightning.
    band.send("BTN:1")
    wait_for(lambda: app.of_type("mandown_cancelled", "band-7"), what="mandown_cancelled")
    assert app.of_type("mandown_cancelled", "band-7")[0]["by"] == "button"
    wait_for(lambda: band.commands()[-1:] == ["BUZZ:LIGHTNING"], what="back to BUZZ:LIGHTNING")

    # All clear.
    app.client.post("/api/demo/hazard", json={"kind": "lightning", "on": False})
    wait_for(lambda: band.commands()[-1:] == ["BUZZ:OFF"], what="BUZZ:OFF")
    assert band.commands() == ["BUZZ:LIGHTNING", "BUZZ:MANDOWN", "BUZZ:LIGHTNING", "BUZZ:OFF"]


def test_A16_keep_alive_resends_current_command_every_20_s(udp_app):
    app, band = udp_app
    join(app, band)
    app.client.post("/api/demo/hazard", json={"kind": "lightning", "on": True})
    wait_for(lambda: band.commands() == ["BUZZ:LIGHTNING"], what="BUZZ:LIGHTNING")
    # A replay worker has no physical band: it must never get keep-alives.
    app.on_loop(lambda: app.hub.register_worker("replay-x", source="replay"))

    sent_at = app.hub.last_sent["band-7"]
    app.band.sent.clear()
    app.on_loop(app.hub.keep_alive, sent_at + 5)                      # too soon: nothing
    time.sleep(0.2)
    assert band.commands() == ["BUZZ:LIGHTNING"]
    app.on_loop(app.hub.keep_alive, sent_at + config.BAND_RESEND_S)   # 20 s later: same command again
    wait_for(lambda: band.commands() == ["BUZZ:LIGHTNING", "BUZZ:LIGHTNING"], what="keep-alive re-send")
    assert ("replay-x", "BUZZ:OFF") not in app.band.sent
    assert app.band.sent == [("band-7", "BUZZ:LIGHTNING")]


def test_A06_btn_sos_escalates_at_once_and_never_buzzes_the_band(udp_app):
    app, band = udp_app
    join(app, band, "TEMP:34.0", "HR:130")
    band.send("BTN:SOS")
    sos = wait_for(lambda: app.of_type("sos", "band-7"), what="sos event")[0]
    assert sos["source"] == "band" and sos["hr_bpm"] == 130.0
    # Session D started right away: no stillness wait.
    steps = wait_for(lambda: app.of_type("emergency_status", "band-7"), what="escalation")
    assert (steps[0]["stage"], steps[0]["status"]) == ("notify", "calling")
    assert "SOS" in steps[0]["message"]
    wait_for(lambda: any(s["stage"] == "cancel_window" for s in app.of_type("emergency_status", "band-7")),
             what="cancel window")
    # The crew lead presses "cancel, false alarm" in the window.
    assert app.client.post("/api/emergency/cancel", json={"worker_id": "band-7"}).json() == {"cancelled": True}
    last = app.of_type("emergency_status", "band-7")[-1]
    assert (last["stage"], last["status"]) == ("done", "cancelled")
    assert not [n for n in app.phone.asked if n == app.emergency.contacts["emergency"]], \
        "a cancelled SOS must never reach the emergency leg"
    time.sleep(0.2)
    assert not any("SOS" in c for c in band.commands())      # SOS is never a band command
    raw = [e for e in app.events if e["type"] == "button" and e["worker_id"] == "band-7"]
    assert raw[-1]["kind"] == "sos"                           # the raw input is a "button" with kind sos


# ---------------------------------------------------------------------------
# A USB serial band (a pty pair stands in for the cable)
# ---------------------------------------------------------------------------
@pytest.fixture
def serial_app(live_app, monkeypatch):
    master, slave = os.openpty()
    tty.setraw(slave)                                   # no echo: behave like a USB serial cable
    monkeypatch.setattr(config, "WORKER_ID", "usb-band")
    app = live_app("--source", "live", "--udp-port", str(free_udp_port()), "--serial", os.ttyname(slave),
                   "--no-pulsoid")
    yield app, master
    os.close(master)
    os.close(slave)


def serial_read(master, timeout=0.3) -> list[str]:
    out, buf, end = [], b"", time.monotonic() + timeout
    while time.monotonic() < end:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            buf += os.read(master, 1024)
    out += [ln.strip() for ln in buf.decode(errors="replace").splitlines() if ln.strip()]
    return out


def test_A02_A03_A06_serial_link_end_to_end(serial_app):
    app, master = serial_app

    def write(*lines):
        os.write(master, ("\n".join(lines) + "\n").encode("utf-8", errors="replace"))

    before = time.time()
    write("# beacon v1 booting", "TEMP:34.62", "TILT:0,ACT:5", "HR:118", *GARBAGE, "BTN:1")
    wait_for(lambda: app.of_type("button", "usb-band"), timeout=8, what="button over serial")
    r = readings(app, "usb-band")
    assert [x.get("skin_temp") for x in r if x.get("skin_temp")] == [34.62]
    assert [(x["tilt"], x["activity"]) for x in r if x.get("tilt") is not None] == [(0, 5.0)]
    assert [x["hr"] for x in r if x.get("hr")] == [118.0]
    assert len(r) == 3, f"garbage became readings: {r}"
    assert all(x["timestamp"] >= before for x in r)
    assert app.hub.workers["usb-band"]["source"] == "serial"
    wait_for(lambda: "BUZZ:OFF" in serial_read(master, 0.1), timeout=3, what="first keep-alive")

    # Commands come back down the cable at the right moment.
    app.client.post("/api/demo/hazard", json={"kind": "flood", "on": True})
    assert "BUZZ:FLOOD_FIRE" in serial_read(master)
    app.on_loop(app.hub.keep_alive, app.hub.last_sent["usb-band"] + config.BAND_RESEND_S)
    assert serial_read(master) == ["BUZZ:FLOOD_FIRE"]               # keep-alive over USB too
    app.client.post("/api/demo/hazard", json={"kind": "flood", "on": False})
    assert serial_read(master) == ["BUZZ:OFF"]

    # BTN:SOS over the cable escalates at once.
    write("BTN:SOS")
    wait_for(lambda: app.of_type("emergency_status", "usb-band"), timeout=8, what="SOS escalation")
    assert app.of_type("sos", "usb-band")[0]["source"] == "band"
