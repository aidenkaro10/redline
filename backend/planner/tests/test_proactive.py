"""Tests for acting on forecasts, storm-aware scheduling and notifications. No network, no real texts."""

import datetime as dt

import pytest

from backend import config
from backend.planner.generate import demo_scenario
from backend.planner.notify import Feed, Sms
from backend.planner.optimizer import OBJECTIVES, optimize
from backend.planner.proactive import (TZ, ProactiveLoop, propose, pushes_work_later, slot_at)
from backend.planner.scenario import REST, Plan, Scenario, SlotWeather, Task, Worker
from backend.planner.storm_risk import add_storm_probs, storm_reason, watch_slots

LEAD = "+13525550100"


@pytest.fixture(scope="module")
def demo_plan():
    sc = demo_scenario()
    return sc, optimize(sc, "throughput", deterministic=True).plan


def ts_of(sc, slot, minute=0):
    h, m = map(int, sc.shift_start.split(":"))
    d = dt.date.fromisoformat(sc.date)
    return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp() + (slot * sc.slot_min + minute) * 60


def estimate(sc, wid, slot, p90_20, minute=0, **extra):
    """An estimate event shaped like session A's pipeline output."""
    return {"type": "estimate", "worker_id": wid, "timestamp": ts_of(sc, slot, minute), "core_temp": 37.5,
            "forecast": {"minutes": list(range(1, 31)), "p90": [p90_20] * 30}, **extra}


def busiest(sc, plan, slot):
    """A worker doing the hardest work in that slot."""
    order = {REST: 0, "light": 1, "moderate": 2, "heavy": 3, "very_heavy": 4}
    return max(sc.crew, key=lambda w: order[REST if plan.assignments[w.id][slot] == REST
                                            else sc.task(plan.assignments[w.id][slot]).intensity]).id


def test_slot_at(demo_plan):
    sc, _ = demo_plan
    assert slot_at(sc, ts_of(sc, 3, 10)) == (3, pytest.approx(10))


def test_pushes_work_later(demo_plan):
    sc, plan = demo_plan
    wid = next(w for w, row in plan.assignments.items() if row[0] != REST)
    later = Plan({k: list(v) for k, v in plan.assignments.items()})
    j = next(k for k in range(1, sc.n_slots) if later.assignments[wid][k] == REST)
    later.assignments[wid][0], later.assignments[wid][j] = REST, plan.assignments[wid][0]
    assert pushes_work_later(sc, plan, later) and not pushes_work_later(sc, plan, plan)


def test_proposals_are_safer_and_classified(demo_plan):
    sc, plan = demo_plan
    s = 4
    wid = busiest(sc, plan, s)
    core = {w.id: 37.4 for w in sc.crew}
    changes = propose(sc, plan, wid, s, {**core, wid: 37.75}, forecasts={wid: 37.95})
    assert changes, "expected at least one verified change for a hot worker on heavy work"
    for c in changes:
        assert c.risk_after < c.risk_before                      # safer for the hot worker
        assert c.auto == (c.throughput_delta >= 0 and not c.pushes_later)
        assert "°C" not in c.message                             # no body numbers in crew-lead text
        assert c.plan.assignments[wid][s] != plan.assignments[wid][s]


def test_live_loop_rate_limits_and_ignores_other_events(demo_plan):
    sc, plan = demo_plan
    state = {"plan": plan}
    feed = Feed(sms=Sms(to=LEAD), path=None)
    loop = ProactiveLoop(lambda: (sc, state["plan"]), lambda p: state.update(plan=p) or True, feed, asynchronous=False)
    s = 4
    wid = busiest(sc, plan, s)
    loop.on_event({"type": "alert", "worker_id": wid, "kind": "HEAT_RED"})           # alerts: ignored
    loop.on_event(estimate(sc, wid, s, config.PLAN_EARLY_LINE_C - 0.2))               # below the early line
    loop.on_event(estimate(sc, wid, s, 38.2, preroll=True))                           # replayed history
    assert feed.list() == []
    loop.on_event(estimate(sc, wid, s, 38.2))
    events = feed.list()
    assert len(events) == 1 and events[0]["status"] in ("applied", "proposed")
    assert events[0]["type"] == "planner_change" and events[0]["checked_by_rules"]
    if events[0]["status"] == "applied":
        assert state["plan"] is not plan
    loop.on_event(estimate(sc, wid, s, 38.3, minute=5))                               # cooldown
    assert len(feed.list()) == 1


def test_proposal_approve_reject_expire(demo_plan):
    sc, plan = demo_plan
    s = 4
    wid = busiest(sc, plan, s)
    state = {"plan": plan}
    loop = ProactiveLoop(lambda: (sc, state["plan"]), lambda p: state.update(plan=p) or True,
                         Feed(sms=Sms(to=LEAD), path=None), asynchronous=False)
    changes = propose(sc, plan, wid, s, {w.id: 37.4 for w in sc.crew}, forecasts={wid: 38.2})
    proposal = next((c for c in changes if not c.auto), None)
    if proposal is None:
        pytest.skip("no proposal-type change in this plan")
    for decision in (True, False):
        ev = loop.event_for(sc, proposal, 38.1, ts_of(sc, s))
        ev["status"] = "proposed"
        loop.pending[ev["id"]] = {"event": ev, "change": proposal, "scenario_id": sc.id}
        out = loop.decide(ev["id"], approve=decision)
        assert out["status"] in (("approved", "rejected_by_rules") if decision else ("rejected",))
    with pytest.raises(KeyError):
        loop.decide("nope", True)
    ev = loop.event_for(sc, proposal, 38.1, ts_of(sc, s))
    loop.pending[ev["id"]] = {"event": {**ev, "status": "proposed"}, "change": proposal, "scenario_id": sc.id}
    loop.expire(ts_of(sc, s) + (config.PLAN_PROPOSAL_EXPIRE_MIN + 1) * 60)
    assert loop.proposals() == [] and loop.feed.list()[0]["status"] == "expired"


def test_sms_guard_dry_run_and_rate_limit(monkeypatch):
    monkeypatch.setattr(config, "CREW_LEAD_NUMBER", LEAD)
    clock = {"t": 1000.0}
    sms = Sms(mode="dry_run", clock=lambda: clock["t"])
    first = sms.send("Added a break for Jake at 1:30 PM.")
    assert first["sent"] and first.get("dry_run") and first["to"].endswith("0100")
    assert not sms.send("again")["sent"]                                   # too soon
    clock["t"] += config.PLAN_SMS_MIN_GAP_S + 1
    assert sms.send("later")["sent"]
    assert "emergency" in Sms(to="911").send("x")["error"]                 # D's 911 guard
    assert "allowlist" in Sms(to="+13525559999").send("x")["error"]
    sent = []
    live = Sms(mode="twilio", sender=lambda n, t: sent.append((n, t)), clock=lambda: 5.0)
    assert live.send("hello")["sent"] and sent == [(LEAD, "hello")]


def storm_scenario(prob_late: float) -> Scenario:
    weather = [SlotWeather(tdb=27, rh=60, solar=500, wind=2, wbgt=24,
                           storm_prob=prob_late if k >= 4 else 0.0) for k in range(8)]
    return Scenario(id="storm-test", site={"id": "x", "name": "X", "lat": 29.7, "lon": -82.4}, date="2026-07-15",
                    shift_start="07:00", weather=weather,
                    crew=[Worker(id="w1", name="Sam", skills=["climbing"]), Worker(id="w2", name="Ana")],
                    tasks=[Task(id="t1", name="Scaffold setup", intensity="light", units=2, skill="climbing", elevated=True),
                           Task(id="t2", name="Control room checks", intensity="light", units=8, outdoor=False)])


def test_storm_chance_moves_elevated_work_earlier():
    sc = storm_scenario(0.6)
    plan = optimize(sc, OBJECTIVES["throughput"]).plan
    slots = [k for k, a in enumerate(plan.assignments["w1"]) if a == "t1"]
    assert len(slots) == 2 and max(slots) < 4
    reason = storm_reason(sc, plan)
    assert reason.startswith("60% storms 9 AM-11 AM") and "Scaffold setup at 7 AM" in reason
    assert storm_reason(storm_scenario(0.0), plan) is None


def test_storm_probs_fake_and_watch(monkeypatch):
    sc = demo_scenario()
    monkeypatch.setenv("PLANNER_STORM_MODE", "fake")
    assert add_storm_probs(sc) == "fake"
    by_time = {sc.slot_time(k): w.storm_prob for k, w in enumerate(sc.weather)}
    assert by_time["14:00"] == 0.6 and by_time["13:00"] == 0.3 and by_time["09:00"] == 0.0
    t = lambda h: dt.datetime(2026, 7, 15, h, tzinfo=TZ).astimezone(dt.timezone.utc).isoformat()
    watch = [{"title": "Severe Thunderstorm Watch", "starts": t(10), "ends": t(12)},
             {"title": "Heat Advisory", "starts": t(7), "ends": t(15)}]
    assert watch_slots(sc, watch) == {6, 7, 8, 9}
    monkeypatch.setenv("PLANNER_STORM_MODE", "off")
    assert add_storm_probs(sc, watch) == "nws" and sc.weather[6].storm_prob == 0.7


def test_feed_endpoints():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.planner.api import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    assert isinstance(c.get("/api/planner/feed").json(), list)
    assert c.post("/api/planner/proposals/chg-x/approve").status_code == 404
    assert c.post("/api/planner/proposals/chg-x/maybe").status_code == 400
