"""
Shared tools for the end-to-end tests (session J).

The big idea: start the REAL app (FastAPI + hub + emergency + bridges) in-process with
FastAPI's TestClient, then drive time ourselves with a clock we step forward by hand.
That makes a 45-minute replay finish in seconds, the same way every run.

What's in here:
  - StepClock:      a replay clock that only moves when the test says so
  - RecordingBand:  a fake "physical" band output that remembers every BUZZ line sent
  - SpyPhone:       session D's dry-run phone with a spy on every number it is asked to dial
  - LiveApp:        the running app plus helpers (start a replay, step time, drain a WebSocket)
  - live_app:       pytest fixture that builds a LiveApp with safe settings
  - no_network:     autouse fixture: any connection off this machine fails the test

Safety rules for every test here:
  - never a real phone call (dry run, test numbers only, a spy checks every number)
  - never a paid API or the internet (network blocked, fake hazard mode, planner without LLM)
"""

import os
import socket
import subprocess
from pathlib import Path

import pytest

# Keep the CPU free for the ML session's training run.
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("PLANNER_LLM", "off")        # the planner never calls the Claude API in tests
try:
    import torch
    torch.set_num_threads(2)
except ImportError:          # Kalman-only machines still run the suite
    pass

from fastapi.testclient import TestClient  # noqa: E402

from backend import config  # noqa: E402
from backend.alerts.band_output import BandOutput  # noqa: E402
from backend.app import main  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

# Fake phone numbers for the emergency chain. 555-01xx numbers are reserved for fiction.
TEST_NUMBERS = {
    "crew_lead": "+13525550101",
    "safety_officer": "+13525550102",
    "emergency": "+13525550103",     # the "teammate playing dispatcher". Never 911.
}


# ---------------------------------------------------------------------------
# Finding gitignored data (sim shifts, forecaster checkpoints)
# ---------------------------------------------------------------------------
def main_checkout() -> Path | None:
    """The repo's main checkout (worktrees share its .git). Big data files live there."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).parent
    except Exception:
        return None


def find_data(relative: str) -> Path | None:
    """Look for a gitignored file here first, then in the main checkout. None if missing."""
    for root in (Path.cwd(), main_checkout()):
        if root is not None and (root / relative).exists():
            return root / relative
    return None


SIM_PATH = find_data("data/sim/sim.npz")
# The model the live demo uses (backend/app/model_runner.py CHECKPOINTS order), else the older ens5.
DEMO_MODEL_PATH = find_data("ml/checkpoints/final_cv.pt") or find_data("ml/checkpoints/ens5.pt")


# ---------------------------------------------------------------------------
# No internet in tests
# ---------------------------------------------------------------------------
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "0.0.0.0", "testserver")


@pytest.fixture(scope="module", autouse=True)
def no_network():
    """
    Any attempt to reach another machine raises right away (so it fails loudly, not slowly).
    Module-wide, so it also covers the shared recordings some test files make once per module.
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in LOCAL_HOSTS and not host.startswith("/"):
            raise OSError(f"e2e tests are offline: blocked connection to {host}")
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in LOCAL_HOSTS and host is not None:
            raise socket.gaierror(f"e2e tests are offline: blocked DNS lookup of {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(socket.socket, "connect", guarded_connect)
    mp.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    yield
    mp.undo()


@pytest.fixture(scope="module", autouse=True)
def hazard_files_in_tmp(tmp_path_factory):
    """Session C1's caches and hazard report log go to a temp folder, never data/hazards."""
    try:
        from backend.hazards import report_log, store
    except ImportError:
        yield
        return
    folder = tmp_path_factory.mktemp("hazards")
    old_mode = store.get_mode()
    store.set_data_dir(folder)
    report_log.reset_log(folder / "hazard_log.jsonl")
    yield
    store.set_mode(old_mode)
    store.set_data_dir(Path(os.getenv("HAZARD_DATA_DIR", "data/hazards")))
    report_log.reset_log()


@pytest.fixture(autouse=True)
def fresh_hazard_state():
    """Each test starts with no injected test hazards, empty caches and no cached hazard picture."""
    try:
        from backend.hazards import picture
        from backend.hazards.cache import clear_all_caches
        from backend.hazards.inject import clear_injected
    except ImportError:
        yield
        return

    def reset():
        clear_injected()
        clear_all_caches()
        with picture._latest_lock:
            picture._latest = None      # C1's cached picture: never leak one test's into the next
    reset()
    yield
    reset()


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class StepClock:
    """A replay clock that only moves when advance() is called. Same interface as VirtualClock."""

    def __init__(self, start: float = 1_792_000_000.0, speed: float = 10.0):
        self.t = float(start)
        self.speed = float(speed)          # only reported to the UI; time moves by advance()

    def now(self) -> float:
        return self.t

    def set_speed(self, speed: float) -> None:
        self.speed = float(speed)

    def advance(self, seconds: float) -> None:
        self.t += seconds


class RecordingBand(BandOutput):
    """Pretends to be a real band link. Remembers every (worker, line) it was sent."""

    physical = True

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, worker_id: str, line: str) -> None:
        self.sent.append((worker_id, line))

    def lines_for(self, worker_id: str) -> list[str]:
        return [line for w, line in self.sent if w == worker_id]


def make_spy_phone(clock):
    """
    Session D's real DryRunPhone (the 911 guard runs first, then nothing is dialed),
    allowed to "call" only our fake numbers. Every number it was asked for is recorded,
    and so is every number that got past the guard to the dial step.
    """
    from backend.emergency.phones import DryRunPhone

    class SpyPhone(DryRunPhone):
        def __init__(self):
            super().__init__(allowlist=set(TEST_NUMBERS.values()), clock=clock, printer=lambda text: None)
            self.asked: list[str] = []         # every number passed to call()
            self.dialed: list[str] = []        # numbers that passed the guard

        def call(self, number, script, report, on_status=None):
            self.asked.append(number)
            return super().call(number, script, report, on_status)

        def _dial(self, number, script, report, on_status):
            self.dialed.append(number)
            return super()._dial(number, script, report, on_status)

    return SpyPhone()


# ---------------------------------------------------------------------------
# The running app
# ---------------------------------------------------------------------------
class LiveApp:
    """The real app in-process, plus helpers to drive it."""

    def __init__(self, client: TestClient, clock: StepClock):
        self.client = client
        self.clock = clock
        self.app = main.app
        self.hub = main.app.state.hub
        self.frontend = main.app.state.frontend
        self.band = RecordingBand()
        self.hub.band.add(self.band)
        self.events: list[dict] = []                 # every raw hub event, in order
        self.hub.add_listener(self.events.append)
        self.emergency = getattr(main.app.state, "emergency", None)
        self.phone = None
        if self.emergency is not None:
            # Session D's manager: our clock (replay time), our fake numbers, a spy phone, no log file.
            from backend.emergency.escalation import EscalationLog
            self.phone = make_spy_phone(clock.now)
            self.emergency.clock = clock.now
            self.emergency.phone = self.phone
            self.emergency.contacts = dict(TEST_NUMBERS)
            self.emergency.log = EscalationLog(None)
        self.replay = None
        self._marker = 0

    # -- running code on the app's event loop (the hub is not thread-safe) ----------
    def on_loop(self, fn, *args):
        return self.client.portal.call(fn, *args)

    # -- replay with a hand-driven clock -------------------------------------------
    def start_replay(self, plan) -> None:
        """Like hub.start_replay(), but time only moves in step(). Pre-roll plays right away."""
        from backend.replay.replay import Replay

        def start():
            self.hub.stop_replay()
            self.replay = Replay(plan, self.hub, clock=self.clock)
            self.hub.replay = self.replay
            self.replay.register()
            self.replay.prerolling = True
            self.replay.emit_due(self.replay.start)
            self.replay.prerolling = False
            self.hub.preroll_done(list(self.replay.workers))
            self.hub._replay_state()
        self.on_loop(start)

    def step(self, seconds: float, dt: float = 1.0, until=None) -> float:
        """
        Move time forward `seconds` in steps of `dt`: replay items come due, then the
        hub's and the emergency manager's timers run, exactly like the real loop does.
        Stops early (returns the time moved) once until() is true.
        """
        moved = 0.0

        def one_step():
            self.clock.advance(dt)
            if self.replay is not None:
                self.replay.emit_due(self.clock.now())
            self.hub.tick()
            if self.emergency is not None:
                self.emergency.tick()

        while moved < seconds:
            self.on_loop(one_step)
            moved += dt
            if until is not None and until():
                break
        return moved

    def offset(self) -> float:
        """Replay seconds since the replay started."""
        return self.clock.now() - self.replay.start

    # -- reading what was sent ------------------------------------------------------
    def of_type(self, kind: str, worker_id: str | None = None, live_only: bool = True) -> list[dict]:
        return [e for e in self.events if e["type"] == kind
                and (worker_id is None or e.get("worker_id") == worker_id)
                and not (live_only and e.get("preroll"))]

    def drain(self, ws, raw: bool = False) -> list[dict]:
        """Every message waiting on a /ws/live WebSocket (or the raw /ws): sends a marker, reads up to it."""
        self._marker += 1
        marker = {"type": "e2e_marker", "n": self._marker}
        self.on_loop(self.hub.broadcast if raw else self.frontend.broadcast, marker)
        out = []
        while True:
            msg = ws.receive_json()
            if msg == marker:
                return out
            out.append(msg)


def settings(tmp_path, *extra: str):
    """Safe server settings: no DB file outside tmp, no call log, no hazards/planner unless asked."""
    args = ["--source", "none", "--db", str(tmp_path / "e2e.db"), "--emergency-log", ""]
    if "--hazards" not in extra:
        args += ["--hazards", "off"]
    if "--planner" in extra:
        extra = tuple(a for a in extra if a != "--planner")
    else:
        args += ["--no-planner"]
    if "--checkpoint" not in extra:
        args += ["--no-forecaster"]          # Kalman-only unless a forecaster file is given
    return main.parse_args(args + list(extra))


class AppFactory:
    """
    Starts the real app with safe settings, as many times as a test wants, and shuts
    every one down in close(). Used by the live_app fixture (and by module-wide fixtures).
    """

    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path, self.mp = tmp_path, monkeypatch
        self.clients: list[TestClient] = []
        # Pin the emergency config to fake numbers, whatever the developer's .env says.
        for role, attr in (("crew_lead", "CREW_LEAD_NUMBER"), ("safety_officer", "SAFETY_OFFICER_NUMBER"),
                           ("emergency", "EMERGENCY_TEST_NUMBER")):
            monkeypatch.setattr(config, attr, TEST_NUMBERS[role])
        monkeypatch.setattr(config, "EMERGENCY_LIVE_CALLS", False)

    def start(self, *extra: str, clock: StepClock | None = None) -> LiveApp:
        self.mp.setattr(main, "SETTINGS", settings(self.tmp_path, *extra))
        client = TestClient(main.app)
        client.__enter__()
        self.clients.append(client)
        app = LiveApp(client, clock or StepClock())
        if app.hub.why is not None:
            app.hub.why = None        # session H's why-lines run a second model copy: too heavy here
        return app

    __call__ = start                  # live_app(...) starts an app

    def stop(self, app: LiveApp) -> None:
        """Shut one app down (e.g. to restart the server on the same database)."""
        app.client.__exit__(None, None, None)
        self.clients.remove(app.client)

    def close(self) -> None:
        for client in reversed(self.clients):
            client.__exit__(None, None, None)
        self.clients.clear()


@pytest.fixture
def live_app(tmp_path, monkeypatch):
    """
    Factory: live_app("--checkpoint", path, ...) starts the real app and returns a LiveApp.
    live_app.stop(app) shuts one down early (restart tests). The rest shut down when the test ends.
    """
    factory = AppFactory(tmp_path, monkeypatch)
    yield factory
    factory.close()
