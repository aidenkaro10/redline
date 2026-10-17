"""Session B's planner plugged into the live system: schedule, breaks, routes."""

import asyncio

import pytest

from backend.alerts.band_output import MultiBand
from backend.app.hub import Hub
from backend.app.planner_bridge import schedule_status
from backend.app.schemas import Reading

STEP = 30 * 60


def sched(tasks, start=1000.0, wid="w1"):
    return {"slot_minutes": 30, "day_start": start,
            "rows": [{"worker_id": wid, "slots": [{"start": start + i * STEP, "task": t} for i, t in enumerate(tasks)]}]}


def test_schedule_status_next_break_and_on_break():
    s = sched(["heavy", "light", "break", "stop_work", "heavy"])
    assert schedule_status(s, "w1", 1000.0 + 10) == (False, 1000.0 + 2 * STEP)            # next break
    assert schedule_status(s, "w1", 1000.0 + 2 * STEP + 5) == (True, 1000.0 + 4 * STEP)  # break + stop work, ends at slot 4
    assert schedule_status(s, "w1", 1000.0 + 4 * STEP + 5) == (False, None)              # no more breaks
    assert schedule_status(s, "nobody", 1000.0) == (False, None)
    assert schedule_status(None, "w1", 1000.0) == (False, None)


def test_planner_bridge_plans_for_our_crew(monkeypatch):
    pytest.importorskip("backend.planner.api")
    monkeypatch.setenv("PLANNER_LLM", "off")                 # never call the LLM in tests
    from backend.app.frontend_api import FrontendAdapter
    from backend.app.planner_bridge import PlannerBridge
    from backend.planner import api

    hub = Hub(band=MultiBand([]))
    for wid in ("demo-1", "demo-2"):
        hub.register_worker(wid, name=wid.upper())
        hub.handle_input(Reading(worker_id=wid, timestamp=hub.now(wid), hr=110))
    hub.pipelines["demo-1"].offset.misses = [0.2, 0.2]       # a personalized worker
    got = []
    hub.add_listener(lambda e: e["type"] == "schedule" and got.append(e))
    bridge = PlannerBridge(hub)
    built_at = hub.now("demo-1")                 # planning can take minutes on a busy laptop
    asyncio.run(bridge.step())

    assert got, "schedule event pushed"
    schedule = hub.schedule
    assert [r["worker_id"] for r in schedule["rows"]] == ["demo-1", "demo-2"]
    assert schedule["checked_by_rules"] is True
    assert api.get_schedule()["rows"][0]["worker_id"] == "demo-1"     # the planner's own route agrees
    first = schedule["rows"][0]["slots"][0]["start"]
    assert first <= built_at < first + STEP                           # the shift starts when it was planned
    st = FrontendAdapter(hub).worker_status("demo-1")
    assert isinstance(st["on_break"], bool) and (st["break_at"] is None or st["break_at"] > first)
    # Same crew again: no replan.
    seen = bridge.result_seen
    asyncio.run(bridge.step())
    assert bridge.result_seen is seen
