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
