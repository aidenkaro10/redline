"""
7b. Planner safety: every schedule the planner endpoints return has passed the code rule checker.
    The LLM can propose, never approve (and in tests it's off: PLANNER_LLM=off, no network).

We use a small 3-hour, 2-person version of the planner's demo day (the full day takes minutes
on a busy laptop) with a lightning window in the middle, call the real HTTP endpoints, and
re-check what comes back with the rule checker ourselves.

Plus: the planner's heat model must survive two threads at once (the server runs planner work
on several threads). Run in a separate process, since that crash aborts Python itself.
"""

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import contract_schemas as C
from e2e_support import FIXTURES

pytest.importorskip("backend.planner.api", reason="session B's planner isn't merged")
from backend.planner import api as planner_api  # noqa: E402
from backend.planner.generate import demo_scenario  # noqa: E402
from backend.planner.rules import check_plan  # noqa: E402
from backend.planner.scenario import HazardWindow, Plan, Scenario  # noqa: E402

LIGHTNING_SLOTS = (2, 3)          # lightning 08:00-08:30 -> stop work through the 30-min all-clear


BLACK_FLAG = (37.0, 50.0, 32.5)   # air °C, humidity %, WBGT °C: extreme but survivable in the shade
IMPOSSIBLE = (39.0, 55.0, 34.0)   # even resting in the shade breaks the heat rule in B's model


def small_scenario(weather: tuple | None = None) -> Scenario:
    """The demo day cut to 6 slots and 2 workers, with a lightning window. weather = a hot day, all very heavy work."""
    sc = demo_scenario()
    sc.id = "e2e-small"
    sc.weather = sc.weather[:6]
    sc.crew = sc.crew[:2]
    sc.tasks = [dataclasses.replace(t, units=min(t.units, 2), earliest=0, deadline=None, skill=None)
                for t in sc.tasks[:3]]
    sc.hazards = [HazardWindow("lightning", *LIGHTNING_SLOTS, source="e2e test")]
    if weather:
        tdb, rh, wbgt = weather
        sc.weather = [dataclasses.replace(w, tdb=tdb, rh=rh, solar=900.0, wbgt=wbgt) for w in sc.weather]
        sc.tasks = [dataclasses.replace(t, intensity="very_heavy", priority=1) for t in sc.tasks]
        sc.crew = [dataclasses.replace(w, acclim=0.0) for w in sc.crew]
    return sc


@pytest.fixture
def planner_app(live_app):
    """The app with B's routes mounted (no planner bridge: one planning thread at a time)."""
    saved = dict(planner_api._state)
    planner_api._state.update(scenario=None, result=None, briefings={}, chats={})
    app = live_app()
    yield app
    planner_api._state.clear()
    planner_api._state.update(saved)


def plan_via_http(app, sc: Scenario) -> dict:
    r = app.client.post("/api/planner/plan", json={"scenario": sc.to_dict(), "use_llm": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_A26_A27_planner_returns_only_rule_checked_plans(planner_app):
    app = planner_app
    sc = small_scenario()
    out = plan_via_http(app, sc)                     # use_llm asked, but PLANNER_LLM=off: no LLM call
    assert out["plan"]["verified"] is True and out["plan"]["violations"] == []
    # Re-check the exact plan the server holds with the rule checker, independently.
    plan = planner_api._state["result"].plan
    assert check_plan(sc, plan).ok
    # And over HTTP, from its JSON alone.
    assignments = {w["id"]: [s["task"] for s in w["slots"]] for w in out["plan"]["workers"]}
    check = app.client.post("/api/planner/check", json={"scenario": sc.to_dict(), "plan": {"assignments": assignments}})
    assert check.json()["ok"] is True
    # No OUTDOOR work during the lightning window + its 30-min all-clear (indoor work is allowed).
    outdoor = {t.id for t in sc.tasks if t.outdoor}
    for w in out["plan"]["workers"]:
        for s in w["slots"]:
            if s["slot"] in sc.stop_work_slots():
                assert s["task"] not in outdoor, f"{w['id']} works outdoors in stop-work slot {s['slot']}"
    # Every candidate the agent offered as the answer was verified too.
    assert all(c["verified"] for c in out["alternatives"] if "verified" in c)


def test_A26_schedule_endpoint_serves_the_checked_plan(planner_app):
    app = planner_app
    sc = small_scenario()
    plan_via_http(app, sc)
    r = app.client.get("/api/schedule")
    assert r.status_code == 200
    schedule = r.json()
    assert not C.check(schedule, C.SCHEDULE, "schedule")
    assert schedule["checked_by_rules"] is True
    blocked = sc.stop_work_slots()
    indoor = {t.name for t in sc.tasks if not t.outdoor}
    for row in schedule["rows"]:
        for k in blocked:
            slot = row["slots"][k]
            assert slot["task"] == "stop_work" or slot["label"].removesuffix(" (indoors)") in indoor, slot
    if os.getenv("E2E_WRITE_FIXTURES") == "1":
        (FIXTURES / "rest" / "get_api_schedule.json").write_text(
            json.dumps({"request": "GET /api/schedule", "status": 200, "body": schedule}, indent=1) + "\n")


def test_A26_a_black_flag_day_still_gets_a_verified_plan(planner_app):
    """WBGT 32.5 °C, only very heavy work, new hires: the answer must still pass the checker."""
    app = planner_app
    sc = small_scenario(BLACK_FLAG)
    out = plan_via_http(app, sc)
    assert out["plan"]["verified"] is True
    assert check_plan(sc, planner_api._state["result"].plan).ok


def test_A26_planner_never_claims_an_unverified_plan_is_checked(planner_app):
    """
    A day so hot that nothing passes (not even rest): the planner must SAY so, not pretend.
    It falls back to "everyone rests" with checked_by_rules false and a "No verified plan" note.
    """
    app = planner_app
    sc = small_scenario(IMPOSSIBLE)
    out = plan_via_http(app, sc)
    assert out["plan"]["verified"] is False and out["plan"]["violations"]
    schedule = app.client.get("/api/schedule").json()
    assert schedule["checked_by_rules"] is False
    assert any("No verified plan" in n for n in schedule["notes"])
    assert {s["task"] for row in schedule["rows"] for s in row["slots"]} <= {"break", "stop_work", "off"}


def test_A26_the_check_endpoint_rejects_an_unsafe_plan(planner_app):
    """What an LLM might propose: very heavy work all day, straight through the lightning."""
    app = planner_app
    sc = small_scenario(BLACK_FLAG)
    outdoor = next(t for t in sc.tasks if t.outdoor)
    unsafe = {"assignments": {w.id: [outdoor.id] * sc.n_slots for w in sc.crew}}
    r = app.client.post("/api/planner/check", json={"scenario": sc.to_dict(), "plan": unsafe}).json()
    assert r["ok"] is False and r["n_safety_violations"] > 0
    rules = {v["rule"] for v in r["violations"]}
    assert {"heat", "breaks"} <= rules and rules & {"lightning", "hazard"}, rules
    assert not check_plan(sc, Plan.from_dict(unsafe)).ok


@pytest.fixture(scope="module")
def no_key_briefing(tmp_path_factory):
    """GET /api/briefing once with NO API key (slow: ~30 s offline), shared by the A27 tests."""
    from e2e_support import AppFactory
    mp = pytest.MonkeyPatch()
    mp.delenv("ANTHROPIC_API_KEY", raising=False)
    saved = dict(planner_api._state)
    planner_api._state.update(scenario=None, result=None, briefings={}, chats={})
    factory = AppFactory(tmp_path_factory.mktemp("briefing"), mp)
    app = factory.start()
    try:
        r = app.client.get("/api/briefing")
        chat = app.client.post("/api/planner/chat", json={"message": "Plan tomorrow"})
        yield r, chat
    finally:
        factory.close()
        planner_api._state.clear()
        planner_api._state.update(saved)
        mp.undo()


def test_A27_no_key_briefing_and_chat_still_work(no_key_briefing):
    r, chat = no_key_briefing
    assert r.status_code == 200
    b = r.json()
    assert not C.check(b, C.BRIEFING, "briefing")
    assert b["cost_usd"] == 0 and b["sites"]
    assert chat.status_code == 200 and chat.json()["llm"] is False      # chat says it ran without the LLM
    if os.getenv("E2E_WRITE_FIXTURES") == "1":
        (FIXTURES / "rest" / "get_api_briefing.json").write_text(
            json.dumps({"request": "GET /api/briefing", "status": 200, "body": b}, indent=1) + "\n")


@pytest.mark.xfail(strict=True, reason="gap -> session B: /api/briefing (and /api/schedule) don't say they were "
                   "made without the LLM; only chat has an 'llm' flag. Add e.g. llm: false or a 'no-LLM' note.")
def test_A27_no_key_briefing_is_labeled_no_llm(no_key_briefing):
    b = no_key_briefing[0].json()
    text = json.dumps({k: b.get(k) for k in ("summary", "stubs", "notes", "llm", "plan")}).lower()
    assert b.get("llm") is False or "no-llm" in text or "without the llm" in text or "no llm" in text


def test_A28_what_ifs_never_change_the_live_scenario(planner_app):
    from backend.planner.agent import PlannerSession
    app = planner_app
    sc = small_scenario()
    plan_via_http(app, sc)                           # the live scenario + plan
    live = planner_api._state["scenario"]
    before, live_plan = live.to_dict(), planner_api._state["result"].plan.assignments
    session = PlannerSession(live)
    session.run_tool("run_optimizer", {"what_if": {"remove_workers": [live.crew[0].id], "hotter_c": 3,
                                                   "add_hazard": {"kind": "lightning", "start_slot": 0, "end_slot": 2}}})
    unsafe = {w.id: [sc.tasks[1].id] * sc.n_slots for w in sc.crew}
    session.run_tool("simulate_schedule", {"assignments": unsafe})
    assert live.to_dict() == before
    assert planner_api._state["scenario"] is live and planner_api._state["result"].plan.assignments == live_plan
    assert session.plan.assignments != unsafe                          # a simulated schedule never becomes the plan


# ---------------------------------------------------------------------------
# The planner's heat model on two threads at once
# ---------------------------------------------------------------------------
TWO_THREADS = textwrap.dedent("""
    # Two threads run the planner's rule checker at the same moment, like the server does when
    # planner_bridge's startup plan overlaps the briefing pre-build, or two /api/planner/* or
    # /api/schedule requests arrive together (FastAPI runs those on a thread pool).
    import os, sys, threading
    os.environ["PLANNER_LLM"] = "off"
    from backend.planner.generate import demo_scenario
    from backend.planner.rules import check_plan
    from backend.planner.scenario import Plan
    sc = demo_scenario()
    plan = Plan({w.id: ["rest"] * sc.n_slots for w in sc.crew})
    check_plan(sc, plan)                       # warm up first: only the overlap is tested
    barrier = threading.Barrier(2)
    def work():
        barrier.wait()
        for _ in range(3):
            check_plan(sc, plan)
    threads = [threading.Thread(target=work) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    print("alive", flush=True)
""")


@pytest.mark.xfail(strict=True, reason="BUG (session B heat model, hit by session A's server): pythermalcomfort's "
                   "PHS model uses numba parallel=True; two threads running it at once make numba's workqueue layer "
                   "ABORT the whole process ('Concurrent access has been detected', exit 134). The server does this "
                   "at startup (planner_bridge plan_now + warm_briefing, seen offline) and on overlapping planner "
                   "requests. Fix idea: one threading.Lock around the heat model's simulate().")
def test_planner_heat_model_survives_two_threads(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(repo), "OMP_NUM_THREADS": "2"}
    p = subprocess.run([sys.executable, "-c", TWO_THREADS], cwd=repo, env=env, capture_output=True, text=True,
                       timeout=600)
    assert p.returncode == 0, f"process died (exit {p.returncode}): " + " ".join(
        line for line in p.stderr.splitlines() if "Concurrent" in line)
    assert "alive" in p.stdout
