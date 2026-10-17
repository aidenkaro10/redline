"""
The live server: one Python process, FastAPI + background tasks.

Run (from the repo root):
    python -m backend.app.main --source replay --speed 10        # demo, no hardware
    python -m backend.app.main --source live                     # real band (USB/WiFi) + Pulsoid
then open http://localhost:8000

What it serves (full list with payloads: docs/interfaces/live-system.md):
    GET  /                 bare chart page (HR, core temp estimate, shaded forecast band)
    WS   /ws               live event stream (first message is a snapshot)
    GET  /api/state        same snapshot as JSON
    POST /api/demo/...     demo controls (threshold, hazard, fall, button)
    POST /api/replay...    replay on/off and speed
    POST /api/hazards, /api/sites, /api/plans, /api/workers   plug-ins for sessions B and C1
"""

import argparse
import asyncio
import contextlib
import logging
import math
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend import config
from backend.alerts.band_output import ConsoleBand, MultiBand, UDPBand
from backend.app.frontend_api import FrontendAdapter, router as frontend_router
from backend.app.hub import Hub
from backend.replay import shifts
from backend.replay.replay import Replay

log = logging.getLogger("planit")
STATIC = Path(__file__).parent / "static"
CHECKPOINT = Path("ml/checkpoints/v1.pt")
TICK_S = 0.5                     # how often timers are checked (real seconds)


# ---------------------------------------------------------------------------
# Settings (from the command line, or defaults when imported by tests/uvicorn)
# ---------------------------------------------------------------------------
def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="PlanIT Control live server")
    ap.add_argument("--source", choices=["replay", "live", "none"], default="replay",
                    help="replay = simulated shifts, live = band + Pulsoid, none = wait for REST/inputs")
    ap.add_argument("--speed", type=float, default=10.0, help="replay speed factor (10 = 10x real time)")
    ap.add_argument("--replay-file", default=None,
                    help="'sim' (data/sim/sim.npz), 'synthetic', or a recorded .jsonl file. Default: sim if present")
    ap.add_argument("--shift", type=int, default=shifts.DEMO_SHIFT, help="sim shift index for the main replay worker")
    ap.add_argument("--no-fall", action="store_true", help="replay without the scripted crewmate fall")
    ap.add_argument("--no-ack", action="store_true",
                    help="replay workers never press the button (shows the red-unacknowledged man-down path)")
    ap.add_argument("--demo", action="store_true", help="start with demo thresholds (37.3 / 37.5 °C)")
    ap.add_argument("--no-forecaster", action="store_true", help="Kalman-only alerts")
    ap.add_argument("--checkpoint", default=str(CHECKPOINT))
    ap.add_argument("--serial", default=config.SERIAL_PORT, help="band USB serial port (live)")
    ap.add_argument("--udp-port", type=int, default=config.UDP_PORT, help="band WiFi UDP port (live)")
    ap.add_argument("--no-pulsoid", action="store_true", help="live mode without the Pulsoid heart rate client")
    ap.add_argument("--record", action="store_true", help="save live inputs to data/recordings/*.jsonl")
    ap.add_argument("--db", default=config.DB_PATH, help="SQLite file ('' = no database)")
    ap.add_argument("--emergency-phone", choices=["dry_run", "fake", "live"], default="dry_run",
                    help="session D's phone backend for confirmed man-down calls (never dials 911)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=config.SERVER_PORT)
    return ap.parse_args(argv)


SETTINGS = parse_args([])         # defaults; replaced by main() when run from the command line


def load_predictor(settings):
    """Forecaster v1 if the checkpoint is there, else None (Kalman-only alerts)."""
    if settings.no_forecaster:
        return None
    path = Path(settings.checkpoint)
    if not path.exists():
        log.warning("No forecaster checkpoint at %s: running Kalman-only alerts", path)
        return None
    try:
        from backend.models.forecaster import ForecasterPredictor
        predictor = ForecasterPredictor.load(path)
        log.info("Loaded forecaster %s", predictor.name)
        return predictor
    except Exception as e:
        log.warning("Could not load forecaster (%s): running Kalman-only alerts", e)
        return None


def make_replay(hub: Hub, settings, source: str | None = None, speed: float | None = None) -> Replay:
    source = source or settings.replay_file or ("sim" if shifts.SIM_PATH.exists() else "synthetic")
    fall = None if settings.no_fall else 1
    ack = None if settings.no_ack else 20.0
    if source == "sim":
        plan = shifts.from_sim(shift=settings.shift, fall_worker=fall, auto_ack_s=ack)
    elif source == "synthetic":
        plan = shifts.synthetic(fall_worker=fall, auto_ack_s=ack)
    else:
        plan = shifts.from_jsonl(Path(source), auto_ack_s=ack)
    log.info("Replay: %s at %sx", plan.description, speed or settings.speed)
    return Replay(plan, hub, speed=speed or settings.speed)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    s = SETTINGS
    band = MultiBand([ConsoleBand()])
    db = recorder = None
    if s.db:
        from backend.storage.db import Database
        db = Database(s.db)
    if s.record:
        from backend.storage.recorder import Recorder
        recorder = Recorder()
        log.info("Recording live inputs to %s", recorder.path)
    hub = Hub(predictor=load_predictor(s), band=band, db=db, recorder=recorder, demo=s.demo)
    hub.add_listener(print_key_event)
    app.state.hub = hub
    app.state.frontend = FrontendAdapter(hub)           # the Next.js dashboard's contract (/ws/live)
    hub.add_listener(app.state.frontend.on_event)
    tasks = [asyncio.create_task(_tick_forever(hub, app.state.frontend))]

    tasks += wire_emergency(hub, s)

    if s.source == "replay":
        hub.start_replay(make_replay(hub, s))
    elif s.source == "live":
        tasks += await _start_live_inputs(hub, band, s)

    try:
        yield
    finally:
        hub.stop_replay()
        for t in tasks:
            t.cancel()
        band.close()
        if recorder:
            recorder.close()
        if db:
            db.close()


def print_key_event(ev: dict) -> None:
    """Console line for the events a person cares about (band commands print themselves)."""
    kind, who = ev["type"], ev.get("worker_id", "")
    if kind == "heat_alert":
        text = f"heat {ev['level'].upper()} ({ev['value']} °C vs {ev['threshold']}, {ev['rule']} rule)"
    elif kind == "mandown_confirmed":
        v = ev["last_vitals"]
        text = (f"MANDOWN_CONFIRMED {ev['worker_name']} at {ev['site_name']} ({ev['reason']}, "
                f"{ev['seconds_since_start']:.0f} s, core {v['core_temp']} °C, HR {v['hr']})")
    elif kind == "sos":
        text = f"SOS from {ev['worker_name']} at {ev['site_name']}"
    elif kind == "emergency_status":
        text = f"emergency {ev['stage']}/{ev['status']} {ev.get('target') or ''} {ev.get('demo_label') or ''}".strip()
    elif kind in ("mandown_check", "mandown_cancelled", "mandown_resolved", "heat_ack", "red_unacknowledged"):
        text = kind + "".join(f" {k}={ev[k]}" for k in ("reason", "by") if ev.get(k))
    elif kind == "hazard":
        text, who = f"hazard {ev['kind']} {'ON' if ev['on'] else 'off'} ({ev['source']})", ev["site_id"]
    else:
        return
    print(f"{time.strftime('%H:%M:%S')} [event {who}] {text}", flush=True)


def wire_emergency(hub: Hub, s) -> list[asyncio.Task]:
    """
    Session D's escalation (backend/emergency/), if that code is merged in.
    Fixed rule, plain code: every "mandown_confirmed" or "sos" event goes to the
    manager; its "emergency_status" updates go back out to the dashboard.
    """
    try:
        from backend.emergency.escalation import EscalationManager
        from backend.emergency.voice_agent import make_phone
    except ImportError:
        log.info("Emergency escalation (session D) not merged yet: man-down events still go to the dashboard")
        app.state.emergency = None
        return []
    manager = EscalationManager(phone=make_phone(s.emergency_phone),
                                emit=lambda status: hub.dispatch([status.model_dump()]))

    def on_event(ev: dict) -> None:
        if ev["type"] in ("mandown_confirmed", "sos"):
            manager.handle(ev)

    hub.add_listener(on_event)
    app.state.emergency = manager
    log.info("Emergency escalation wired (%s phone)", s.emergency_phone)
    return [asyncio.create_task(manager.run())]


async def _tick_forever(hub: Hub, frontend: FrontendAdapter):
    n = 0
    while True:
        try:
            hub.tick()
            if n % 2 == 0:
                frontend.tick()              # clock + band-connected, once a second
        except Exception:
            log.exception("tick failed")
        n += 1
        await asyncio.sleep(TICK_S)


async def _start_live_inputs(hub: Hub, band: MultiBand, s) -> list[asyncio.Task]:
    """USB serial (if a port is set), WiFi UDP listener, Pulsoid heart rate (if a token is set)."""
    from backend.ingest import pulsoid_client, serial_reader, udp_listener

    tasks = []
    udp_band = UDPBand()
    band.add(udp_band)
    await udp_listener.start(hub, udp_band, port=s.udp_port, worker_id=config.WORKER_ID)
    log.info("Listening for the band on UDP port %d", s.udp_port)
    if s.serial:
        try:
            serial_band = serial_reader.start(hub, s.serial, worker_id=config.WORKER_ID)
            band.add(serial_band)
            log.info("Reading the band on %s at %d baud", s.serial, config.SERIAL_BAUD)
        except Exception as e:
            log.warning("Serial port %s not available (%s). Continuing without USB.", s.serial, e)
    if config.PULSOID_TOKEN and not s.no_pulsoid:
        tasks.append(asyncio.create_task(pulsoid_client.run(hub, config.PULSOID_TOKEN, worker_id=config.WORKER_ID)))
        log.info("Pulsoid heart rate client started")
    else:
        log.warning("No PULSOID_TOKEN in .env: no live heart rate (band lines still flow)")
    return tasks


app = FastAPI(title="PlanIT Control", lifespan=lifespan)
app.include_router(frontend_router)
# Let the Next.js dashboard (another port) call the REST API from the browser.
app.add_middleware(CORSMiddleware, allow_methods=["*"], allow_headers=["*"],
                   allow_origins=os.getenv("FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","))
# Every route is "async def" on purpose: FastAPI runs those on the event loop, so the hub
# is only ever touched from one thread. (Plain "def" routes would run on a thread pool.)


def hub_of() -> Hub:
    return app.state.hub


def _clean(x):
    """JSON can't carry NaN; turn any stray NaN into null."""
    if isinstance(x, float) and math.isnan(x):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _worker(worker_id: str):
    if worker_id not in hub_of().pipelines:
        raise HTTPException(404, f"Unknown worker {worker_id}")


# ---------------------------------------------------------------------------
# Page + live stream
# ---------------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/raw/state")
async def raw_state():
    """The hub's own snapshot (what /ws sends first). The dashboard uses /api/state instead."""
    return _clean(hub_of().snapshot())


@app.get("/api/raw/workers/{worker_id}/estimates")
async def raw_estimates(worker_id: str, limit: int = 600):
    _worker(worker_id)
    return _clean(list(hub_of().pipelines[worker_id].estimates)[-limit:])


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    hub = hub_of()
    q = hub.subscribe()
    try:
        await websocket.send_json(_clean(hub.snapshot()))
        while True:
            await websocket.send_json(_clean(await q.get()))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(q)


# ---------------------------------------------------------------------------
# Demo controls (one click each on the dashboard)
# ---------------------------------------------------------------------------
class OnOff(BaseModel):
    on: bool


class Speed(BaseModel):
    speed: float


class ReplayRequest(BaseModel):
    on: bool
    speed: float | None = None
    source: str | None = None        # "sim", "synthetic", or a .jsonl path


class HazardRequest(BaseModel):
    kind: str                        # lightning | flood | fire | red_flag | fire_weather
    on: bool = True
    site_id: str | None = None       # default: the demo site
    source: str = "demo"
    details: dict | None = None


class WorkerRequest(BaseModel):
    worker_id: str


@app.post("/api/demo/threshold")
async def demo_threshold(body: OnOff):
    hub_of().set_demo(body.on)
    return app.state.frontend.demo_state()


@app.post("/api/replay")
async def replay_toggle(body: ReplayRequest):
    hub = hub_of()
    if body.on:
        try:
            hub.start_replay(make_replay(hub, SETTINGS, body.source, body.speed))
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(400, str(e))
    else:
        hub.stop_replay()
    return hub.replay.state() if hub.replay else {"on": False}


@app.post("/api/replay/speed")
async def replay_speed(body: Speed):
    if body.speed <= 0:
        raise HTTPException(400, "speed must be > 0")
    hub = hub_of()
    if hub.replay is None:
        raise HTTPException(409, "replay is off")
    hub.set_speed(body.speed)
    return hub.replay.state()


@app.post("/api/demo/hazard")
async def demo_hazard(body: HazardRequest):
    try:
        return hub_of().set_site_hazard(body.site_id, body.kind, body.on, body.source, body.details)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/demo/fall")
async def demo_fall(body: WorkerRequest):
    _worker(body.worker_id)
    hub_of().fake_fall(body.worker_id)
    return {"ok": True}


@app.post("/api/demo/button")
async def demo_button(body: WorkerRequest):
    _worker(body.worker_id)
    hub_of().press_button(body.worker_id)
    return {"ok": True}


@app.post("/api/demo/sos")
async def demo_sos(body: WorkerRequest):
    _worker(body.worker_id)
    hub_of().press_sos(body.worker_id)
    return {"ok": True}


@app.post("/api/emergency/cancel")
async def emergency_cancel(body: WorkerRequest):
    """Crew lead: "cancel, false alarm". False if there's nothing to cancel or it's too late."""
    manager = getattr(app.state, "emergency", None)
    if manager is None:
        raise HTTPException(409, "emergency escalation (session D) is not merged in")
    return {"cancelled": manager.cancel(worker_id=body.worker_id)}


# ---------------------------------------------------------------------------
# Plug-ins: site hazards (C1), sites (C1), plans (B), worker assignment
# ---------------------------------------------------------------------------
class SiteRequest(BaseModel, extra="allow"):
    """A job site. Extra fields (operator, tech, capacity_mw, county, coastal) are kept."""
    site_id: str
    name: str
    lat: float
    lon: float


class WeatherRequest(BaseModel):
    wbgt: float
    future_wbgt: list[float] | None = None     # next 30 minutes, one per minute (optional)


class ContextRequest(BaseModel):
    wbgt: float | None = None
    workload: float | None = None               # W/m², planned task effort right now
    future_wbgt: list[float] | None = None
    future_workload: list[float] | None = None  # planned workload for the next 30 minutes


class AssignRequest(BaseModel):
    worker_id: str
    name: str | None = None
    site_id: str | None = None


@app.post("/api/hazards")
async def hazards(body: HazardRequest):
    return await demo_hazard(body)


@app.post("/api/sites")
async def add_site(body: SiteRequest):
    hub_of().add_site(body.model_dump())
    return body.model_dump()


@app.post("/api/sites/{site_id}/status")
async def site_status(site_id: str, body: dict):
    """Session C1: full SiteStatus (hazards list, index_tomorrow, index_drivers) for the dashboard."""
    if site_id not in hub_of().sites:
        raise HTTPException(404, f"Unknown site {site_id}")
    hub_of().set_site_status(site_id, body)
    return {"ok": True}


@app.post("/api/sites/{site_id}/weather")
async def site_weather(site_id: str, body: WeatherRequest):
    hub_of().set_site_weather(site_id, body.wbgt, body.future_wbgt)
    return {"ok": True}


@app.post("/api/workers/{worker_id}/context")
async def worker_context(worker_id: str, body: ContextRequest):
    """Session B: a worker's planned workload (now and next 30 min). Forecaster inputs."""
    _worker(worker_id)
    hub_of().set_context(worker_id, **body.model_dump(exclude_none=True))
    return {"ok": True}


@app.post("/api/workers")
async def assign_worker(body: AssignRequest):
    """Create a worker or move one to another site (resets their pipeline)."""
    hub = hub_of()
    if body.site_id and body.site_id not in hub.sites:
        raise HTTPException(404, f"Unknown site {body.site_id}")
    old = hub.workers.get(body.worker_id, {})
    hub.register_worker(body.worker_id, name=body.name or old.get("name"), site_id=body.site_id,
                        source=old.get("source", "live"), clock=hub.clocks.get(body.worker_id))
    return hub.public_info(body.worker_id)


@app.post("/api/plans")
async def add_plan(plan: dict):
    return hub_of().add_plan(plan)


# ---------------------------------------------------------------------------
def main(argv=None):
    global SETTINGS
    SETTINGS = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn
    print(f"\n  PlanIT Control: open http://{SETTINGS.host}:{SETTINGS.port}\n", flush=True)
    uvicorn.run(app, host=SETTINGS.host, port=SETTINGS.port, log_level="warning")


if __name__ == "__main__":
    main()
