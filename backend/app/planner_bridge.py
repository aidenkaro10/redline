"""
Bridge to session B's planner (backend/planner/), used only when it's merged.

The planner's own router serves /api/schedule, /api/briefing and /api/planner/*
(main.py mounts it). This bridge keeps the planner's crew in step with the live
system and brings its schedule back into the hub:

  - Whenever the set of workers changes (replay starts, a band joins), it builds a
    scenario for THIS crew: the planner's demo shift (tasks, weather, storm window)
    with our worker ids and names, starting now (rounded down to the half hour) on
    the workers' clock, and each worker's personal offset. Then it makes a quick
    verified plan WITHOUT the LLM (~5 s, on a thread) so "Next break" works right away.
    The LLM is only used when someone asks for it (POST /api/planner/plan, chat), and
    only if ANTHROPIC_API_KEY is set (the planner's own rule).
  - It pre-builds tomorrow's 6 PM briefing on a thread right after the first plan (the first
    build is slow; never in parallel with the first plan, see e2e bug #1).
  - Whenever the planner's current plan changes, it pushes the schedule to the hub
    ("schedule" event), which fills WorkerStatus.on_break / break_at.

Every schedule the planner returns has passed its code rule checker; nothing here
changes a plan or a safety limit.
"""

import asyncio
import dataclasses
import datetime as dt
import logging

log = logging.getLogger(__name__)

POLL_S = 3.0                 # how often we look for crew changes and new plans


class PlannerBridge:
    def __init__(self, hub):
        from backend.planner import api            # ImportError = planner not merged yet
        self.api = api
        self.hub = hub
        self.crew_key = None                        # the crew the current scenario was built for
        self.result_seen = None                     # the planner result we last pushed
        self.briefing_started = False               # the briefing pre-build waits for the first plan

    # -- scenario for our crew --------------------------------------------------
    def build_scenario(self):
        from backend.planner.briefing import CREW_SITE, TZ
        from backend.planner.generate import demo_scenario

        sc = demo_scenario()
        site = self.hub.sites.get(self.hub.default_site_id)
        sc.site = ({"id": site["site_id"], "name": site.get("name"), "lat": site.get("lat"), "lon": site.get("lon")}
                   if site else dict(CREW_SITE))
        workers = sorted(self.hub.workers)
        # The shift starts now on the workers' clock (replay time during a replay), rounded down.
        now = self.hub.now(workers[0]) if workers else self.hub.real_clock.now()
        start = dt.datetime.fromtimestamp(now, TZ).replace(second=0, microsecond=0)
        start = start.replace(minute=0 if start.minute < 30 else 30)
        sc.date, sc.shift_start = start.date().isoformat(), f"{start.hour:02d}:{start.minute:02d}"
        if workers:
            template = sc.crew
            sc.crew = []
            for i, wid in enumerate(workers):
                p = self.hub.pipelines[wid]
                last = p.estimates[-1] if p.estimates else {}
                sc.crew.append(dataclasses.replace(
                    template[i % len(template)], id=wid, name=self.hub.workers[wid]["name"],
                    start_core_c=last.get("core_temp")))     # mid-shift: start from their current estimate
        offsets = {wid: self.hub.pipelines[wid].offset.to_dict() for wid in workers}
        return sc, offsets

    def plan_now(self, scenario, offsets) -> None:
        """Set the scenario and make a quick no-LLM verified plan (runs on a thread)."""
        from backend.planner.archon import plan_with_agent
        from backend.planner.playbook import Playbook

        self.api.set_scenario(scenario, offsets)
        result = plan_with_agent(scenario, playbook=Playbook.load_learned(), use_llm=False)
        with self.api._lock:                         # don't overwrite a newer scenario's plan
            if self.api._state["scenario"] is scenario and self.api._state["result"] is None:
                self.api._state["result"] = result

    # -- loop ------------------------------------------------------------------------
    async def step(self) -> None:
        key = tuple(sorted((w, i["name"]) for w, i in self.hub.workers.items()))
        if key and key != self.crew_key:
            self.crew_key = key
            scenario, offsets = self.build_scenario()
            await asyncio.to_thread(self.plan_now, scenario, offsets)
            if not self.briefing_started:
                # Only now: B's heat model must never run on two threads at once (it aborts the
                # process), so the slow briefing build starts after the first plan is done.
                self.briefing_started = True
                asyncio.get_running_loop().run_in_executor(None, self.warm_briefing)
        result = self.api._state.get("result")
        if result is not None and result is not self.result_seen:
            self.result_seen = result
            schedule = await asyncio.to_thread(self.api.get_schedule)
            self.hub.set_schedule(schedule)

    def warm_briefing(self) -> None:
        """Build tomorrow's briefing once in the background: the first one takes a couple of
        minutes (it pulls every site's forecast), later requests hit the planner's cache."""
        try:
            self.api.get_briefing()
            log.info("6 PM briefing ready")
        except Exception as e:
            log.warning("briefing pre-build failed (%s); it will be built on first request", e)

    async def run(self) -> None:
        while True:
            try:
                await self.step()
            except Exception:
                log.exception("planner bridge step failed")
            await asyncio.sleep(POLL_S)


def make_planner_bridge(hub) -> "PlannerBridge | None":
    try:
        bridge = PlannerBridge(hub)
    except ImportError:
        log.info("Planner (session B) not merged yet: /api/schedule and /api/briefing stay empty")
        return None
    from backend.planner.llm import llm_available
    log.info("Planner wired (LLM %s)", "available on request" if llm_available()
             else "off: no ANTHROPIC_API_KEY, or PLANNER_LLM=off")
    return bridge


def schedule_status(schedule: dict | None, worker_id: str, now: float) -> tuple[bool, float | None]:
    """
    (on_break, break_at) for one worker from a frontend-shaped Schedule.
    on_break: the slot they're in now is a break or a stop-work slot.
    break_at: when the break ends (on a break), else when the next break starts. None if no more.
    """
    if not schedule:
        return False, None
    row = next((r for r in schedule.get("rows", []) if r["worker_id"] == worker_id), None)
    if row is None:
        return False, None
    step = schedule.get("slot_minutes", 30) * 60
    rest = ("break", "stop_work")
    slots = row["slots"]
    current = next((i for i, s in enumerate(slots) if s["start"] <= now < s["start"] + step), None)
    if current is not None and slots[current]["task"] in rest:
        end = current
        while end + 1 < len(slots) and slots[end + 1]["task"] in rest:
            end += 1
        return True, float(slots[end]["start"] + step)
    nxt = next((s for s in slots if s["start"] > now and s["task"] in rest), None)
    return False, (float(nxt["start"]) if nxt else None)
