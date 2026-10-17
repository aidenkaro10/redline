"""
Where band commands go. Every output has the same one method:

    output.send(worker_id, "BUZZ:HEAT_RED")

so the rest of the system doesn't care if the band is on USB, on WiFi, or
not plugged in at all (console printer).

- ConsoleBand: prints the command. Always on, so the demo shows it.
- SerialBand:  writes the line to the band's USB serial port.
- UDPBand:     sends the line back to the band's WiFi address (learned
               from the band's own UDP packets).
- MultiBand:   sends to several outputs. One broken output never stops the others.
"""

import logging
import socket
import sys
import time

log = logging.getLogger(__name__)


class BandOutput:
    """Base class: send one command line to one worker's band."""

    def send(self, worker_id: str, line: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ConsoleBand(BandOutput):
    """Prints commands, e.g.  [band worker-1] BUZZ:HEAT_RED"""

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def send(self, worker_id: str, line: str) -> None:
        clock = time.strftime("%H:%M:%S")
        print(f"{clock} [band {worker_id}] {line}", file=self.stream, flush=True)


class SerialBand(BandOutput):
    """
    Writes commands to a band on USB. `port` is an open pyserial Serial
    (shared with the serial reader) or anything with .write(bytes).
    Only sends to the one worker that band belongs to.
    """

    def __init__(self, port, worker_id: str):
        self.port = port
        self.worker_id = worker_id

    def send(self, worker_id: str, line: str) -> None:
        if worker_id == self.worker_id:
            self.port.write((line + "\n").encode())


class UDPBand(BandOutput):
    """
    Sends commands back over WiFi. The UDP listener calls remember() every
    time a band sends us a packet, so we always reply to its latest address.
    """

    def __init__(self, sock: socket.socket | None = None):
        self.sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # The UDP listener sets this, so replies leave from port 5005 (phone hotspots pass those back).
        self.transport = None
        self.addresses: dict[str, tuple[str, int]] = {}

    def remember(self, worker_id: str, address: tuple[str, int]) -> None:
        self.addresses[worker_id] = address

    def send(self, worker_id: str, line: str) -> None:
        address = self.addresses.get(worker_id)
        if address is not None:
            (self.transport or self.sock).sendto((line + "\n").encode(), address)


class MultiBand(BandOutput):
    """Fans one command out to several outputs."""

    def __init__(self, outputs: list[BandOutput] | None = None):
        self.outputs = list(outputs or [])

    def add(self, output: BandOutput) -> None:
        self.outputs.append(output)

    def remove(self, output: BandOutput) -> None:
        if output in self.outputs:
            self.outputs.remove(output)

    def send(self, worker_id: str, line: str) -> None:
        for out in self.outputs:
            try:
                out.send(worker_id, line)
            except Exception as e:          # a dead cable must never stop the alert reaching others
                log.warning("band output %s failed: %s", type(out).__name__, e)

    def close(self) -> None:
        for out in self.outputs:
            out.close()
