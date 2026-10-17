"""
HTTP endpoints for the planner. Session A mounts this router in the FastAPI app:

    from backend.planner.api import router as planner_router
    app.include_router(planner_router)

Endpoints (shapes in docs/interfaces/planner.md):
    GET  /api/schedule                 today's crew schedule (frontend Schedule shape), verified
    GET  /api/briefing?date=YYYY-MM-DD the 6 PM briefing (frontend Briefing shape + extras)
    POST /api/planner/plan             run the full agent on a scenario (or the current one)
    POST /api/planner/check            rule-check any plan
    POST /api/planner/chat             chat with the planning agent (what-ifs)
    GET  /api/planner/scenario         the scenario the planner is working on

Slow things (the agent takes a few seconds) are cached until the scenario changes.
"""

import threading
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.planner.agent import PlannerSession
from backend.planner.archon import plan_view, plan_with_agent
from backend.planner.briefing import frontend_schedule, make_briefing
from backend.planner.generate import demo_scenario
from backend.planner.personal import apply_offsets, load_offsets
from backend.planner.playbook import Playbook
from backend.planner.rules import check_plan
from backend.planner.scenario import Plan, Scenario
from backend.planner.storm_risk import add_storm_probs

router = APIRouter()
_lock = threading.Lock()
_state = {"scenario": None, "result": None, "briefings": {}, "chats": {}}


def current_scenario() -> Scenario:
    if _state["scenario"] is None:
        from backend.planner.briefing import CREW_SITE
        sc = demo_scenario()
        sc.site = CREW_SITE
        apply_offsets(sc, load_offsets())
        _state["scenario"] = sc
    return _state["scenario"]


def set_scenario(scenario: Scenario, offsets: dict | None = None) -> None:
    """
    Session A calls this when the crew, tasks or forecast change (e.g. live core temps for a mid-shift replan).
    offsets = {worker_id: PersonalOffset or its to_dict()}; default: load_offsets() (data/personal_offsets.json).
    """
    apply_offsets(scenario, load_offsets() if offsets is None else offsets)
    add_storm_probs(scenario)          # soft cost only; PLANNER_STORM_MODE=live|fake|off
    with _lock:
        _state["scenario"], _state["result"] = scenario, None


def current_plan():
    with _lock:
        if _state["result"] is None:
            _state["result"] = plan_with_agent(current_scenario(), playbook=Playbook.load_learned())
        return _state["result"]


class PlanRequest(BaseModel):
    scenario: dict | None = None
    use_llm: bool = True


class CheckRequest(BaseModel):
    scenario: dict | None = None
    plan: dict


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@router.get("/api/schedule")
def get_schedule():
    r = current_plan()
    sc = current_scenario()
    return frontend_schedule(sc, r.plan, r.check, sc.date, [r.summary])


@router.get("/api/briefing")
def get_briefing(date: str | None = None):
    key = date or "tomorrow"
    if key not in _state["briefings"]:
        _state["briefings"][key] = make_briefing(for_date=date, playbook=Playbook.load_learned())
    return _state["briefings"][key]


@router.post("/api/planner/plan")
def post_plan(req: PlanRequest):
    if req.scenario is not None:
        try:
            set_scenario(Scenario.from_dict(req.scenario))
        except (KeyError, TypeError) as e:
            raise HTTPException(400, f"bad scenario: {e}")
    sc = current_scenario()
    r = plan_with_agent(sc, playbook=Playbook.load_learned(), use_llm=req.use_llm)
    with _lock:
        _state["result"] = r
    return r.to_dict(sc)


@router.post("/api/planner/check")
def post_check(req: CheckRequest):
    sc = Scenario.from_dict(req.scenario) if req.scenario else current_scenario()
    plan = Plan.from_dict(req.plan)
    c = check_plan(sc, plan)
    return {**c.to_dict(), "plan": plan_view(sc, plan, c)}


@router.post("/api/planner/chat")
def post_chat(req: ChatRequest):
    sid = req.session_id or uuid.uuid4().hex[:8]
    chat = _state["chats"].get(sid)
    if chat is None:
        chat = PlannerSession(current_scenario(), playbook=Playbook.load_learned())
        _state["chats"][sid] = chat
    return {"session_id": sid, **chat.chat(req.message)}


@router.get("/api/planner/scenario")
def get_scenario():
    return current_scenario().to_dict()


# ---------------------------------------------------------------------------
# Acting on forecasts: the change feed and the crew lead's approvals (proactive.py)
# ---------------------------------------------------------------------------
@router.get("/api/planner/feed")
def get_feed(limit: int = 50):
    """planner_change events, newest first (applied, proposed, approved, rejected, expired)."""
    from backend.planner.proactive import get_loop
    return get_loop().feed.list(limit)


@router.get("/api/planner/proposals")
def get_proposals():
    """Changes waiting for the crew lead."""
    from backend.planner.proactive import get_loop
    return get_loop().proposals()


@router.post("/api/planner/proposals/{change_id}/{decision}")
def post_decision(change_id: str, decision: str):
    """decision = approve | reject. Approval re-runs the rule checker before anything changes."""
    from backend.planner.proactive import get_loop
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve or reject")
    try:
        return get_loop().decide(change_id, decision == "approve")
    except KeyError:
        raise HTTPException(404, f"no pending change {change_id} (already decided or expired)")
