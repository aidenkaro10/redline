"""
USB input: read the band's lines over serial at 115200 baud (SERIAL_BAUD).

pyserial blocks while it waits for a line, so reading happens on a small
background thread. Each line is stamped with the laptop clock right when it
arrives, then handed to the hub on the server's event loop (the hub is not
thread-safe, so it's only ever touched from the loop).

Find the port on a Mac with:  ls /dev/cu.*   (put it in .env as SERIAL_PORT)
"""

import asyncio
import logging
import threading
import time

import serial

from backend import config
from backend.alerts.band_output import SerialBand
from backend.app.schemas import events_from_line

log = logging.getLogger(__name__)


def read_lines(port, on_line, stop: threading.Event, clock=time.time) -> None:
    """Read lines until `stop` is set. on_line(text, laptop_timestamp) for each one."""
    while not stop.is_set():
        try:
            raw = port.readline()                 # returns b"" after the timeout if nothing came
        except serial.SerialException as e:
            log.warning("serial read failed (%s); retrying", e)
            time.sleep(1)
            continue
        if raw:
            on_line(raw.decode("utf-8", errors="replace"), clock())


def start(hub, port_name: str, worker_id: str, loop: asyncio.AbstractEventLoop | None = None,
          port=None) -> SerialBand:
    """
    Open the port and start the reader thread. Returns a SerialBand that writes
    BUZZ commands back down the same cable. `port` can be passed in for tests.
    """
    loop = loop or asyncio.get_running_loop()
    port = port or serial.Serial(port_name, config.SERIAL_BAUD, timeout=0.5)
    stop = threading.Event()

    def on_line(text: str, t: float) -> None:
        for event in events_from_line(text, worker_id, t):
            loop.call_soon_threadsafe(hub.handle_input, event, "serial")

    thread = threading.Thread(target=read_lines, args=(port, on_line, stop), daemon=True, name="serial-reader")
    thread.start()
    band = SerialBand(port, worker_id)
    band.close = lambda: (stop.set(), port.close())       # stop the thread and free the port on shutdown
    return band
