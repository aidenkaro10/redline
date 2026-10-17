"""
Heart rate from the Apple Watch, through Pulsoid's real-time WebSocket.

    wss://dev.pulsoid.net/api/v1/data/real_time?access_token=<PULSOID_TOKEN>

Each message looks like:  {"measured_at": 1700000000000, "data": {"heart_rate": 82}}
Messages arrive about every 5 s while a workout runs on the watch.

Per the protocol table, heart rate ONLY comes from here (never from the band).
We stamp each reading with the laptop's clock on arrival, like every other
input, so all signals line up. If the connection drops we reconnect with a
growing pause (1 s, 2 s, 4 s ... up to 30 s).
"""

import asyncio
import json
import logging
import time

import websockets

from backend import config
from backend.app.schemas import Reading

log = logging.getLogger(__name__)


def parse_message(text: str, worker_id: str, timestamp: float) -> Reading | None:
    """One Pulsoid message -> a Reading with only hr set. None if it isn't a heart rate."""
    try:
        msg = json.loads(text)
        hr = float(msg["data"]["heart_rate"])
    except (ValueError, KeyError, TypeError):
        return None
    if not 20 <= hr <= 250:                  # obviously broken values
        return None
    return Reading(worker_id=worker_id, timestamp=timestamp, hr=hr)


async def run(hub, token: str, worker_id: str, url: str = config.PULSOID_WS_URL, clock=time.time) -> None:
    """Stream heart rate into the hub forever. Cancel the task to stop."""
    pause = 1.0
    while True:
        try:
            async with websockets.connect(f"{url}?access_token={token}") as ws:
                log.info("Pulsoid connected")
                pause = 1.0
                async for text in ws:
                    reading = parse_message(text, worker_id, clock())
                    if reading is not None:
                        hub.handle_input(reading, source="pulsoid")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Pulsoid connection problem (%s); retrying in %.0f s", e, pause)
        await asyncio.sleep(pause)
        pause = min(pause * 2, 30.0)
