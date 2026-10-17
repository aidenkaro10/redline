"""Inputs: UDP listener, serial reader, Pulsoid parsing, fake band."""

import asyncio
import io
import socket
import threading
import time

from backend.alerts.band_output import MultiBand, UDPBand
from backend.app.hub import Hub
from backend.ingest import fake_band, pulsoid_client, serial_reader, udp_listener


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_pulsoid_parse():
    r = pulsoid_client.parse_message('{"measured_at": 1700000000000, "data": {"heart_rate": 82}}', "w1", 5.0)
    assert r.hr == 82 and r.timestamp == 5.0 and r.worker_id == "w1"   # laptop time, not measured_at
    assert pulsoid_client.parse_message('{"data": {}}', "w1", 5.0) is None
    assert pulsoid_client.parse_message("not json", "w1", 5.0) is None
    assert pulsoid_client.parse_message('{"data": {"heart_rate": 0}}', "w1", 5.0) is None


def test_udp_listener_parses_and_replies_buzz():
    port = free_port()
    udp = UDPBand()
    hub = Hub(band=MultiBand([udp]), demo=True)
    got = []
    hub.add_listener(got.append)

    async def go():
        transport, _ = await udp_listener.start(hub, udp, port=port, worker_id="w1", host="127.0.0.1")
        band = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        band.bind(("127.0.0.1", 0))
        band.setblocking(False)
        band.sendto(b"TEMP:34.6\nTILT:0,ACT:5\nHR:150\n", ("127.0.0.1", port))
        await asyncio.sleep(0.1)
        hub.press_button("w1")                      # nothing to ack, just checks the path works
        hub.fake_fall("w1")                         # man-down check -> BUZZ:MANDOWN to the band's address
        await asyncio.sleep(0.1)
        data = band.recv(100)
        band.close()
        transport.close()
        return data

    data = asyncio.run(go())
    assert data == b"BUZZ:MANDOWN\n"
    readings = [e for e in got if e["type"] == "reading"]
    assert [r.get("skin_temp") for r in readings][:1] == [34.6]
    assert any(r.get("hr") == 150 for r in readings)


def test_serial_reader_thread_and_commands():
    class FakePort:
        def __init__(self):
            self.lines = [b"TEMP:34.1\n", b"BTN:1\n", b"junk\x00\n"]
            self.written = io.BytesIO()

        def readline(self):
            if self.lines:
                return self.lines.pop(0)
            time.sleep(0.01)
            return b""

        def write(self, b):
            self.written.write(b)

        def close(self):
            pass

    port = FakePort()
    hub = Hub(band=MultiBand([]))
    got = []
    hub.add_listener(got.append)

    async def go():
        band = serial_reader.start(hub, "fake", worker_id="w1", port=port)
        await asyncio.sleep(0.2)
        band.send("w1", "BUZZ:OFF")
        band.close()

    asyncio.run(go())
    kinds = [e["type"] for e in got]
    assert "reading" in kinds and "button" in kinds
    assert port.written.getvalue() == b"BUZZ:OFF\n"


def test_fake_band_lines_follow_protocol():
    from backend.app.schemas import parse_line
    band = fake_band.FakeBand(send_hr=True, fall_at=10, fall_for=5, ack_after=2, sos_at=30)
    first = band.lines_at(0)
    assert first[0].startswith("#") and parse_line(first[0]) is None      # debug line, ignored
    assert all(parse_line(l) is not None for l in first[1:])
    assert not any(l.startswith("TEMP") for l in band.lines_at(1))       # TEMP only every 5 s
    assert any(l.startswith("TEMP") for l in band.lines_at(5))
    assert "TILT:1,ACT:0" in band.lines_at(12)
    band.on_command("BUZZ:HEAT_RED", 20)
    assert "BTN:1" in band.lines_at(22)
    assert "BTN:SOS" in band.lines_at(30) and "BTN:SOS" not in band.lines_at(31)


def test_fake_band_udp_end_to_end():
    """The real UDP fake band talking to the real listener; a fall produces BUZZ:MANDOWN back."""
    port = free_port()
    udp = UDPBand()
    hub = Hub(band=MultiBand([udp]))
    band = fake_band.FakeBand(fall_at=0.0, fall_for=60)

    async def go():
        transport, _ = await udp_listener.start(hub, udp, port=port, worker_id="w1", host="127.0.0.1")
        # The fake band runs on a thread; the hub's clock is real time, so shrink the man-down timers.
        import backend.alerts.mandown as md
        old = md.config.MANDOWN_STILL_S
        md.config.MANDOWN_STILL_S = 0.5
        try:
            th = threading.Thread(target=fake_band.run_udp, args=(band, "127.0.0.1", port, 0.1, 2.0))
            th.start()
            for _ in range(25):
                await asyncio.sleep(0.1)
                hub.tick()
            th.join()
        finally:
            md.config.MANDOWN_STILL_S = old
            transport.close()

    asyncio.run(go())
    assert "BUZZ:MANDOWN" in band.received
