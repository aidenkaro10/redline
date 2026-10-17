"""
The hub: the one place every piece meets.

    sources (replay, serial, UDP, Pulsoid) ──handle_input()──> hub ──> WorkerPipeline (one per worker)
                                                                │
         events <───────────────────────────────────────────────┘
           ├─> band outputs (console / serial / UDP)    for "band_command"
           ├─> dashboard WebSocket subscribers          every event
           ├─> SQLite                                   readings, alerts, estimates
           └─> in-process listeners                     e.g. session D's emergency escalation

Also holds the demo controls (demo threshold, test hazard, fake fall,
replay on/off/speed) and the site hazards that session C1 pushes in.

Everything here is synchronous and fast. The async parts (WebSocket, replay,
input readers) live in main.py and the source modules, and call into here.
"""

import asyncio
import logging
from collections import deque
from typing import Callable

from backend import config
from backend.alerts.band_output import ConsoleBand, MultiBand
from backend.app.pipeline import WorkerPipeline
from backend.app.schemas import ButtonPress, Reading
from backend.replay.clock import RealClock

log = logging.getLogger(__name__)

DEFAULT_SITE = {
    "site_id": "site-gnv",
    "name": "Gainesville demo site",
    "lat": config.JOB_SITE_LAT,
    "lon": config.JOB_SITE_LON,
}

# Hazard kinds (from session C1 or the demo button) -> band pattern.
HAZARD_PATTERNS = {
    "lightning": "LIGHTNING",
    "flood": "FLOOD_FIRE",
    "fire": "FLOOD_FIRE",
    "red_flag": "FLOOD_FIRE",
    "fire_weather": "FLOOD_FIRE",
}

RECENT_KEEP = 300            # recent alert/command events kept for new dashboard connections
QUEUE_MAX = 2000             # per dashboard connection; a stuck browser drops events instead of eating memory
QUIET_EVENTS = {"reading", "estimate"}    # too many to keep in "recent"


class Hub:
    def __init__(self, predictor=None, band: MultiBand | None = None, db=None, recorder=None,
                 demo: bool = False, site: dict | None = None):
        self.predictor = predictor
        self.band = band or MultiBand([ConsoleBand()])
        self.db = db
        self.recorder = recorder
        self.demo = demo
        self.sites: dict[str, dict] = {}
        self.default_site_id = self.add_site(site or DEFAULT_SITE)
        self.workers: dict[str, dict] = {}
        self.pipelines: dict[str, WorkerPipeline] = {}
        self.clocks: dict[str, object] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self.listeners: list[Callable[[dict], None]] = []
        self.recent: deque = deque(maxlen=RECENT_KEEP)
        self.hazards: dict[str, dict[str, dict]] = {}     # site_id -> kind -> latest hazard event
        self.plans: list[dict] = []
        self.replay = None              # the running Replay, if any
        self.replay_task: asyncio.Task | None = None
        self.real_clock = RealClock()

    # ------------------------------------------------------------------
    # Workers and sites
    # ------------------------------------------------------------------
    def add_site(self, site: dict) -> str:
        self.sites[site["site_id"]] = dict(site)
        if self.db:
            self.db.upsert_site(site)
        return site["site_id"]

    def register_worker(self, worker_id: str, name: str | None = None, site_id: str | None = None,
                        source: str = "live", clock=None, meta: dict | None = None) -> WorkerPipeline:
        """Create (or reset) a worker's pipeline."""
        site_id = site_id or self.default_site_id
        info = {"worker_id": worker_id, "name": name or worker_id, "site_id": site_id,
                "source": source, "meta": meta or {}}
        self.workers[worker_id] = info
        self.clocks[worker_id] = clock or self.real_clock
        p = WorkerPipeline(worker_id, predictor=self.predictor, demo=self.demo,
                           info=info, site=self.sites.get(site_id, {}))
        self.pipelines[worker_id] = p
        if self.db:
            self.db.upsert_worker(info)
        t = self.clocks[worker_id].now()
        events = [{"type": "worker", "timestamp": t, **self.public_info(worker_id)}]
        # Any hazard already active at the site applies to the new worker too.
        for kind in self.hazards.get(site_id, {}):
            events += p.set_hazard(HAZARD_PATTERNS[kind], True, t)
        self.dispatch(events)
        return p

    def remove_worker(self, worker_id: str) -> None:
        for d in (self.workers, self.pipelines, self.clocks):
            d.pop(worker_id, None)
        self.dispatch([{"type": "worker_removed", "timestamp": self.real_clock.now(), "worker_id": worker_id}])

    def public_info(self, worker_id: str) -> dict:
        w = self.workers[worker_id]
        return {"worker_id": worker_id, "name": w["name"], "site_id": w["site_id"], "source": w["source"]}

    def now(self, worker_id: str) -> float:
        return self.clocks.get(worker_id, self.real_clock).now()

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def handle_input(self, event: Reading | ButtonPress, source: str = "live") -> None:
        """A reading or button press from any source. Unknown workers are created on the fly."""
        if event.worker_id not in self.pipelines:
            self.register_worker(event.worker_id, source=source)
        if self.recorder and source != "replay":
            self.recorder.write(event)
        if self.db and isinstance(event, Reading):
            self.db.add_reading(event)
        out = [event.model_dump()]
        out += self.pipelines[event.worker_id].handle(event)
        self.dispatch(out)

    def set_context(self, worker_id: str, wbgt: float | None = None, workload: float | None = None) -> None:
        """Per-worker weather / planned workload (replay, planner)."""
        if worker_id in self.pipelines:
            self.pipelines[worker_id].set_context(wbgt=wbgt, workload=workload)

    def set_site_weather(self, site_id: str, wbgt: float) -> None:
        """Session C1: current WBGT at a site. Feeds every worker there (a forecaster input)."""
        for wid, w in self.workers.items():
            if w["site_id"] == site_id:
                self.pipelines[wid].set_context(wbgt=wbgt)

    def tick(self) -> None:
        """About once a second: close quiet minutes, check man-down and red-alert timers."""
        for wid, p in list(self.pipelines.items()):
            self.dispatch(p.tick(self.clocks[wid].now()))

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------
    def dispatch(self, events: list[dict]) -> None:
        for ev in events:
            kind = ev["type"]
            if kind == "estimate":
                self._add_truth(ev)
            elif kind == "band_command":
                self.band.send(ev["worker_id"], ev["line"])
                if self.replay is not None:
                    self.replay.on_band_command(ev["worker_id"], ev["pattern"], ev["timestamp"])
            elif kind == "mandown_confirmed":
                # Nearby crewmates = everyone else at the same site. They see it on the dashboard.
                site = ev.get("site_id")
                ev["crewmates"] = [w for w, info in self.workers.items()
                                   if info["site_id"] == site and w != ev["worker_id"]]
            if self.db:
                self.db.log_event(ev)
            if kind not in QUIET_EVENTS:
                self.recent.append(ev)
            for listener in self.listeners:
                try:
                    listener(ev)
                except Exception as e:
                    log.warning("listener failed on %s: %s", kind, e)
            self.broadcast(ev)

    def _add_truth(self, ev: dict) -> None:
        """Replay of simulated shifts: attach the simulator's true core temp (for the chart only)."""
        truth = self.workers.get(ev["worker_id"], {}).get("meta", {}).get("true_core_temp")
        if truth and 0 <= ev["minute"] < len(truth):
            ev["true_core_temp"] = truth[ev["minute"]]

    def broadcast(self, ev: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=QUEUE_MAX)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        """In-process consumer of every event (session D: watch for "mandown_confirmed")."""
        self.listeners.append(fn)

    # ------------------------------------------------------------------
    # Demo controls and plug-ins
    # ------------------------------------------------------------------
    def set_demo(self, on: bool) -> None:
        self.demo = on
        events = [{"type": "demo", "timestamp": self.real_clock.now(), "demo": on,
                   "warning_c": config.DEMO_HEAT_WARNING_C if on else config.HEAT_WARNING_C,
                   "danger_c": config.DEMO_HEAT_DANGER_C if on else config.HEAT_DANGER_C}]
        for p in self.pipelines.values():
            events += p.set_demo(on)
        self.dispatch(events)

    def set_site_hazard(self, site_id: str | None, kind: str, on: bool, source: str = "demo",
                        details: dict | None = None) -> dict:
        """
        Turn a site hazard on/off (session C1's feeds, or the demo button).
        kind: lightning | flood | fire | red_flag | fire_weather. Bands at that site switch pattern.
        """
        if kind not in HAZARD_PATTERNS:
            raise ValueError(f"Unknown hazard kind: {kind}. Use one of {sorted(HAZARD_PATTERNS)}")
        site_id = site_id or self.default_site_id
        active = self.hazards.setdefault(site_id, {})
        ev = {"type": "hazard", "timestamp": self.real_clock.now(), "site_id": site_id, "kind": kind,
              "on": on, "pattern": HAZARD_PATTERNS[kind], "source": source, "details": details or {}}
        if on:
            active[kind] = ev
        else:
            active.pop(kind, None)
        pattern = HAZARD_PATTERNS[kind]
        pattern_on = any(HAZARD_PATTERNS[k] == pattern for k in active)
        events = [ev]
        for wid, w in self.workers.items():
            if w["site_id"] == site_id:
                events += self.pipelines[wid].set_hazard(pattern, pattern_on, self.now(wid))
        self.dispatch(events)
        return ev

    def press_button(self, worker_id: str) -> None:
        """Demo: act as if the worker pressed the band button."""
        self.handle_input(ButtonPress(worker_id=worker_id, timestamp=self.now(worker_id)), source="demo")

    def fake_fall(self, worker_id: str, duration_s: float = 180.0) -> None:
        """
        Demo: make a worker fall. Replay workers really tip over in the replayed
        data (so the tilt rule fires); live workers get a man-down check right away.
        """
        if self.replay is not None and worker_id in self.replay.workers:
            self.replay.inject_fall(worker_id, duration_s)
        else:
            self.dispatch(self.pipelines[worker_id].trigger_mandown(self.now(worker_id), "demo_fall"))

    def resolve_mandown(self, worker_id: str, by: str = "crew_lead") -> None:
        self.dispatch(self.pipelines[worker_id].resolve_mandown(self.now(worker_id), by))

    def add_plan(self, plan: dict, source: str = "planner") -> dict:
        """Session B: a proposed/approved schedule. Stored and broadcast as-is."""
        ev = {"type": "plan", "timestamp": self.real_clock.now(), "source": source, "plan": plan}
        self.plans.append(ev)
        if self.db:
            self.db.add_plan(ev)
        self.dispatch([ev])
        return ev

    # ------------------------------------------------------------------
    # Replay control
    # ------------------------------------------------------------------
    def start_replay(self, replay) -> None:
        """Start a Replay (backend.replay.replay.Replay). Must be called from the event loop."""
        self.stop_replay()
        self.replay = replay
        replay.register()
        self.replay_task = asyncio.get_running_loop().create_task(replay.run())
        self._replay_state()

    def stop_replay(self) -> None:
        if self.replay is None:
            return
        if self.replay_task is not None:
            self.replay_task.cancel()
        for wid in list(self.replay.workers):
            self.remove_worker(wid)
        self.replay, self.replay_task = None, None
        self._replay_state()

    def set_speed(self, speed: float) -> None:
        if self.replay is not None:
            self.replay.set_speed(speed)
            self._replay_state()

    def _replay_state(self) -> None:
        state = self.replay.state() if self.replay is not None else {"on": False}
        self.dispatch([{"type": "replay", "timestamp": self.real_clock.now(), **state}])

    # ------------------------------------------------------------------
    # Snapshot for new dashboard connections and GET /api/state
    # ------------------------------------------------------------------
    def snapshot(self, history: int = 240) -> dict:
        return {
            "type": "snapshot",
            "timestamp": self.real_clock.now(),
            "demo": self.demo,
            "thresholds": {
                "warning_c": config.DEMO_HEAT_WARNING_C if self.demo else config.HEAT_WARNING_C,
                "danger_c": config.DEMO_HEAT_DANGER_C if self.demo else config.HEAT_DANGER_C,
            },
            "replay": self.replay.state() if self.replay is not None else {"on": False},
            "sites": list(self.sites.values()),
            "hazards": [ev for kinds in self.hazards.values() for ev in kinds.values()],
            "workers": [
                {**self.public_info(wid), **p.status(),
                 # older points without their forecast curve, to keep the snapshot small
                 "history": [{k: v for k, v in e.items() if k != "forecast"} for e in list(p.estimates)[-history:]]}
                for wid, p in self.pipelines.items()
            ],
            "recent": list(self.recent)[-100:],
            "plans": self.plans[-5:],
        }
