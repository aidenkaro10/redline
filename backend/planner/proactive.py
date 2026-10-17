"""
Acting on forecasts ("closing the loop"): when a worker is forecast to run hot, change the
SCHEDULE before they get there.

Trigger: the worker's 20-min forecast at the 90th pct reaches the early line (PlannerWeights.early_line_c,
default PLAN_EARLY_LINE_C = 37.7 °C,
below the 38.0 yellow line). Live, that's the forecaster's estimate event, personal offset applied.

Candidate changes, from the slot they're in (or the next one if this one is nearly over):
    swap          trade tasks with a cooler crewmate doing something lighter (same work gets done)
    break         a shade/water break, with a resting crewmate covering the task when possible
    lighter_task  switch to a lighter task that still has work left
    defer_heavy   swap their heavy slot with one of their own lighter slots later in the day

Every candidate is checked in code (never_worse): the rule checker on the whole day (breaks, hazards,
skills...) and the full heat model on the rest of the shift, starting from everyone's CURRENT core
temp: nobody may go over the heat limit, or get worse if live temps already have them over it.

AUTO-APPLY only when ALL of these hold ("safer-only"):
    - it passes the rule checker (never_worse)
    - it's safer for the hot worker, net: their heat above the early line over the next hour plus
      their heat above the yellow line for the rest of the shift goes DOWN (degree-minutes).
      (Core temp keeps rising a few minutes after work stops, so a break shows up as less time
      over the line, not a lower next minute. If a later slot gets a bit hotter, the loop gets
      another look before it.)
    - no crewmate ends up above the early line, or hotter above it than they already were
    - no priority-weighted work is lost, and no task's work moves LATER
Anything else (loses work or pushes work later) becomes a PROPOSAL the crew lead approves in the app.

Hard lines: this only edits the schedule. Heat alerts, man-down and emergency logic never pass
through here, and nothing here can delay or override them. Changes are rate-limited per worker.
"""

import asyncio
import copy
import datetime as dt
import itertools
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from zoneinfo import ZoneInfo

from backend import config
from backend.planner.heat_model import HeatModel, default_heat_model
from backend.planner.notify import Feed
from backend.planner.optimizer import PRIORITY_WEIGHT
from backend.planner.rules import check_plan, check_plans, check_rules, check_structure
from backend.planner.scenario import INTENSITIES, REST, HazardWindow, Plan, Scenario
from backend.planner.weights import DEFAULT as DEFAULT_WEIGHTS, PlannerWeights

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/New_York")
HARDNESS = {REST: 0, **{k: i + 1 for i, k in enumerate(INTENSITIES)}}   # rest < light < moderate < heavy < very_heavy
LATE_IN_SLOT_MIN = 20          # this far into a slot, act on the NEXT slot instead
MAX_LIVE_BIAS_C = 0.5          # cap on how far live forecasts can shift a worker's planning model
NEAR_MIN = 60                  # "right now" = the next hour of the forecast
DOSE_TOL = 0.5                 # degree-minutes: changes smaller than this count as "no change"


def dose(trace, line: float) -> float:
    """Degree-minutes of the 90th pct above a line (how far over, for how long)."""
    return float((trace[:, 2] - line).clip(min=0).sum())
_ids = itertools.count(1)


@dataclass
class Change:
    kind: str                   # swap | break | lighter_task | defer_heavy
    worker_id: str
    slot: int                   # first slot it changes
    plan: Plan                  # the whole new plan
    auto: bool                  # safer-only and costs nothing: applies without asking
    message: str                # plain words for the crew lead (no body numbers)
    throughput_delta: float     # change in priority-weighted share of work done (0 or negative)
    pushes_later: bool
    peak_before: float          # hot worker's forecast 90th pct peak over the next hour, °C
    peak_after: float
    why_not_auto: str = ""
    risk_before: float = 0.0    # degree-minutes (see propose)
    risk_after: float = 0.0
    others: list = field(default_factory=list)   # crewmates whose schedule also changed


# ---------------------------------------------------------------------------
# Pure functions (used live and by the benchmark)
# ---------------------------------------------------------------------------
def slot_at(scenario: Scenario, ts: float) -> tuple[int, float]:
    """(slot index, minutes into that slot) for a Unix time on the scenario's day."""
    h, m = map(int, scenario.shift_start.split(":"))
    d = dt.date.fromisoformat(scenario.date)
    start = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp()
    minutes = (ts - start) / 60
    return int(minutes // scenario.slot_min), minutes % scenario.slot_min


def remaining_scenario(scenario: Scenario, plan: Plan, from_slot: int, core_now: dict) -> Scenario:
    """The rest of the shift from from_slot: weather, hazards and task work left; everyone starts at their current core temp."""
    sc = Scenario.from_dict(copy.deepcopy(scenario.to_dict()))
    done_before = {t.id: 0 for t in scenario.tasks}
    for row in plan.assignments.values():
        for a in row[:from_slot]:
            if a in done_before:
                done_before[a] += 1
    sc.id = f"{scenario.id}@{from_slot}"
    sc.shift_start = scenario.slot_time(from_slot)
    sc.weather = sc.weather[from_slot:]
    sc.hazards = [HazardWindow(h.kind, max(0, h.start_slot - from_slot), h.end_slot - from_slot, h.source, h.note)
                  for h in sc.hazards if h.end_slot > from_slot]
    for t in sc.tasks:
        t.units = max(0, t.units - done_before[t.id])
        t.earliest = max(0, t.earliest - from_slot)
        t.deadline = None if t.deadline is None else max(0, t.deadline - from_slot)
    for w in sc.crew:
        if w.available is not None:
            w.available = [k - from_slot for k in w.available if k >= from_slot]
        if core_now.get(w.id) is not None:
            w.start_core_c = float(core_now[w.id])
    return sc


def tail(plan: Plan, from_slot: int) -> Plan:
    return Plan({w: row[from_slot:] for w, row in plan.assignments.items()}, source=plan.source)


def weighted_done(scenario: Scenario, plan: Plan) -> float:
    done = plan.units_done(scenario)
    total = sum(PRIORITY_WEIGHT.get(t.priority, 1) * t.units for t in scenario.tasks) or 1
    return sum(PRIORITY_WEIGHT.get(t.priority, 1) * done[t.id] for t in scenario.tasks) / total


def pushes_work_later(scenario: Scenario, old: Plan, new: Plan) -> bool:
    """True if, for any task, less of its work is done by some slot than before (work moved later)."""
    for t in scenario.tasks:
        def cumulative(plan):
            per_slot = [sum(row[k] == t.id for row in plan.assignments.values()) for k in range(scenario.n_slots)]
            out, total = [], 0
            for n in per_slot:
                total = min(t.units, total + n)
                out.append(total)
            return out
        if any(n < o for n, o in zip(cumulative(new), cumulative(old))):
            return True
    return False


def hardness(scenario: Scenario, assignment: str) -> int:
    return HARDNESS[REST] if assignment == REST else HARDNESS[scenario.task(assignment).intensity]


def can_do(worker, task, slot: int, scenario: Scenario) -> bool:
    deadline = task.deadline if task.deadline is not None else scenario.n_slots
    return (worker.is_available(slot) and (task.skill is None or task.skill in worker.skills)
            and task.earliest <= slot < deadline)


def candidates(scenario: Scenario, plan: Plan, wid: str, s: int) -> list[tuple[str, Plan, list]]:
    """Every schedule change we'd consider for worker wid starting at slot s: (kind, new plan, other workers touched)."""
    w = scenario.worker(wid)
    row = plan.assignments[wid]
    mine = row[s]
    out = []
    if mine == REST:
        return out
    t_mine = scenario.task(mine)

    def with_changes(changes):
        a = {k: list(v) for k, v in plan.assignments.items()}
        for who, k, what in changes:
            a[who][k] = what
        return Plan(a, source=plan.source)

    for c in scenario.crew:
        if c.id == wid or not c.is_available(s):
            continue
        theirs = plan.assignments[c.id][s]
        if hardness(scenario, theirs) >= hardness(scenario, mine) or not can_do(c, t_mine, s, scenario):
            continue
        if theirs == REST:
            # break covered: c works wid's task, wid takes the shade break
            out.append(("break", with_changes([(wid, s, REST), (c.id, s, mine)]), [c.id]))
        elif can_do(w, scenario.task(theirs), s, scenario):
            out.append(("swap", with_changes([(wid, s, theirs), (c.id, s, mine)]), [c.id]))
    out.append(("break", with_changes([(wid, s, REST)]), []))
    assigned = {t.id: sum(r.count(t.id) for r in plan.assignments.values()) for t in scenario.tasks}
    for t in scenario.tasks:
        if (HARDNESS[t.intensity] < hardness(scenario, mine) and can_do(w, t, s, scenario)
                and assigned[t.id] < t.units and t.id != mine):
            out.append(("lighter_task", with_changes([(wid, s, t.id)]), []))
    for j in range(s + 1, scenario.n_slots):
        later = row[j]
        if hardness(scenario, later) < hardness(scenario, mine) and can_do(w, t_mine, j, scenario) \
                and (later == REST or can_do(w, scenario.task(later), s, scenario)):
            out.append(("defer_heavy", with_changes([(wid, s, later), (wid, j, mine)]), []))
            break
    return out


def message_for(scenario: Scenario, ch_kind: str, wid: str, s: int, old: Plan, new: Plan, others: list) -> str:
    """Template text, no LLM and no body numbers (crew leads don't see biometrics)."""
    name = scenario.worker(wid).name
    at = _clock(scenario.slot_time(s))
    task = lambda a: "a shade break" if a == REST else scenario.task(a).name
    why = "forecast to run hot in the next 20 min"
    if ch_kind == "swap":
        o = others[0]
        return (f"Swapped {name} to {task(new.assignments[wid][s])} at {at}; "
                f"{scenario.worker(o).name} takes {task(old.assignments[wid][s])}. {name} was {why}.")
    if ch_kind == "break":
        cover = f" {scenario.worker(others[0]).name} covers {task(old.assignments[wid][s])}." if others else \
            f" {task(old.assignments[wid][s])} loses {scenario.slot_min} min of work."
        return f"Added a {scenario.slot_min}-min shade/water break for {name} at {at} ({why}).{cover}"
    if ch_kind == "lighter_task":
        return (f"Moved {name} from {task(old.assignments[wid][s])} to {task(new.assignments[wid][s])} at {at} "
                f"({why}).")
    j = next(k for k in range(s + 1, scenario.n_slots) if new.assignments[wid][k] != old.assignments[wid][k])
    return (f"Moved {name}'s {task(old.assignments[wid][s])} from {at} to {_clock(scenario.slot_time(j))} "
            f"({why}). Work finishes later.")


def never_worse(scenario: Scenario, new: Plan, base, chk, exempt: str | None = None) -> bool:
    """
    The rule checker's verdict on a CHANGE to a plan already in use:
      - every non-heat rule passes on the whole day (breaks, hazards, lightning, new hires, skills...)
      - heat, from everyone's current core temp: each worker stays below the limit, or, if live
        temps already put them over it in the current plan, the change doesn't make them worse.
    exempt: the hot worker, whose heat is judged by the net-risk test in propose() instead.
    """
    if check_structure(scenario, new) + check_rules(scenario, new):
        return False
    if any(v.rule != "heat" for v in chk.violations):
        return False
    lim = config.PLAN_MAX_P90_C
    return all(w.id == exempt or chk.peak_p90[w.id] < lim
               or dose(chk.trace[w.id], lim) <= dose(base.trace[w.id], lim) + DOSE_TOL
               for w in scenario.crew)


def calibrate_to_live(rest: Scenario, plan_tail: Plan, forecasts: dict, heat_model: HeatModel,
                      horizon_min: int = config.PLAN_EARLY_HORIZON_MIN) -> dict:
    """
    Line the planning model up with the live forecaster: for each worker with a live 20-min
    forecast (90th pct), shift their model so its own 20-min 90th pct matches. That carries what
    the live system knows (their heart rate, personal offset, today's real heat) into the check.
    Capped at MAX_LIVE_BIAS_C. Changes `rest` in place, returns the shifts.
    """
    if not forecasts:
        return {}
    k = min(horizon_min, rest.n_slots * rest.slot_min) - 1
    shifts = {}
    for _ in range(2):                         # two passes: one shift doesn't fully close a 20-min gap
        model = heat_model.simulate(rest, [plan_tail])[0]
        for w in rest.crew:
            f = forecasts.get(w.id)
            if f is None:
                continue
            total = shifts.get(w.id, 0.0)
            step = float(min(MAX_LIVE_BIAS_C - total, max(-MAX_LIVE_BIAS_C - total, f - model[w.id][k, 2])))
            w.core_offset_c += step            # start temp stays the live reading; the person "runs hotter"
            shifts[w.id] = total + step
    return {k_: round(v, 3) for k_, v in shifts.items()}


def propose(scenario: Scenario, plan: Plan, wid: str, s: int, core_now: dict,
            heat_model: HeatModel | None = None, forecasts: dict | None = None,
            weights: PlannerWeights | None = None) -> list[Change]:
    """
    All verified candidate changes for worker wid from slot s, best first (auto ones first).
    core_now: {worker: current core temp}; forecasts: {worker: live 20-min 90th pct} (optional).
    """
    heat_model = heat_model or default_heat_model()
    if s >= scenario.n_slots:
        return []
    cands = candidates(scenario, plan, wid, s)
    if not cands:
        return []
    rest = remaining_scenario(scenario, plan, s, core_now)
    w8 = (weights or DEFAULT_WEIGHTS).validate()
    calibrate_to_live(rest, tail(plan, s), forecasts or {}, heat_model, w8.early_horizon_min)
    checks = check_plans(rest, [tail(plan, s)] + [tail(p, s) for _, p, _ in cands], heat_model)
    base = checks[0]
    before_tp = weighted_done(scenario, plan)
    out = []
    for (kind, new, others), chk in zip(cands, checks[1:]):
        if not never_worse(scenario, new, base, chk, exempt=wid):
            continue                                     # the rule checker said no
        near = min(NEAR_MIN, rest.n_slots * rest.slot_min)
        peak_b, peak_a = float(base.trace[wid][:near, 2].max()), float(chk.trace[wid][:near, 2].max())
        early, lim = w8.early_line_c, config.PLAN_MAX_P90_C
        risk_b = dose(base.trace[wid][:near], early) + dose(base.trace[wid], lim)
        risk_a = dose(chk.trace[wid][:near], early) + dose(chk.trace[wid], lim)
        if risk_a >= risk_b - DOSE_TOL:
            continue                                     # not actually safer for the hot worker
        others_ok = all(chk.peak_p90[c.id] <= max(base.peak_p90[c.id], early)
                        or dose(chk.trace[c.id], early) <= dose(base.trace[c.id], early) + DOSE_TOL
                        for c in scenario.crew if c.id != wid)
        if not others_ok:
            continue                                     # would just move the problem to a crewmate
        delta = weighted_done(scenario, new) - before_tp
        later = pushes_work_later(scenario, plan, new)
        auto = delta >= -1e-9 and not later
        why_not = "" if auto else ("loses work" if delta < -1e-9 else "pushes work later")
        out.append(Change(kind=kind, worker_id=wid, slot=s, plan=new, auto=auto,
                          message=message_for(scenario, kind, wid, s, plan, new, others),
                          throughput_delta=round(delta, 4), pushes_later=later,
                          peak_before=round(peak_b, 3), peak_after=round(peak_a, 3), why_not_auto=why_not,
                          others=others, risk_before=round(risk_b, 1), risk_after=round(risk_a, 1)))
    # auto first, then least work lost, then biggest drop in the hot worker's peak
    out.sort(key=lambda c: (not c.auto, -c.throughput_delta, c.risk_after))
    return out


def _clock(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


# ---------------------------------------------------------------------------
# The live loop
# ---------------------------------------------------------------------------
class ProactiveLoop:
    """
    Watches estimate events, proposes / applies schedule changes, keeps the feed.
    get_state() -> (scenario, plan) ; set_plan(plan) -> bool stores a new plan (False = refused it).
    """

    def __init__(self, get_state, set_plan, feed: Feed | None = None, heat_model: HeatModel | None = None,
                 asynchronous: bool = True, weights: PlannerWeights | None = None):
        self.weights = (weights or DEFAULT_WEIGHTS).validate()
        self.get_state, self.set_plan = get_state, set_plan
        self.feed = feed or Feed()
        self.heat_model = heat_model or default_heat_model()
        self.core_now: dict[str, float] = {}
        self.forecast_now: dict[str, float] = {}      # latest live 20-min 90th pct per worker
        self.last_change: dict[str, float] = {}       # worker -> timestamp of last change or proposal
        self.count: dict[str, int] = {}
        self.pending: dict[str, dict] = {}             # proposal id -> {"event", "change", "scenario_id"}
        self.busy: set[str] = set()
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1) if asynchronous else None

    # -- input ------------------------------------------------------------------
    def on_event(self, ev: dict) -> None:
        """Hub listener. Only estimate events matter; everything else is ignored (alerts never pass through)."""
        if ev.get("type") != "estimate" or ev.get("preroll"):
            return
        wid, ts = ev.get("worker_id"), ev.get("timestamp")
        if ev.get("core_temp") is not None:
            self.core_now[wid] = ev["core_temp"]
        self.expire(ts)
        f = ev.get("forecast") or {}
        p90 = f.get("p90") or []
        h = self.weights.early_horizon_min
        if len(p90) >= h and p90[h - 1] is not None:
            self.forecast_now[wid] = float(p90[h - 1])
        if len(p90) < h or p90[h - 1] is None or p90[h - 1] < self.weights.early_line_c:
            return
        if not self.allowed(wid, ts):
            return
        with self.lock:
            self.busy.add(wid)
        if self.pool:
            self.pool.submit(self._act_safe, wid, float(p90[h - 1]), ts)
        else:
            self._act_safe(wid, float(p90[h - 1]), ts)

    def allowed(self, wid: str, ts: float) -> bool:
        """Rate limit: one change or proposal per worker per cooldown, capped per shift, none while one is pending."""
        with self.lock:
            if wid in self.busy:
                return False
            if any(p["event"]["worker_id"] == wid for p in self.pending.values()):
                return False
            if ts - self.last_change.get(wid, -1e18) < config.PLAN_PROACTIVE_COOLDOWN_MIN * 60:
                return False
            return self.count.get(wid, 0) < config.PLAN_PROACTIVE_MAX_PER_SHIFT

    def _act_safe(self, wid, p90_20, ts):
        try:
            self.act(wid, p90_20, ts)
        except Exception:
            log.exception("proactive planner failed for %s", wid)
        finally:
            with self.lock:
                self.busy.discard(wid)

    # -- deciding -------------------------------------------------------------------
    def act(self, wid: str, p90_20: float, ts: float) -> dict | None:
        scenario, plan = self.get_state()
        if scenario is None or plan is None or wid not in plan.assignments:
            return None
        s, into = slot_at(scenario, ts)
        if into >= LATE_IN_SLOT_MIN:
            s += 1
        if not 0 <= s < scenario.n_slots:
            return None
        if plan.assignments[wid][s] == REST and s + 1 < scenario.n_slots:
            s += 1                                          # already on a break: look at their next slot
        changes = propose(scenario, plan, wid, s, self.core_now, self.heat_model, forecasts=dict(self.forecast_now),
                          weights=self.weights)
        if not changes:
            return None
        ch = changes[0]
        with self.lock:
            self.last_change[wid] = ts
            self.count[wid] = self.count.get(wid, 0) + 1
        event = self.event_for(scenario, ch, p90_20, ts)
        if ch.auto:
            event["status"] = "applied" if self.set_plan(ch.plan) else "rejected_by_rules"
        else:
            event["status"] = "proposed"
            with self.lock:
                self.pending[event["id"]] = {"event": event, "change": ch, "scenario_id": scenario.id}
        return self.feed.publish(event)

    def event_for(self, scenario: Scenario, ch: Change, p90_20: float, ts: float) -> dict:
        return {
            "type": "planner_change", "id": f"chg-{next(_ids)}", "timestamp": ts,
            "worker_id": ch.worker_id, "worker_name": scenario.worker(ch.worker_id).name,
            "kind": ch.kind, "status": None, "needs_approval": not ch.auto,
            "why_needs_approval": ch.why_not_auto or None,
            "message": ch.message, "slot": ch.slot, "slot_time": scenario.slot_time(ch.slot),
            "others": ch.others, "throughput_delta": ch.throughput_delta, "pushes_work_later": ch.pushes_later,
            # Body numbers: for the worker / demo views only. Session A should drop these for the crew view.
            "forecast_p90_c": round(p90_20, 2), "peak_before_c": ch.peak_before, "peak_after_c": ch.peak_after,
            "checked_by_rules": True,
        }

    # -- crew lead decisions (in the app) ---------------------------------------------
    def decide(self, change_id: str, approve: bool, ts: float | None = None) -> dict:
        with self.lock:
            p = self.pending.pop(change_id, None)
        if p is None:
            raise KeyError(change_id)
        ev = dict(p["event"])
        if not approve:
            ev["status"] = "rejected"
            return self.feed.publish(ev, text_crew_lead=False)
        scenario, plan = self.get_state()
        ch: Change = p["change"]
        # The plan may have changed since: re-apply this worker's edits onto the CURRENT plan and re-check.
        new = Plan({k: list(v) for k, v in plan.assignments.items()}, source=plan.source)
        for who in [ch.worker_id] + ch.others:
            for k in range(ch.slot, scenario.n_slots):
                new.assignments[who][k] = ch.plan.assignments[who][k]
        same_day = scenario.id == p["scenario_id"]
        rules_ok = same_day and not (check_structure(scenario, new) + check_rules(scenario, new))
        if rules_ok:
            rest = remaining_scenario(scenario, plan, ch.slot, self.core_now)
            calibrate_to_live(rest, tail(plan, ch.slot), dict(self.forecast_now), self.heat_model,
                              self.weights.early_horizon_min)
            base, chk = check_plans(rest, [tail(plan, ch.slot), tail(new, ch.slot)], self.heat_model)
            heat_ok = never_worse(scenario, new, base, chk, exempt=ch.worker_id)
        else:
            heat_ok = False
        if not (heat_ok and self.set_plan(new)):
            ev["status"] = "rejected_by_rules"
            ev["message"] = "Couldn't apply: the schedule changed and this no longer passes the rule checker. " + ev["message"]
            return self.feed.publish(ev, text_crew_lead=False)
        ev["status"] = "approved"
        return self.feed.publish(ev, text_crew_lead=False)

    def expire(self, ts: float | None) -> None:
        if ts is None:
            return
        with self.lock:
            old = [k for k, p in self.pending.items()
                   if ts - p["event"]["timestamp"] > config.PLAN_PROPOSAL_EXPIRE_MIN * 60]
            gone = [self.pending.pop(k) for k in old]
        for p in gone:
            self.feed.publish({**p["event"], "status": "expired"}, text_crew_lead=False)

    def proposals(self) -> list[dict]:
        with self.lock:
            return [p["event"] for p in self.pending.values()]


# ---------------------------------------------------------------------------
# Wiring to the planner's current plan and to session A's hub
# ---------------------------------------------------------------------------
_loop: ProactiveLoop | None = None


def _api_state():
    from backend.planner import api
    st = api._state
    result = st.get("result")
    return st.get("scenario"), (result.plan if result is not None else None)


def _api_set_plan(plan: Plan) -> bool:
    """Store a changed plan as the planner's current plan, only if the full-day rule check passes."""
    from backend.planner import api
    with api._lock:
        result, scenario = api._state.get("result"), api._state.get("scenario")
    if result is None:
        return False
    check = check_plan(scenario, plan)
    if not check.ok:
        log.warning("proactive change refused by the full-day rule check: %s", [v.detail for v in check.violations][:3])
        return False
    plan.source = result.plan.source if "+proactive" in result.plan.source else f"{result.plan.source}+proactive"
    with api._lock:
        if api._state.get("scenario") is not scenario:
            return False                                 # the scenario changed meanwhile
        api._state["result"] = replace(result, plan=plan, check=check)
    return True


def get_loop() -> ProactiveLoop:
    """The one live loop, bound to the planner API's current scenario and plan."""
    global _loop
    if _loop is None:
        _loop = ProactiveLoop(_api_state, _api_set_plan)
    return _loop


def attach(hub) -> ProactiveLoop:
    """
    Session A: call once at startup (on the event loop):  from backend.planner.proactive import attach; attach(hub)
    Listens to estimate events and broadcasts planner_change events through hub.dispatch.
    """
    loop = get_loop()
    aloop = asyncio.get_running_loop()
    loop.feed.emit = lambda ev: aloop.call_soon_threadsafe(hub.dispatch, [dict(ev)])
    hub.add_listener(loop.on_event)
    return loop
