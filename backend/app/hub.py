"""
The hub: the one place every piece meets.

    sources (replay, serial, UDP, Pulsoid) ──handle_input()──> hub ──> WorkerPipeline (one per worker)
    band PPG / ACC / air lines (BandSample) ──> BandStreams (backend/ingest/band_stream.py)
        ──> a few slow readings (heart rate, activity, tilt) back into handle_input()
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
import time
from collections import deque
from typing import Callable

from backend import config
from backend.alerts.band_output import ConsoleBand, MultiBand
from backend.app.demo_hr import DemoHeartRate
from backend.app.pipeline import WorkerPipeline
from backend.app.schemas import BandSample, ButtonPress, Reading
from backend.ingest.band_stream import BandStreams
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
        if self.db:
            self.schedule = self.db.load_kv("schedule")  # last schedule survives a restart (breaks on cards)
        self.workers: dict[str, dict] = {}
        self.pipelines: dict[str, WorkerPipeline] = {}
        self.clocks: dict[str, object] = {}
        self.subscribers: set[asyncio.Queue] = set()
        self.listeners: list[Callable[[dict], None]] = []
        self.recent: deque = deque(maxlen=RECENT_KEEP)
        self.hazards: dict[str, dict[str, dict]] = {}     # site_id -> kind -> latest hazard event
        self.site_status: dict[str, dict] = {}            # site_id -> full status pushed by session C1
        self.plans: list[dict] = []
        if not hasattr(self, "schedule"):
            self.schedule: dict | None = None           # the planner's current verified schedule (session B)
        self.replay = None              # the running Replay, if any
        self.replay_task: asyncio.Task | None = None
        self.real_clock = RealClock()
        self.last_sent: dict[str, float] = {}          # worker -> monotonic time of the last command to their band
        self.why = None                                 # WhyExplainer (backend/app/why.py), set by main.py
        self.whys: dict[str, dict] = {}                 # worker -> latest why-line result
        # The band's fast lines (PPG, ACC) and air sensor, per worker. See backend/ingest/band_stream.py.
        self.site_air: dict[str, tuple[float, float]] = {}   # site -> (air temp °C, RH %) from the weather feed
        self.band_streams = BandStreams(site_air_of=self.site_air_of)
        # Labeled demo heart rate input (backend/app/demo_hr.py). Off until POST /api/demo/heart_rate.
        self.demo_hr = DemoHeartRate()

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
        p.demo_hr_only = self.demo_hr.mode != "off" and self.demo_hr.worker_id == worker_id
        if self.db:
            self.db.upsert_worker(info)
            saved = self.db.load_offset(worker_id)          # personal offset survives restarts
            if saved:
                from backend.models.personal_offset import PersonalOffset
                p.offset = PersonalOffset.from_dict(saved)
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
        self.band_streams.drop(worker_id)
        if self.demo_hr.worker_id == worker_id and self.demo_hr.mode != "off":
            self.demo_hr.set_mode("off", worker_id, self.real_clock.now())   # its worker is gone
        self.dispatch([{"type": "worker_removed", "timestamp": self.real_clock.now(), "worker_id": worker_id}])

    def public_info(self, worker_id: str) -> dict:
        w = self.workers[worker_id]
        return {"worker_id": worker_id, "name": w["name"], "site_id": w["site_id"], "source": w["source"]}

    def now(self, worker_id: str) -> float:
        return self.clocks.get(worker_id, self.real_clock).now()

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------
    def handle_input(self, event: Reading | ButtonPress | BandSample, source: str = "live") -> None:
        """A reading or button press from any source. Unknown workers are created on the fly."""
        if event.worker_id not in self.pipelines:
            self.register_worker(event.worker_id, source=source)
        if isinstance(event, BandSample):
            self._band_sample(event, source)        # fast band lines: never stored or broadcast one by one
            return
        if isinstance(event, Reading) and event.worker_id in self.band_streams:
            event = self.band_streams.filter_reading(event)   # ACC streaming: it replaces the tilt switch
            if event is None:
                return
        if isinstance(event, Reading) and event.source is None:
            event.source = source                           # tag untagged readings with where they came in
        if self.recorder and source != "replay":
            self.recorder.write(event)
        if self.db and isinstance(event, Reading):
            self.db.add_reading(event)
        # Raw inputs go out too (the chart's live HR). An SOS goes out raw as a "button" with
        # kind "sos"; the pipeline adds the full "sos" event (who/where/vitals) that session D acts on.
        out = [event.model_dump()]
        derived = self.pipelines[event.worker_id].handle(event)
        for ev in derived:
            if ev["type"] == "sos":     # where the press came from: the band, the phone, a demo button
                ev["source"] = "band" if source in ("serial", "udp", "live", "replay") else source
        out += derived
        self.dispatch(out)

    def _band_sample(self, sample: BandSample, source: str) -> None:
        """PPG / ACC / air -> the band stream. Its few slow outputs come back in as normal inputs."""
        readings, events = self.band_streams.feed(sample)
        for r in readings:
            self.handle_input(r, source)
        for ev in events:
            if ev["type"] == "band_minute" and sample.worker_id in self.pipelines:
                # Local air -> this worker's WBGT input only (None = no sane reading: no correction).
                self.pipelines[sample.worker_id].set_wbgt_correction(ev["wbgt_correction_c"], ev["timestamp"])
        if events:
            self.dispatch(events)

    def site_air_of(self, worker_id: str) -> tuple[float, float] | None:
        """
        Air temp and humidity at a worker's site, to compare with the band's air sensor:
        from POST /api/sites/{id}/weather (temp_c, rh_pct), else from C1's heat hazard details.
        """
        site = self.workers.get(worker_id, {}).get("site_id")
        if site in self.site_air:
            return self.site_air[site]
        for h in (self.site_status.get(site) or {}).get("hazards") or []:
            d = h.get("details") if isinstance(h, dict) else None
            if isinstance(d, dict) and d.get("temp_c") is not None and d.get("rh_pct") is not None:
                return float(d["temp_c"]), float(d["rh_pct"])
        return None

    def set_context(self, worker_id: str, **context) -> None:
        """
        Per-worker weather / planned workload (replay, planner). Keys: wbgt, workload,
        future_wbgt, future_workload (lists for the next 30 minutes).
        """
        if worker_id in self.pipelines:
            self.pipelines[worker_id].set_context(**context)

    def set_site_weather(self, site_id: str, wbgt: float, future_wbgt: list | None = None,
                         temp_c: float | None = None, rh_pct: float | None = None) -> None:
        """
        Session C1: current WBGT at a site, plus optionally the forecast for the next
        30 minutes (one value per minute). Feeds every worker there (forecaster inputs).
        temp_c / rh_pct: the site's air, compared with each band's air sensor (local WBGT correction).
        """
        if temp_c is not None and rh_pct is not None:
            self.site_air[site_id] = (float(temp_c), float(rh_pct))
        for wid, w in self.workers.items():
            if w["site_id"] == site_id:
                self.pipelines[wid].set_context(wbgt=wbgt, future_wbgt=future_wbgt)

    def tick(self) -> None:
        """About once a second: close quiet minutes, check man-down and red-alert timers."""
        for wid, p in list(self.pipelines.items()):
            if self._prerolling(wid):
                continue        # its readings are still catching up to the clock
            self.dispatch(p.tick(self.clocks[wid].now()))
        self.demo_hr_tick()
        self.keep_alive()

    def demo_hr_tick(self) -> None:
        """While the demo heart rate input is on: one labeled reading per second (worker's clock)."""
        wid = self.demo_hr.worker_id
        if self.demo_hr.mode == "off" or wid not in self.pipelines:
            return
        t = self.now(wid)
        bpm = self.demo_hr.next(t)
        if bpm is not None:
            self.handle_input(Reading(worker_id=wid, timestamp=t, hr=bpm, source="demo_input"), source="demo_input")

    def set_demo_heart_rate(self, mode: str, worker_id: str | None = None) -> dict:
        """
        Demo button: "rest" / "active" / "off" for one worker (default WORKER_ID, the band's worker).
        While on, that worker's other heart rate sources are ignored. Returns the new state.
        """
        worker_id = worker_id or self.demo_hr.worker_id or config.WORKER_ID
        old_mode, old_worker = self.demo_hr.mode, self.demo_hr.worker_id
        if old_worker and old_worker != worker_id and old_worker in self.pipelines:
            self.pipelines[old_worker].demo_hr_only = False        # moving the demo input to someone else
            self.demo_hr.set_mode("off", old_worker, self.now(old_worker))
        if mode != "off" and worker_id not in self.pipelines:
            self.register_worker(worker_id)
        t = self.now(worker_id)
        p = self.pipelines.get(worker_id)
        last_real = p.last_reading.get("hr") if p and p.last_reading.get("hr_source") != "demo_input" else None
        self.demo_hr.set_mode(mode, worker_id, t, start_bpm=last_real)
        if p is not None:
            p.demo_hr_only = mode != "off"
        state = self.demo_hr.state()
        self.dispatch([{"type": "demo_heart_rate", "timestamp": t, "worker_id": worker_id, "mode": mode,
                        "previous": old_mode, "demo": mode != "off", "source": "demo_input"}])
        return state

    def keep_alive(self, now: float | None = None) -> None:
        """
        Re-send each real band its current status every BAND_RESEND_S real seconds, so it
        never thinks the link is dead (firmware contract). Console and replay workers are
        skipped; there's no physical band behind them.
        """
        now = time.monotonic() if now is None else now
        for wid, p in self.pipelines.items():
            if self.workers[wid]["source"] == "replay":
                continue
            if now - self.last_sent.get(wid, now - config.BAND_RESEND_S - 1) >= config.BAND_RESEND_S:
                self.band.send(wid, f"BUZZ:{p.router.current}", physical_only=True)
                self.last_sent[wid] = now

    def _prerolling(self, worker_id: str) -> bool:
        return self.replay is not None and self.replay.prerolling and worker_id in self.replay.workers

    def preroll_done(self, worker_ids: list[str]) -> None:
        """Replay finished its instant pre-roll: tell each band what it should be showing now."""
        for wid in worker_ids:
            p = self.pipelines.get(wid)
            if p is not None and p.router.current != "OFF":
                self.band.send(wid, f"BUZZ:{p.router.current}")

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------
    def dispatch(self, events: list[dict]) -> None:
        for ev in events:
            kind = ev["type"]
            if ev.get("worker_id") and self._prerolling(ev["worker_id"]):
                ev["preroll"] = True        # replayed history, not news (the console skips these)
            if kind == "estimate":
                self._add_truth(ev)
                self._add_why(ev)
            elif kind == "band_command":
                if not self._prerolling(ev["worker_id"]):      # history, not news: don't buzz anyone
                    self.band.send(ev["worker_id"], ev["line"])
                    self.last_sent[ev["worker_id"]] = time.monotonic()
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
            # Broadcast first: a listener (session D) may react with events of its own, and those
            # must reach the dashboards AFTER the event that caused them.
            self.broadcast(ev)
            for listener in self.listeners:
                try:
                    listener(ev)
                except Exception as e:
                    log.warning("listener failed on %s: %s", kind, e)

    def _add_why(self, ev: dict) -> None:
        """
        Attach the latest why-line (None until session H's ml/explain is merged) and ask
        for a fresh one for this minute; it arrives later as a "why" event.
        """
        wid = ev["worker_id"]
        ev["why"] = self.whys.get(wid, {}).get("why")
        p = self.pipelines.get(wid)
        if (self.why is not None and self.why.enabled and ev.get("forecast") and p
                and not ev.get("preroll") and not ev.get("refresh")):
            self.why.request(wid, p._model_arrays(), p.n_minutes - 1)

    def on_why(self, worker_id: str, minute: int, result: dict) -> None:
        """A why-line is ready (called on the event loop)."""
        if worker_id not in self.pipelines:
            return
        self.whys[worker_id] = {"minute": minute, **result}
        self.dispatch([{"type": "why", "timestamp": self.now(worker_id), "worker_id": worker_id,
                        "minute": minute, "why": result.get("why"), "direction": result.get("direction"),
                        "contributions": result.get("contributions")}])

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

    def spot_check(self, worker_id: str, thermometer_c: float, timestamp: float | None = None) -> dict:
        """
        A thermometer spot check for one worker (POST /spot-check). Updates and saves their
        personal offset, then sends an "offset_update" event. Returns that event.
        """
        p = self.pipelines[worker_id]
        events = p.spot_check(thermometer_c, self.now(worker_id) if timestamp is None else timestamp)
        update = events[0]
        if self.db:
            if "thermometer_c" in update:
                self.db.add_spot_check(update)
            if update["accepted"]:
                self.db.save_offset(p.offset.to_dict())
        self.dispatch(events)
        return update

    def set_schedule(self, schedule: dict) -> None:
        """Session B's current schedule (frontend Schedule shape). Drives on_break / break_at."""
        self.schedule = schedule
        if self.db:
            self.db.save_kv("schedule", schedule)
        self.dispatch([{"type": "schedule", "timestamp": self.real_clock.now(), "schedule": schedule}])

    def set_site_status(self, site_id: str, status: dict) -> None:
        """
        Session C1: the full live hazard picture for a site (contract's SiteStatus: hazards,
        index_tomorrow, index_drivers). Shown on the dashboard as-is. Band patterns still
        only change through set_site_hazard(), so C1 calls both.
        """
        self.site_status[site_id] = status
        self.dispatch([{"type": "site_status_update", "timestamp": self.real_clock.now(), "site_id": site_id}])

    def press_sos(self, worker_id: str, source: str = "demo") -> None:
        """Act as if the worker held the band button for 3 s (SOS). source: demo | phone."""
        self.handle_input(ButtonPress(worker_id=worker_id, timestamp=self.now(worker_id), kind="sos"), source=source)

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
