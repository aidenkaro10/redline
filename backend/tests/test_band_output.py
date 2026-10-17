"""Band command outputs: console, serial, UDP, fan-out."""

import io
import socket

from backend.alerts.band_output import BandOutput, ConsoleBand, MultiBand, SerialBand, UDPBand


def test_console_prints_command():
    buf = io.StringIO()
    ConsoleBand(buf).send("worker-1", "BUZZ:HEAT_RED")
    assert "[band worker-1] BUZZ:HEAT_RED" in buf.getvalue()


def test_serial_only_its_worker():
    port = io.BytesIO()
    band = SerialBand(port, "worker-1")
    band.send("worker-2", "BUZZ:OFF")
    band.send("worker-1", "BUZZ:HEAT_YELLOW")
    assert port.getvalue() == b"BUZZ:HEAT_YELLOW\n"


def test_udp_replies_to_learned_address():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2)
    band = UDPBand()
    band.send("worker-1", "BUZZ:OFF")               # unknown address: silently skipped
    band.remember("worker-1", rx.getsockname())
    band.send("worker-1", "BUZZ:MANDOWN")
    assert rx.recv(100) == b"BUZZ:MANDOWN\n"
    rx.close()


def test_multiband_survives_broken_output():
    class Broken(BandOutput):
        def send(self, worker_id, line):
            raise OSError("cable unplugged")

    buf = io.StringIO()
    MultiBand([Broken(), ConsoleBand(buf)]).send("w", "BUZZ:HEAT_RED")
    assert "BUZZ:HEAT_RED" in buf.getvalue()


def test_keep_alive_resends_status_to_real_bands_only():
    from backend import config
    from backend.app.hub import Hub
    from backend.app.schemas import Reading

    class Fake(BandOutput):
        def __init__(self):
            self.lines = []

        def send(self, worker_id, line):
            self.lines.append((worker_id, line))

    import time
    buf, real = io.StringIO(), Fake()
    hub = Hub(band=MultiBand([ConsoleBand(buf), real]))
    hub.handle_input(Reading(worker_id="w1", timestamp=1.0, hr=90))
    hub.register_worker("r1", source="replay")                  # no physical band behind replay workers
    t0 = time.monotonic()
    hub.keep_alive(now=t0)                                      # first one goes out right away
    assert real.lines == [("w1", "BUZZ:OFF")] and buf.getvalue() == ""
    hub.keep_alive(now=t0 + config.BAND_RESEND_S - 1)           # too soon
    assert len(real.lines) == 1
    hub.set_site_hazard(None, "lightning", True)                # a real change goes out (and resets the timer)
    real.lines.clear()
    hub.keep_alive(now=time.monotonic() + config.BAND_RESEND_S + 1)
    assert real.lines == [("w1", "BUZZ:LIGHTNING")]
