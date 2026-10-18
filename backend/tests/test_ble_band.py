"""The band over Bluetooth LE (backend/ingest/ble_band.py). Fake BLE link and fake helper: no Bluetooth needed."""

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend import config
from backend.alerts.band_output import MultiBand
from backend.app.hub import Hub
from backend.ingest import ble_band
from backend.ingest.ble_band import NUS_RX, NUS_TX, BleBandHelper, BleBandLink, LineAssembler, matches


# ---------------------------------------------------------------------------
# Reassembling lines
# ---------------------------------------------------------------------------
def test_split_and_merged_notifications_become_whole_lines():
    a = LineAssembler()
    assert a.feed(b"TEMP:34.") == []                                  # half a line waits
    assert a.feed(b"62\nACC:0.98,0.02,0.11\nBTN") == ["TEMP:34.62", "ACC:0.98,0.02,0.11"]
    assert a.feed(b":SOS\r\n\n\n") == ["BTN:SOS"]                      # \r\n and empty lines
    assert a.feed("# café".encode()[:-1]) == []                   # a UTF-8 character split in two
    assert a.feed("# café".encode()[-1:] + b"\n") == ["# café"]


def test_endless_junk_without_newline_is_thrown_away():
    a = LineAssembler(max_bytes=100)
    a.feed(b"x" * 150)
    assert a.buf == b"" and a.dropped == 1
    assert a.feed(b"x" * 50 + b"\nTEMP:34.0\n") == ["x" * 50, "TEMP:34.0"]
    a.reset()
    a.feed(b"TEMP:3")
    a.reset()                                                         # new connection: no gluing
    assert a.feed(b"4.5\n") == ["4.5"]


def test_device_matching():
    band = SimpleNamespace(name="Redline Band", address="AA:BB")
    adv = SimpleNamespace(local_name="Redline Band", service_uuids=[ble_band.NUS_SERVICE])
    other = SimpleNamespace(name="Living Room TV", address="11:22")
    other_adv = SimpleNamespace(local_name="Living Room TV", service_uuids=[])
    assert matches(band, adv, None) and not matches(other, other_adv, None)
    assert matches(other, other_adv, "11:22") and matches(band, adv, "redline")


# ---------------------------------------------------------------------------
# The link, with a fake BLE stack
# ---------------------------------------------------------------------------
class Sink:
    def __init__(self):
        self.lines, self.status = [], []

    def on_line(self, line, t):
        self.lines.append(line)

    def on_status(self, state, message, device, address):
        self.status.append((state, message))


class FakeScanner:
    def __init__(self, devices):
        self.devices, self.scans = devices, 0

    async def find_device_by_filter(self, f, timeout=10.0):
        self.scans += 1
        await asyncio.sleep(0)
        for d in self.devices:
            if f(d, SimpleNamespace(local_name=d.name, service_uuids=[ble_band.NUS_SERVICE])):
                return d
        return None


def fake_band_client(chunks, every_s=0.005, stay_s=0.3):
    """BleakClient-like: plays notification chunks on TX, records RX writes, then disconnects."""

    class FakeClient:
        instances = []

        def __init__(self, dev, disconnected_callback=None):
            self.on_drop, self.writes, self.task = disconnected_callback, [], None
            FakeClient.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            if self.task:
                self.task.cancel()

        async def start_notify(self, char, callback):
            assert char == NUS_TX

            async def play():
                for c in chunks:
                    await asyncio.sleep(every_s)
                    callback(char, bytearray(c))
                await asyncio.sleep(stay_s)
                self.on_drop(self)
            self.task = asyncio.create_task(play())

        async def write_gatt_char(self, char, data, response=True):
            self.writes.append((char, bytes(data), response))

    return FakeClient


CHUNKS = [b"TEMP:34.", b"62\nACC:0.98,0.02,0.11\nACC:0.97,", b"0.03,0.12\nBTN:1\n", b"# boot\nBTN:SOS\n"]
LINES = ["TEMP:34.62", "ACC:0.98,0.02,0.11", "ACC:0.97,0.03,0.12", "BTN:1", "# boot", "BTN:SOS"]


def test_link_streams_lines_and_sends_buzz_over_rx():
    sink = Sink()
    client = fake_band_client(CHUNKS)
    link = BleBandLink(sink, scanner=FakeScanner([SimpleNamespace(name="Redline Band", address="AA")]),
                       client_cls=client)

    async def go():
        link.write("BUZZ:HEAT_RED")                       # queued before connecting: sent once connected
        await asyncio.wait_for(link.run_once(), timeout=5)

    asyncio.run(go())
    assert sink.lines == LINES
    assert client.instances[0].writes == [(NUS_RX, b"BUZZ:HEAT_RED\n", False)]
    assert [s for s, _ in sink.status] == ["searching", "connected", "lost"]
    assert "connected over Bluetooth" in sink.status[1][1] and "reconnecting" in sink.status[2][1]


def test_link_reconnects_after_the_band_walks_away():
    sink = Sink()
    client = fake_band_client([b"TEMP:34.0\n"], stay_s=0.05)
    scanner = FakeScanner([SimpleNamespace(name="Redline Band", address="AA")])
    link = BleBandLink(sink, scanner=scanner, client_cls=client)

    async def go():
        task = asyncio.create_task(link.run())
        while len(sink.lines) < 2:
            await asyncio.sleep(0.02)
        task.cancel()

    asyncio.run(asyncio.wait_for(go(), timeout=10))
    assert len(client.instances) >= 2
    assert sink.lines[:2] == ["TEMP:34.0", "TEMP:34.0"]
    assert [s for s, _ in sink.status][:4] == ["searching", "connected", "lost", "searching"]


def test_no_band_around_keeps_searching_without_crashing():
    sink = Sink()
    scanner = FakeScanner([SimpleNamespace(name="Living Room TV", address="11")])
    link = BleBandLink(sink, scanner=scanner, client_cls=fake_band_client([]))

    async def go():
        task = asyncio.create_task(link.run())
        while scanner.scans < 2:
            await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(asyncio.wait_for(go(), timeout=10))
    assert sink.lines == [] and sink.status[0][0] == "searching"


# ---------------------------------------------------------------------------
# Server side: helper process -> hub, hub -> helper
# ---------------------------------------------------------------------------
FAKE_HELPER = """
import json, sys
out = sys.argv[1]
def say(**m):
    print(json.dumps(m), flush=True)
say(kind="link", state="connected", message="band connected over Bluetooth", device="Redline Band", address="AA")
print("stray text", flush=True)
for line in ["TEMP:34.62", "BTN:SOS", "PPG:1,2", "ACC:0.98,0.02,0.11"]:
    say(kind="line", t=0, line=line)          # t far off: the server uses its own clock
with open(out, "w") as f:
    for raw in sys.stdin:                     # commands from the server
        f.write(raw); f.flush()
        if "BUZZ:HEAT_RED" in raw:
            break
sys.exit(3)
"""


def test_helper_feeds_the_hub_and_buzz_goes_back_down(tmp_path):
    out = tmp_path / "rx.txt"
    script = tmp_path / "fake_helper.py"
    script.write_text(FAKE_HELPER)
    events = []
    helper = None

    hub = Hub(band=MultiBand([]))
    hub.add_listener(events.append)
    helper = BleBandHelper(hub, "w1", cmd=[sys.executable, str(script), str(out)], restart_s=60)
    hub.band.add(helper.output)

    async def go():
        task = asyncio.create_task(helper.run())
        while not any(e["type"] == "reading" for e in events):
            await asyncio.sleep(0.02)
        hub.band.send("w1", "BUZZ:HEAT_RED")              # e.g. a heat alert: must reach the band's RX
        hub.band.send("someone-else", "BUZZ:MANDOWN")     # not this band
        while not any(e["type"] == "band_link" and e["state"] == "lost" for e in events):
            await asyncio.sleep(0.02)
        task.cancel()

    asyncio.run(asyncio.wait_for(go(), timeout=20))
    links = [e for e in events if e["type"] == "band_link"]
    assert [l["state"] for l in links] == ["connected", "lost"]
    assert links[0]["device"] == "Redline Band" and links[0]["transport"] == "ble"
    assert "exit code 3" in links[1]["message"]
    readings = [e for e in events if e["type"] == "reading"]
    assert readings[0]["skin_temp"] == 34.62 and readings[0]["source"] == "band"
    assert abs(readings[0]["timestamp"] - hub.real_clock.now()) < 30      # laptop clock, not the bogus t=0
    sos = [e for e in events if e["type"] == "sos"]
    assert sos and sos[0]["source"] == "band"                             # BTN:SOS over Bluetooth = the band
    assert "w1" in hub.band_streams                                       # ACC went to the band stream
    sent = [json.loads(x) for x in out.read_text().splitlines()]
    assert sent == [{"write": "BUZZ:HEAT_RED"}]


def test_write_without_a_helper_is_dropped_quietly():
    helper = BleBandHelper(Hub(band=MultiBand([])), "w1", cmd=["true"])
    helper.output.send("w1", "BUZZ:OFF")                                  # no process yet: no crash


# ---------------------------------------------------------------------------
# Picking USB vs Bluetooth, and no Bluetooth in tests
# ---------------------------------------------------------------------------
def settings(*args):
    from backend.app import main
    return main.parse_args(["--source", "live", *args])


def test_band_link_choice(monkeypatch):
    from backend.app import main
    monkeypatch.setattr(config, "SERIAL_PORT", "/dev/cu.usbmodem1101")
    # Inside the test suite Bluetooth never starts: auto falls back to the USB port from .env.
    assert main.band_links(settings()) == (False, "/dev/cu.usbmodem1101")
    monkeypatch.setattr(ble_band, "safe_to_scan", lambda: True)
    monkeypatch.setattr(ble_band, "available", lambda: True)
    assert main.band_links(settings()) == (True, "")                              # auto: Bluetooth
    assert main.band_links(settings("--serial", "/dev/x")) == (False, "/dev/x")   # --serial given: USB
    assert main.band_links(settings("--band", "usb")) == (False, "/dev/cu.usbmodem1101")
    assert main.band_links(settings("--band", "ble", "--serial", "/dev/x")) == (True, "")
    monkeypatch.setattr(ble_band, "available", lambda: False)                     # no bleak: USB fallback
    assert main.band_links(settings("--band", "ble")) == (False, "/dev/cu.usbmodem1101")


def test_live_mode_starts_the_band_helper_and_registers_its_output(monkeypatch):
    import socket

    from fastapi.testclient import TestClient

    from backend.app import main

    started = []

    class Recorder:
        def __init__(self, hub, worker_id, wanted=None):
            started.append((worker_id, wanted))
            self.output = ble_band.BleBand(self)
            self.worker_id = worker_id

        async def run(self):
            await asyncio.sleep(3600)

    monkeypatch.setattr(ble_band, "BleBandHelper", Recorder)
    monkeypatch.setattr(main, "band_links", lambda s: (True, ""))
    monkeypatch.setattr(config, "PULSOID_TOKEN", "")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(main, "SETTINGS", main.parse_args(
        ["--source", "live", "--udp-port", str(port), "--band-device", "Redline", "--no-ble", "--db", "",
         "--no-forecaster", "--hazards", "off", "--no-planner", "--emergency-log", ""]))
    with TestClient(main.app):
        outputs = main.app.state.hub.band.outputs
    assert started == [(config.WORKER_ID, "Redline")]
    assert any(isinstance(o, ble_band.BleBand) for o in outputs)


def test_no_bleak_import_and_no_bluetooth_in_tests():
    assert ble_band.safe_to_scan() is False
    code = ("import sys; import backend.app.main, backend.ingest.ble_band, backend.ingest.ble_hr; "
            "print('bleak' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         env={**__import__("os").environ, "PLANIT_BLE": "off"})
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip().splitlines()[-1] == "False"


@pytest.mark.parametrize("raw", ["", "{", "[1,2]", '{"kind":"line"}', '{"kind":"line","line":5}',
                                 '{"kind":"link"}', '{"kind":"line","line":"PPG:x"}', "null"])
def test_helper_ignores_garbage_messages(raw):
    hub = Hub(band=MultiBand([]))
    BleBandHelper(hub, "w1", cmd=["true"]).handle_line(raw)               # never raises


FAKE_BLEAK_HELPER = """
import asyncio, sys, types
from types import SimpleNamespace
written = []

class Scanner:
    @staticmethod
    async def find_device_by_filter(f, timeout=10.0):
        d = SimpleNamespace(name="Redline Band", address="AA")
        return d if f(d, SimpleNamespace(local_name=d.name, service_uuids=[])) else None

class Client:
    def __init__(self, dev, disconnected_callback=None):
        self.drop = disconnected_callback
    async def __aenter__(self):
        return self
    async def __aexit__(self, *e):
        pass
    async def start_notify(self, char, cb):
        cb(char, bytearray(b"TEMP:34.5\\nACC:0.1,"))
        cb(char, bytearray(b"0.2,0.9\\n"))
    async def write_gatt_char(self, char, data, response=True):
        print('{"kind": "line", "line": "# rx ' + data.decode().strip() + '"}', flush=True)
        sys.stderr.write("wrote " + data.decode())
        sys.exit(0)

sys.modules["bleak"] = types.SimpleNamespace(BleakScanner=Scanner, BleakClient=Client)
from backend.ingest import ble_band
ble_band.main(["--json"])
"""


def test_helper_mode_end_to_end_with_a_fake_bleak():
    """The real `--json` helper: JSON lines out, {"write": ...} on stdin goes to RX. bleak is faked."""
    proc = subprocess.run([sys.executable, "-c", FAKE_BLEAK_HELPER], input='{"write": "BUZZ:OFF"}\n',
                          capture_output=True, text=True, timeout=60)
    msgs = [json.loads(x) for x in proc.stdout.splitlines()]
    assert [m["state"] for m in msgs if m["kind"] == "link"][:2] == ["searching", "connected"]
    assert [m["line"] for m in msgs if m["kind"] == "line"][:2] == ["TEMP:34.5", "ACC:0.1,0.2,0.9"]
    assert "wrote BUZZ:OFF\n" in proc.stderr
