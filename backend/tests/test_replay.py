"""Replay + hub end to end, driven by a hand-cranked clock (no waiting)."""

import asyncio
from pathlib import Path

import pytest

from backend import config
from backend.alerts.band_output import BandOutput, MultiBand
from backend.app.hub import Hub
from backend.replay import shifts
from backend.replay.clock import VirtualClock
from backend.replay.replay import Replay


class ManualClock:
    speed = 1.0

    def __init__(self, t=1_000_000.0):
        self.t = t

    def now(self):
        return self.t

    def set_speed(self, s):
        self.speed = s


class Recorder(BandOutput):
    def __init__(self):
        self.lines = []

    def send(self, worker_id, line):
        self.lines.append((worker_id, line))


def run_replay(plan, demo=False, minutes=None, step=5.0):
    band = Recorder()
    hub = Hub(band=MultiBand([band]), demo=demo)
    events = []
    hub.add_listener(events.append)
    clock = ManualClock()
    replay = Replay(plan, hub, clock=clock)
    hub.replay = replay
    replay.register()
    replay.emit_due(clock.now())                     # pre-roll
    end = replay.start + (minutes * 60 if minutes else max(i.offset_s for i in plan.items) + 1)
    while clock.t < end:
        clock.t += step
        replay.emit_due(clock.t)
        hub.tick()
    return hub, band.lines, events


def test_synthetic_replay_full_demo_story():
    plan = shifts.synthetic(minutes=90, fall_worker=1, fall_at_min=40, fall_for_min=5)
    hub, lines, events = run_replay(plan)
    jake = [l for w, l in lines if w == "demo-1"]
    assert jake[:2] == ["BUZZ:HEAT_YELLOW", "BUZZ:HEAT_RED"]
    # Jake auto-acknowledges the red alert, so no red-unacknowledged man-down for him.
    assert any(e["type"] == "heat_ack" and e["worker_id"] == "demo-1" for e in events)
    assert not any(e["type"] == "mandown_check" and e["worker_id"] == "demo-1" for e in events)
    # Maria's scripted fall -> MANDOWN on her band, then confirmed with crewmates listed.
    maria = [l for w, l in lines if w == "demo-2"]
    assert maria[0] == "BUZZ:MANDOWN"
    conf = [e for e in events if e["type"] == "mandown_confirmed"]
    assert len(conf) == 1 and conf[0]["worker_id"] == "demo-2" and conf[0]["reason"] == "fall"
    assert set(conf[0]["crewmates"]) == {"demo-1", "demo-3"}
    assert conf[0]["worker_name"] == "Maria" and conf[0]["location"]["lat"] == config.JOB_SITE_LAT
    # Events are in time order around the fall.
    kinds = [e["type"] for e in events if e.get("worker_id") == "demo-2" and e["type"].startswith("mandown")]
    assert kinds == ["mandown_check", "mandown_confirmed"]


def test_no_ack_means_red_unacknowledged_triggers_check():
    plan = shifts.synthetic(minutes=90, fall_worker=None, auto_ack_s=None, n_crew=0)
    # The worker keeps walking (replay readings carry activity), but movement doesn't end a
    # "red, no button press" check: only the button does. So it escalates.
    hub, lines, events = run_replay(plan)
    checks = [e for e in events if e["type"] == "mandown_check"]
    assert checks and checks[0]["reason"] == "heat_red_unacknowledged"
    assert not any(e["type"] == "mandown_cancelled" for e in events)
    conf = [e for e in events if e["type"] == "mandown_confirmed"]
    assert conf and conf[0]["trigger"] == "heat_red_unacked"


def test_injected_fall_on_replay_worker():
    plan = shifts.synthetic(minutes=10, fall_worker=None, n_crew=1)
    band = Recorder()
    hub = Hub(band=MultiBand([band]))
    clock = ManualClock()
    replay = Replay(plan, hub, clock=clock)
    hub.replay = replay
    replay.register()
    clock.t += 60
    replay.emit_due(clock.t)
    hub.fake_fall("demo-2", duration_s=200)
    for _ in range(40):
        clock.t += 5
        replay.emit_due(clock.t)
        hub.tick()
    assert ("demo-2", "BUZZ:MANDOWN") in band.lines


def test_jsonl_replay_roundtrip(tmp_path):
    f = tmp_path / "rec.jsonl"
    f.write_text(
        '{"type": "reading", "worker_id": "w9", "timestamp": 100.0, "hr": 120, "tilt": 0, "activity": 3}\n'
        '{"type": "button", "worker_id": "w9", "timestamp": 130.0}\n'
        '{"type": "reading", "worker_id": "w9", "timestamp": 165.0, "hr": 125}\n')
    plan = shifts.from_jsonl(f)
    assert [i.offset_s for i in plan.items] == [0.0, 30.0, 65.0]
    hub, lines, events = run_replay(plan, minutes=2)
    assert hub.pipelines["w9"].signals["hr"][:2] == [120.0, 125.0]   # one value per minute
    assert any(e["type"] == "button" for e in events)


def test_virtual_clock_speed_change_does_not_jump():
    c = VirtualClock(speed=10, start=0.0)
    before = c.now()
    c.set_speed(100)
    assert abs(c.now() - before) < 0.5


def test_async_run_with_speed_up():
    plan = shifts.synthetic(minutes=3, fall_worker=None, n_crew=0)
    hub = Hub(band=MultiBand([Recorder()]))

    async def go():
        replay = Replay(plan, hub, speed=600)          # 3 minutes in ~0.3 s
        hub.start_replay(replay)
        await asyncio.wait_for(hub.replay_task, timeout=5)
        return replay

    replay = asyncio.run(go())
    assert replay.finished
    assert len(hub.pipelines["demo-1"].signals["hr"]) >= 2


@pytest.mark.skipif(not shifts.SIM_PATH.exists(), reason="data/sim/sim.npz not in this checkout")
def test_sim_plan_preroll():
    plan = shifts.from_sim()
    assert plan.workers[0].meta["first_minute"] > 0
    assert min(i.offset_s for i in plan.items) < 0             # pre-roll items exist
    assert len(plan.workers) == 1 + len(shifts.CREW_SHIFTS)


def test_preroll_survives_concurrent_ticks():
    """The server's tick loop runs during pre-roll; history must stay one row per minute."""
    import numpy as np
    sig = {"hr": np.full(40, 150.0), "activity": np.full(40, 5.0)}
    plan = shifts.ReplayPlan([shifts.ReplayWorker("demo-1", "Jake")],
                             sorted(shifts._minutes_to_items("demo-1", sig, first_minute=35)))
    band = Recorder()
    hub = Hub(band=MultiBand([band]))

    async def go():
        async def ticker():
            while True:
                hub.tick()
                await asyncio.sleep(0)
        t = asyncio.create_task(ticker())
        replay = Replay(plan, hub, speed=1)
        hub.start_replay(replay)
        while replay.prerolling or replay.start is None or not hub.pipelines["demo-1"].estimates:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        t.cancel()
        hub.stop_replay()

    hub_minutes = []
    hub.add_listener(lambda e: e["type"] == "estimate" and hub_minutes.append(e["minute"]))
    asyncio.run(go())
    assert hub_minutes[:34] == list(range(34))
    # Band commands from pre-roll history are held back, then the current state is sent once.
    assert band.lines.count(("demo-1", "BUZZ:HEAT_RED")) <= 1
