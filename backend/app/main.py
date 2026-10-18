"""
The live server: one Python process, FastAPI + background tasks.

Run (from the repo root):
    python -m backend.app.main --source replay --speed 10        # demo, no hardware
    python -m backend.app.main --source live                     # real band (Bluetooth, else USB; WiFi) + heart rate
    python -m backend.app.main --source live --band usb          # band on the USB cable instead of Bluetooth
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
from pydantic import BaseModel, Field

from backend import config
from backend.alerts.band_output import ConsoleBand, MultiBand, UDPBand
from backend.app.frontend_api import FrontendAdapter, router as frontend_router
from backend.app.hub import Hub
from backend.replay import shifts
from backend.replay.replay import Replay

log = logging.getLogger("planit")
STATIC = Path(__file__).parent / "static"
TICK_S = 0.5                     # how often timers are checked (real seconds)


# ---------------------------------------------------------------------------
# Settings (from the command line, or defaults when imported by tests/uvicorn)
# ---------------------------------------------------------------------------
def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Redline live server")
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
    ap.add_argument("--demo-timers", action="store_true",
                    help="demo only: man-down beep after 10 s still, crew lead call ~20 s, emergency call ~30 s")
    ap.add_argument("--no-forecaster", action="store_true", help="Kalman-only alerts")
    ap.add_argument("--checkpoint", default=None, help="forecaster file (default: final_cv.pt, then ens5.pt, then v1.pt)")
    ap.add_argument("--band", choices=["auto", "ble", "usb"], default="auto",
                    help="how the band talks to the laptop (live): ble = Bluetooth LE, usb = serial cable, "
                         "auto = Bluetooth unless --serial is given (USB from SERIAL_PORT if Bluetooth can't start)")
    ap.add_argument("--band-device", default=config.BLE_BAND_DEVICE,
                    help=f"Bluetooth band: part of its name or its address (default: BLE_BAND_DEVICE, "
                         f"else {config.BLE_BAND_NAME!r})")
    ap.add_argument("--serial", default=None, help="band USB serial port (live; default SERIAL_PORT). '' = no USB")
    ap.add_argument("--udp-port", type=int, default=config.UDP_PORT, help="band WiFi UDP port (live)")
    ap.add_argument("--no-pulsoid", action="store_true", help="live mode without the Pulsoid heart rate client")
    ap.add_argument("--no-ble", dest="ble", action="store_false", help="live mode without Bluetooth heart rate")
    ap.add_argument("--ble-device", default=config.BLE_HR_DEVICE,
                    help="Bluetooth heart rate device: part of its name or its address (default: BLE_HR_DEVICE)")
    ap.add_argument("--record", action="store_true", help="save live inputs to data/recordings/*.jsonl")
    ap.add_argument("--db", default=config.DB_PATH, help="SQLite file ('' = no database)")
    ap.add_argument("--hazards", choices=["fake", "live", "off"], default=None,
                    help="session C1's hazard feeds: fake demo data, live feeds, or off "
                         "(default: fake with --source replay, live otherwise)")
    ap.add_argument("--emergency-log", default=None,
                    help="where session D logs every call step (default: its EMERGENCY_LOG_PATH; '' = memory only)")
    ap.add_argument("--no-planner", dest="planner", action="store_false",
                    help="don't run session B's planner for the live crew")
    ap.add_argument("--emergency-phone", choices=["dry_run", "fake", "live"], default="dry_run",
                    help="session D's phone backend for confirmed man-down calls (never dials 911)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=config.SERVER_PORT)
    return ap.parse_args(argv)


SETTINGS = parse_args([])         # defaults; replaced by main() when run from the command line


def load_predictor(settings):
    """Best forecaster available (final_cv, then ens5, then v1), else None = Kalman-only alerts."""
    if settings.no_forecaster:
        return None
    from backend.app.model_runner import load_best_predictor
    return load_best_predictor(explicit=settings.checkpoint)


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
    if hub.predictor is not None:
        from backend.app.why import WhyExplainer
        loop = asyncio.get_running_loop()
        hub.why = WhyExplainer(hub.predictor.source_path,
                               on_result=lambda *r: loop.call_soon_threadsafe(hub.on_why, *r))
        log.info("Why-lines: %s", "on (session H's explain)" if hub.why.enabled
                 else "off until session H's ml/explain is merged")
    app.state.hub = hub
    app.state.frontend = FrontendAdapter(hub)           # the Next.js dashboard's contract (/ws/live)
    if db:
        app.state.frontend.restore_alerts(db.recent_alert_events())   # alert history survives a restart
    hub.add_listener(app.state.frontend.on_event)
    # The dashboard clock rides on the hub's tick, so whatever drives the hub (the real loop, or
    # a test's stepped clock) also sends "clock".
    hub.tick_listeners.append(app.state.frontend.tick)
    tasks = [asyncio.create_task(_tick_forever(hub, app.state.frontend))]

    tasks += wire_emergency(hub, s)
    from backend.app.site_bridge import make_bridge
    hazard_mode = s.hazards or ("fake" if s.source == "replay" else "live")
    app.state.sites = make_bridge(hub, hazard_mode)     # session C1's 79 sites + hazards, if merged
    if app.state.sites is not None:
        tasks.append(asyncio.create_task(app.state.sites.run()))
    from backend.app.planner_bridge import make_planner_bridge
    app.state.planner = make_planner_bridge(hub) if s.planner else None
    if app.state.planner is not None:
        tasks.append(asyncio.create_task(app.state.planner.run()))
        try:
            # Session B's act-on-forecast: reads estimates, changes only the plan, sends planner_change events.
            from backend.planner.proactive import attach
            attach(hub)
        except ImportError:
            pass

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
    if ev.get("preroll"):
        return
    if kind == "heat_alert":
        text = f"heat {ev['level'].upper()} ({ev['value']} °C vs {ev['threshold']}, {ev['rule']} rule)"
    elif kind == "mandown_confirmed":
        v = ev["last_vitals"]
        text = (f"MANDOWN_CONFIRMED {ev['worker_name']} at {ev['site_name']} ({ev['reason']}, "
                f"{ev['seconds_since_start']:.0f} s, core {v['core_temp']} °C, HR {v['hr']})")
    elif kind == "offset_update":
        text = (f"spot check {ev.get('thermometer_c')} °C -> personal offset {ev['offset_c']:+.2f} °C "
                f"({ev['n_checks']} checks{'' if ev['accepted'] else ', reading rejected'})")
    elif kind == "hr_link":
        text = f"heart rate ({ev['source']}) {ev['state']}: {ev['message']}"
    elif kind == "sos":
        text = f"SOS from {ev['worker_name']} at {ev['site_name']}"
    elif kind == "emergency_status":
        text = f"emergency {ev['stage']}/{ev['status']} {ev.get('target') or ''} {ev.get('demo_label') or ''}".strip()
    elif kind in ("mandown_check", "mandown_cancelled", "mandown_resolved", "heat_ack", "red_unacknowledged"):
        text = kind + "".join(f" {k}={ev[k]}" for k in ("reason", "by") if ev.get(k))
    elif kind == "hazard":
        text, who = f"hazard {ev['kind']} {'ON' if ev['on'] else 'off'} ({ev['source']})", ev["site_id"]
    elif kind in ("band_fall", "band_up"):
        from backend.app.frontend_api import fall_text
        text = fall_text(ev)                     # every fall decision, so real-band tests show why
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
    from backend.emergency.escalation import EscalationLog
    kwargs = {"log": EscalationLog(s.emergency_log)} if s.emergency_log is not None else {}
    from backend.alerts import mandown
    if getattr(s, "demo_timers", False):
        mandown.use_demo_timers(True)
        kwargs["cancel_window_s"] = config.DEMO_EMERGENCY_CANCEL_WINDOW_S
        log.warning("DEMO TIMERS ON: man-down after %ss still, confirmed %ss later, emergency call %ss after that",
                    config.DEMO_MANDOWN_STILL_S, config.DEMO_MANDOWN_ESCALATE_S, config.DEMO_EMERGENCY_CANCEL_WINDOW_S)
    manager = EscalationManager(phone=make_phone(s.emergency_phone),
                                emit=lambda status: hub.dispatch([status.model_dump()]), **kwargs)

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
            hub.tick()                       # also runs frontend.tick (clock + band-connected), twice a second
        except Exception:
            log.exception("tick failed")
        n += 1
        await asyncio.sleep(TICK_S)


def band_links(s) -> tuple[bool, str]:
    """
    (use Bluetooth?, USB serial port or "") for the band, from --band / --serial:
      ble   Bluetooth only
      usb   the USB cable only (--serial, else SERIAL_PORT)
      auto  Bluetooth, unless --serial was given (then USB only)
    If Bluetooth can't run here (tests, PLANIT_BLE=off, bleak missing), the USB port is the fallback.
    WiFi (UDP) is always listening as well.
    """
    from backend.ingest import ble_band
    port = s.serial if s.serial is not None else config.SERIAL_PORT
    wants_ble = s.band == "ble" or (s.band == "auto" and s.serial is None)
    if not wants_ble:
        return False, port
    if not ble_band.safe_to_scan():
        log.info("Bluetooth band off (test suite or PLANIT_BLE=off)%s", f": using USB {port}" if port else "")
        return False, port
    if not ble_band.available():
        log.warning("bleak not installed: no Bluetooth band (pip install -r requirements.txt)%s",
                    f". Using USB {port}" if port else "")
        return False, port
    return True, ""


async def _start_live_inputs(hub: Hub, band: MultiBand, s) -> list[asyncio.Task]:
    """Band over Bluetooth or USB (band_links), WiFi UDP listener, heart rate (Bluetooth, Pulsoid)."""
    from backend.ingest import pulsoid_client, serial_reader, udp_listener

    tasks = []
    # Tie the band to a real crew member, so alerts, the dashboards and the emergency call use their name.
    if config.WORKER_NAME and config.WORKER_ID not in hub.workers:
        hub.register_worker(config.WORKER_ID, name=config.WORKER_NAME, source="live")
        log.info("Band belongs to %s (%s)", config.WORKER_NAME, config.WORKER_ID)
    udp_band = UDPBand()
    band.add(udp_band)
    await udp_listener.start(hub, udp_band, port=s.udp_port, worker_id=config.WORKER_ID)
    log.info("Listening for the band on UDP port %d", s.udp_port)
    use_ble, serial_port = band_links(s)
    if use_ble:
        # In a helper process, like Bluetooth heart rate: a macOS refusal kills the helper, not the server.
        from backend.ingest import ble_band
        helper = ble_band.BleBandHelper(hub, config.WORKER_ID, wanted=s.band_device or None)
        band.add(helper.output)                          # BUZZ commands go back over the band's RX
        tasks.append(asyncio.create_task(helper.run()))
        log.info("Looking for the band over Bluetooth (%r). macOS asks for Bluetooth permission the first time.",
                 s.band_device or config.BLE_BAND_NAME)
    if serial_port:
        try:
            serial_band = serial_reader.start(hub, serial_port, worker_id=config.WORKER_ID)
            band.add(serial_band)
            log.info("Reading the band on %s at %d baud", serial_port, config.SERIAL_BAUD)
        except Exception as e:
            log.warning("Serial port %s not available (%s). Continuing without USB.", serial_port, e)
    # Heart rate. Bluetooth first (no internet needed); Pulsoid as an optional online second source.
    # When both are on, the Kalman fuses them (step_fused) and keeps going if one drops.
    ble_on = False
    if s.ble:
        from backend.ingest import ble_hr
        if not ble_hr.safe_to_scan():
            log.info("Bluetooth heart rate off (test suite or PLANIT_BLE=off)")
        elif not ble_hr.available():
            log.warning("bleak not installed: no Bluetooth heart rate (pip install -r requirements.txt)")
        else:
            # In a helper process: if macOS refuses Bluetooth it kills that process, not this server.
            reader = ble_hr.BleHelper(hub, config.WORKER_ID, wanted=s.ble_device or None)
            tasks.append(asyncio.create_task(reader.run()))
            ble_on = True
            log.info("Bluetooth heart rate started (%s). macOS asks for Bluetooth permission the first time.",
                     f"device {s.ble_device!r}" if s.ble_device else "first heart rate device found")
    if config.PULSOID_TOKEN and not s.no_pulsoid:
        tasks.append(asyncio.create_task(pulsoid_client.run(hub, config.PULSOID_TOKEN, worker_id=config.WORKER_ID)))
        log.info("Pulsoid heart rate client started")
    elif not ble_on:
        log.warning("No heart rate source: no Bluetooth and no PULSOID_TOKEN (band lines still flow)")
    return tasks


app = FastAPI(title="Redline", lifespan=lifespan)
try:
    # Session B's planner: /api/schedule, /api/briefing, /api/planner/*. Mounted before
    # frontend_router so its routes win over the empty fallbacks there.
    from backend.planner.api import router as planner_router
    app.include_router(planner_router)
except ImportError:
    pass
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


class SpotCheck(BaseModel):
    worker_id: str
    thermometer_c: float = Field(ge=34, le=42)   # ear or oral thermometer reading, °C (outside: 422)
    timestamp: float | None = None      # when it was taken (default: now on that worker's clock)


@app.post("/spot-check")
@app.post("/api/spot-check")
async def spot_check(body: SpotCheck):
    """Personal offset twin: record a thermometer spot check for a worker."""
    _worker(body.worker_id)
    return hub_of().spot_check(body.worker_id, body.thermometer_c, body.timestamp)


class WeatherRequest(BaseModel):
    wbgt: float
    future_wbgt: list[float] | None = None     # next 30 minutes, one per minute (optional)
    temp_c: float | None = None                # site air temp + humidity: compared with each band's
    rh_pct: float | None = None                # air sensor for a local WBGT correction (optional)


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
    hub_of().set_site_weather(site_id, body.wbgt, body.future_wbgt, body.temp_c, body.rh_pct)
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
    print(f"\n  Redline: open http://{SETTINGS.host}:{SETTINGS.port}\n", flush=True)
    uvicorn.run(app, host=SETTINGS.host, port=SETTINGS.port, log_level="warning")


if __name__ == "__main__":
    main()
