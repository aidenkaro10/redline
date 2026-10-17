"""
Replay: plays a ReplayPlan (see shifts.py) into the hub as if it were live.

- Readings are stamped with a VirtualClock that runs `speed` times faster
  than real time. Change the speed any time with set_speed().
- Items before offset 0 ("pre-roll") are played instantly, so the demo can
  start just before the interesting part of a shift with full history.
- Simulated workers react to their band: if a worker has auto_ack_s set,
  they "press the button" that many seconds after their band goes HEAT_RED.
- inject_fall() makes a replay worker tip over and lie still from now on.

The sink is the hub. It needs: register_worker(), handle_input(), set_context().
"""

import asyncio
import heapq
import itertools
import logging

from backend.app.schemas import ButtonPress, Reading
from backend.replay.clock import VirtualClock
from backend.replay.shifts import READ_EVERY_S, ReplayPlan, ScheduledItem

log = logging.getLogger(__name__)

PREROLL_BATCH = 200       # yield to the server every this many pre-roll items


class Replay:
    def __init__(self, plan: ReplayPlan, sink, speed: float = 10.0, clock: VirtualClock | None = None):
        self.plan = plan
        self.sink = sink
        self.clock = clock or VirtualClock(speed)
        self.workers = {w.worker_id: w for w in plan.workers}
        self._seq = itertools.count()          # tie-breaker so the heap never compares dicts
        self.heap = [(it.offset_s, next(self._seq), it) for it in plan.items]
        heapq.heapify(self.heap)
        self.start: float | None = None
        self.prerolling = False                    # True while the instant pre-roll is playing
        self.finished = False
        self.fallen_until: dict[str, float] = {}   # worker -> offset (s) when an injected fall ends

    @property
    def speed(self) -> float:
        return self.clock.speed

    def set_speed(self, speed: float) -> None:
        self.clock.set_speed(speed)

    def offset_now(self) -> float:
        """Seconds of replay time since the start."""
        return self.clock.now() - self.start

    # -- reacting to the band ----------------------------------------------
    def on_band_command(self, worker_id: str, pattern: str, t: float) -> None:
        w = self.workers.get(worker_id)
        if w is not None and pattern == "HEAT_RED" and w.auto_ack_s is not None and self.start is not None:
            self.push(ScheduledItem(t - self.start + w.auto_ack_s, "button", worker_id))

    def inject_fall(self, worker_id: str, duration_s: float = 180.0) -> None:
        """Demo button: this worker tips over and stops moving, starting now."""
        self.fallen_until[worker_id] = self.offset_now() + duration_s

    def push(self, item: ScheduledItem) -> None:
        heapq.heappush(self.heap, (item.offset_s, next(self._seq), item))

    # -- playing -------------------------------------------------------------
    def register(self) -> None:
        self.start = self.clock.now()
        for w in self.plan.workers:
            self.sink.register_worker(w.worker_id, name=w.name, source="replay", clock=self.clock, meta=w.meta)

    def emit_due(self, now: float, limit: int | None = None) -> int:
        """Send every item whose time has come (at most `limit`). Returns how many were sent."""
        sent = 0
        while self.heap and self.start + self.heap[0][0] <= now and (limit is None or sent < limit):
            offset, _, item = heapq.heappop(self.heap)
            self._send(item, self.start + offset)
            sent += 1
        return sent

    def _send(self, item: ScheduledItem, t: float) -> None:
        if item.kind == "context":
            self.sink.set_context(item.worker_id, **item.data)
        elif item.kind == "button":
            self.sink.handle_input(ButtonPress(worker_id=item.worker_id, timestamp=t), source="replay")
        elif item.kind == "reading":
            data = dict(item.data)
            if self.fallen_until.get(item.worker_id, -1e18) > t - self.start:
                data.update(tilt=1, activity=0.0)          # injected fall: tipped and still
            self.sink.handle_input(Reading(worker_id=item.worker_id, timestamp=t, **data), source="replay")

    async def run(self) -> None:
        """Play the whole plan. Cancel the task to stop it."""
        if self.start is None:
            self.register()
        # Pre-roll: everything stamped before "now", in batches so the server stays responsive.
        self.prerolling = True
        while self.emit_due(self.start, limit=PREROLL_BATCH):
            await asyncio.sleep(0)
        self.prerolling = False
        if hasattr(self.sink, "preroll_done"):
            self.sink.preroll_done(list(self.workers))
        while self.heap:
            self.emit_due(self.clock.now())
            if not self.heap:
                break
            wait = (self.start + self.heap[0][0] - self.clock.now()) / self.clock.speed
            await asyncio.sleep(min(max(wait, 0.0), 0.1))   # short naps so speed changes apply fast
        self.finished = True
        log.info("replay finished: %s", self.plan.description)

    def state(self) -> dict:
        return {
            "on": not self.finished, "speed": self.speed, "description": self.plan.description,
            "offset_s": round(self.offset_now(), 1) if self.start is not None else 0.0,
            "workers": list(self.workers),
            "read_every_s": READ_EVERY_S,
        }
