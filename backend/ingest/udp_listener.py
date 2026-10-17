"""
WiFi input: the band sends its text lines as UDP packets to the laptop on
port 5005 (UDP_PORT in .env). One packet can hold one or more lines.

Every line is stamped with the laptop's clock when it arrives, parsed with
the shared line parser, and handed to the hub. We remember the band's
address so BUZZ commands go straight back to it (UDPBand).

One band per laptop for now (WORKER_ID in .env). A band can also say which
worker it is with a line like "ID:worker-2" (sent once at boot, or before
its other lines in the same packet).
"""

import asyncio
import logging
import time

from backend.alerts.band_output import UDPBand
from backend.app.schemas import events_from_line

log = logging.getLogger(__name__)


class BandProtocol(asyncio.DatagramProtocol):
    def __init__(self, hub, band: UDPBand, worker_id: str, clock=time.time):
        self.hub = hub
        self.band = band
        self.default_worker = worker_id
        self.workers_by_addr: dict[tuple, str] = {}     # band address -> worker id
        self.clock = clock

    def connection_made(self, transport):
        self.transport = transport
        self.band.transport = transport                       # reply from port 5005, so NAT/hotspots pass it back

    def datagram_received(self, data: bytes, addr) -> None:
        t = self.clock()                                      # laptop timestamp on arrival
        text = data.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("ID:"):
                self.workers_by_addr[addr] = line[3:].strip()
                continue
            worker = self.workers_by_addr.get(addr, self.default_worker)
            self.band.remember(worker, addr)
            for event in events_from_line(line, worker, t):
                self.hub.handle_input(event, source="udp")


async def start(hub, band: UDPBand, port: int, worker_id: str, host: str = "0.0.0.0"):
    """Start listening. Returns (transport, protocol); close the transport to stop."""
    loop = asyncio.get_running_loop()
    return await loop.create_datagram_endpoint(lambda: BandProtocol(hub, band, worker_id), local_addr=(host, port))
