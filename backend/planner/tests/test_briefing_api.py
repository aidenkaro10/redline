"""Tests for the chat agent's tools, the 6 PM briefing and the HTTP endpoints (no API key needed)."""

import os

import pytest

os.environ["PLANNER_LLM"] = "off"      # these tests never call the real API

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.planner.agent import PlannerSession, apply_what_if
from backend.planner.api import router
from backend.planner.briefing import make_briefing, render_text
from backend.planner.generate import demo_scenario


@pytest.fixture(scope="module")
def demo():
    return demo_scenario()


def test_agent_tools_and_what_if(demo):
    s = PlannerSession(demo)
    f = s.run_tool("get_forecast", {})
    assert len(f["slots"]) == demo.n_slots and f["stop_work_slots"]
    assert "stub" in s.run_tool("get_crew_status", {})["crew"][0]["live"]["note"]
    out = s.run_tool("run_optimizer", {"objective": "margin", "what_if": {"remove_workers": ["w1"], "hotter_c": 2}})
    assert out["verified_by_rule_checker"] and set(out["schedule"]["w1 " + demo.worker("w1").name].split()) == {"rest"}
    assert s.run_tool("simulate_schedule", {})["verified_by_rule_checker"]
    bad = {w.id: [demo.tasks[0].id] * demo.n_slots for w in demo.crew}
    assert not s.run_tool("simulate_schedule", {"assignments": bad})["verified_by_rule_checker"]
    assert "error" in s.run_tool("nope", {})
    # what-ifs only add restrictions: the original scenario is untouched
    sc, forbid, soft = apply_what_if(demo, {"forbid": [{"worker": "w2", "task": "t1"}], "hotter_c": 99})
    assert forbid == [("w2", None, "t1")] and sc.weather[0].tdb == pytest.approx(demo.weather[0].tdb + 6)


def test_chat_without_key_returns_verified_plan(demo):
    r = PlannerSession(demo).chat("What should we do tomorrow?")
    assert r["llm"] is False and r["plan"]["verified"] and "verified plan" in r["reply"]


def test_briefing_renders_from_stubs():
    b = make_briefing(for_date="2026-10-18", use_llm=False, live=False)   # stubs only, even when C1/C2 are merged
    assert b["for_date"] == "2026-10-18" and b["schedule"]["checked_by_rules"]
    assert b["stubs"] and all(s["data_sources"]["index"] == "stub" for s in b["sites"])
    assert b["sites"] == sorted(b["sites"], key=lambda s: -s["index"])
    for s in b["sites"]:
        assert 0 <= s["index"] <= 10 and {"top_hazards", "windows", "past_incidents"} <= set(s)
        assert all(h["kind"] in ("heat", "lightning", "flood", "fire", "soil", "wildlife") for h in s["top_hazards"])
    kinds = {x["task"] for row in b["schedule"]["rows"] for x in row["slots"]}
    assert kinds <= {"heavy", "moderate", "light", "break", "stop_work", "off"} and "stop_work" in kinds
    text = render_text(b)
    assert "6 PM briefing" in text and "STUB" in text


def test_http_endpoints(demo):
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    sched = c.get("/api/schedule").json()
    assert sched["checked_by_rules"] and sched["slot_minutes"] == 30
    plan = c.post("/api/planner/plan", json={"scenario": demo.to_dict(), "use_llm": False}).json()
    assert plan["plan"]["verified"]
    bad = {"assignments": {w.id: [demo.tasks[0].id] * demo.n_slots for w in demo.crew}}
    chk = c.post("/api/planner/check", json={"plan": bad}).json()
    assert chk["ok"] is False and chk["n_safety_violations"] > 0
    chat = c.post("/api/planner/chat", json={"message": "hi"}).json()
    assert chat["session_id"] and chat["plan"]["verified"]
