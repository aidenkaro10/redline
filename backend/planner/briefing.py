"""
The 6 PM briefing: what tomorrow looks like at every site, plus the crew's suggested schedule.

Per site:
  index           tomorrow's 0-10 hazard index            (session C2: ml.hazard_index.predict.predict_index)
  top_hazards     live + forecast hazards                  (session C1: backend.hazards.picture.hazard_picture)
  windows         forecast hazard windows (heat, storms)   (C1's tomorrow block / our scenario)
  past_incidents  past incidents nearby                    (STUB until C1/C2 expose OSHA + Storm Events by site)
For the crew site: the planner's suggested schedule (archon.py), verified by the rule checker.

Until C1 and C2 are merged, their data comes from clearly marked STUBS: every stubbed field
is listed in briefing["stubs"] and each site says where its numbers came from (data_sources).
The JSON matches the frontend's Briefing and Schedule types (frontend/src/lib/contract.ts).

Run:  python -m backend.planner.briefing            (prints it, writes data/planner/briefing.json)
"""

import argparse
import datetime as dt
import json
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from backend import config
from backend.planner.archon import plan_view, plan_with_agent
from backend.planner.generate import SITES, demo_scenario, weather_slots
from backend.planner.playbook import Playbook
from backend.planner.rules import CheckResult
from backend.planner.scenario import REST, Plan, Scenario

TZ = ZoneInfo("America/New_York")
OUT_PATH = Path("data/planner/briefing.json")
# The crew's site: Deerhaven Generating Station, Gainesville (C1's id; the frontend mock calls it fl-deerhaven)
CREW_SITE = {"id": "eia-663", "name": "Deerhaven Generating Station", "lat": 29.7597, "lon": -82.3893}

# scenario hazard kinds -> the frontend's HazardKind
KIND_MAP = {"lightning": "lightning", "severe_thunderstorm": "lightning", "tornado": "lightning",
            "flash_flood": "flood", "flood": "flood", "hurricane": "flood", "tropical_storm": "flood",
            "heat_advisory": "heat", "extreme_heat": "heat", "red_flag_fire": "fire"}


# ---------------------------------------------------------------------------
# Data providers: real ones when the other sessions' code is merged, stubs otherwise
# ---------------------------------------------------------------------------
def c2_index(site_id: str, date: str) -> dict | None:
    """Session C2's hazard index, or None if it isn't available (not merged, not cached)."""
    try:
        from ml.hazard_index.predict import predict_index
        return predict_index(site_id, date, offline=True)
    except Exception:                       # not merged yet / nothing cached / no network: use the stub
        return None


def c1_picture() -> dict | None:
    """Session C1's site hazard picture, or None if it isn't available."""
    try:
        from backend.hazards.picture import latest_picture
        return latest_picture()
    except Exception:
        try:
            from backend.hazards.picture import hazard_picture
            return hazard_picture()
        except Exception:
            return None


def stub_index(site: dict, scenario: Scenario | None, date: str) -> dict:
    """STUB 0-10 index from tomorrow's max WBGT and stop-work hazards. Replaced by C2's model."""
    wbgt = max(w.wbgt for w in scenario.weather) if scenario else 28.0
    storms = sum(h.stops_outdoor_work() for h in scenario.hazards) if scenario else 0
    index = float(np.clip((wbgt - 24) * 0.6 + 2.0 * storms, 0, 10))
    band = "low" if index < 2 else "moderate" if index < 4 else "high" if index < 6 else "extreme"
    return {"site_id": site["id"], "date": date, "index": round(index, 1), "band": band,
            "top_reasons": [{"hazard": "heat", "text": f"Max WBGT tomorrow about {wbgt:.0f} °C (STUB)"}],
            "notes": ["STUB: session C2's hazard index isn't merged yet"], "stub": True}


def stub_incidents(site: dict) -> list[dict]:
    """STUB past incidents. Real ones come from OSHA Severe Injury Reports + NOAA Storm Events near the site."""
    rng = np.random.default_rng(zlib.crc32(site["id"].encode()))
    out = []
    if rng.uniform() > 0.4:
        out.append({"date": "2024-07-18", "text": "Heat exhaustion, lineworker hospitalized (example)",
                    "source": "STUB (OSHA Severe Injury Reports not wired yet)"})
    if rng.uniform() > 0.5:
        out.append({"date": "2025-06-02", "text": "Lightning struck a transmission structure nearby (example)",
                    "source": "STUB (NOAA Storm Events not wired yet)"})
    return out


def site_scenario(site: dict, date: str) -> Scenario:
    """STUB weather + hazards for a non-crew site (seeded by site id). Replaced by C1's forecast."""
    rng = np.random.default_rng(zlib.crc32((site["id"] + date).encode()))
    storm = int(rng.integers(10, 15)) if rng.uniform() < 0.4 else None
    sc = demo_scenario()
    sc.id, sc.site, sc.date = f"briefing-{site['id']}", site, date
    sc.weather = weather_slots(rng, hotter=float(rng.uniform(-1, 3)), storm_slot=storm)
    from backend.planner.scenario import HazardWindow
    sc.hazards = [HazardWindow("lightning", storm, storm + 2, "STUB forecast")] if storm is not None else []
    return sc


# ---------------------------------------------------------------------------
# Pieces of the briefing
# ---------------------------------------------------------------------------
def hour_of(scenario: Scenario, slot: int) -> float:
    h, m = map(int, scenario.shift_start.split(":"))
    return h + (m + slot * scenario.slot_min) / 60


def windows_from(scenario: Scenario) -> list[dict]:
    out = []
    for h in scenario.hazards:
        kind = KIND_MAP.get(h.kind, "lightning")
        a, b = hour_of(scenario, h.start_slot), hour_of(scenario, h.end_slot)
        stop = " (stop outdoor work)" if h.stops_outdoor_work() else ""
        out.append({"kind": kind, "start_hour": a, "end_hour": b,
                    "text": f"{h.kind.replace('_', ' ').capitalize()} {fmt_hour(a)}-{fmt_hour(b)}{stop}"})
    hot = [k for k, w in enumerate(scenario.weather) if w.wbgt >= config.PLAN_HOT_WBGT_C]
    if hot:
        a, b = hour_of(scenario, hot[0]), hour_of(scenario, hot[-1] + 1)
        out.append({"kind": "heat", "start_hour": a, "end_hour": b,
                    "text": f"WBGT at or above {config.PLAN_HOT_WBGT_C:.0f} °C from {fmt_hour(a)} to {fmt_hour(b)}"})
    return out


def top_hazards_from(index: dict, scenario: Scenario) -> list[dict]:
    """Up to 3 hazards with a level, for the frontend's top_hazards."""
    out = []
    for r in (index.get("top_reasons") or [])[:3]:
        kind = {"storm": "lightning", "tropical": "flood"}.get(r.get("hazard"), r.get("hazard", "heat"))
        if kind not in ("heat", "lightning", "flood", "fire", "soil", "wildlife"):
            kind = "heat"
        out.append({"kind": kind, "level": index.get("band", "moderate"), "text": r.get("text", "")})
    if not any(h["kind"] == "lightning" for h in out) and any(h.kind == "lightning" for h in scenario.hazards):
        out.append({"kind": "lightning", "level": "high", "text": "Lightning likely during the shift: stop-work window"})
    return out[:3]


def fmt_hour(h: float) -> str:
    hh, mm = int(h), int(round((h % 1) * 60))
    suffix = "AM" if hh < 12 else "PM"
    return f"{(hh - 1) % 12 + 1}:{mm:02d} {suffix}" if mm else f"{(hh - 1) % 12 + 1} {suffix}"


def frontend_schedule(scenario: Scenario, plan: Plan, check: CheckResult, date: str, notes: list[str]) -> dict:
    """The planner's plan in the frontend's Schedule shape (Unix seconds, task kinds)."""
    h, m = map(int, scenario.shift_start.split(":"))
    day = dt.date.fromisoformat(date)
    day_start = int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=TZ).timestamp())
    blocked = scenario.stop_work_slots()
    rows = []
    for w in scenario.crew:
        slots = []
        for k, a in enumerate(plan.assignments[w.id]):
            start = day_start + k * scenario.slot_min * 60
            if not w.is_available(k):
                task, label = "off", "Off"
            elif a == REST:
                task, label = ("stop_work", "Stop work (lightning/storm)") if k in blocked else ("break", "Shade break")
            else:
                t = scenario.task(a)
                task = "heavy" if t.intensity == "very_heavy" else t.intensity
                label = t.name + ("" if t.outdoor else " (indoors)")
            slots.append({"start": start, "task": task, "label": label})
        rows.append({"worker_id": w.id, "name": w.name, "slots": slots})
    return {"date": date, "slot_minutes": scenario.slot_min, "day_start": day_start, "rows": rows,
            "notes": notes, "checked_by_rules": bool(check.ok)}


# ---------------------------------------------------------------------------
# The briefing
# ---------------------------------------------------------------------------
def make_briefing(crew_scenario: Scenario | None = None, for_date: str | None = None,
                  playbook: Playbook | None = None, sites: list[dict] | None = None, use_llm: bool = True) -> dict:
    """Build tomorrow's briefing. crew_scenario = the crew's site and day (default: the demo day)."""
    for_date = for_date or (dt.datetime.now(TZ).date() + dt.timedelta(days=1)).isoformat()
    day = dt.date.fromisoformat(for_date)
    generated_at = int(dt.datetime.combine(day - dt.timedelta(days=1), dt.time(18, 0), TZ).timestamp())
    crew_sc = crew_scenario or demo_scenario()
    crew_sc.date = for_date
    crew_site = CREW_SITE if crew_scenario is None else crew_sc.site
    crew_sc.site = crew_site
    stubs = set()
    picture = c1_picture()
    if sites is None and picture:
        # C1's real site list (79 Florida plants), crew site first
        c1_sites = [{"id": e["site"].get("site_id"), "name": e["site"].get("name", ""), "lat": e["site"].get("lat"),
                     "lon": e["site"].get("lon")} for e in picture.get("sites", []) if e.get("site")]
        sites = [crew_site] + [x for x in c1_sites if x["id"] != crew_site["id"]]
    sites = sites or [crew_site] + [s for s in SITES if s["name"] != crew_site["name"]]
    if picture is None:
        stubs.add("site hazards + forecast windows (session C1 not merged): from planner scenarios")
    out_sites = []
    for site in sites:
        sc = crew_sc if site["id"] == crew_site["id"] else site_scenario(site, for_date)
        index = c2_index(site["id"], for_date)
        sources = {"index": "C2", "hazards": "C1" if picture else "stub", "incidents": "stub"}
        if index is None:
            index = stub_index(site, sc, for_date)
            sources["index"] = "stub"
            stubs.add("0-10 hazard index (session C2 not merged): rough WBGT/storm formula")
        top = top_hazards_from(index, sc)
        windows = windows_from(sc)
        if picture:
            entry = next((e for e in picture.get("sites", []) if e.get("site", {}).get("site_id") == site["id"]), None)
            if entry:
                top = [{"kind": e["hazard"] if e["hazard"] in ("heat", "lightning", "flood", "fire", "soil", "wildlife") else "lightning",
                        "level": {1: "low", 2: "moderate", 3: "high", 4: "extreme"}.get(e.get("level", 2), "moderate"),
                        "text": e.get("title", "")} for e in entry.get("events", [])[:3]] or top
        stubs.add("past incidents near each site: examples, not real records")
        out_sites.append({"site_id": site["id"], "name": site["name"], "index": index["index"],
                          "band": index.get("band"), "top_hazards": top, "windows": windows,
                          "past_incidents": stub_incidents(site), "index_reasons": index.get("top_reasons", []),
                          "data_sources": sources})
    out_sites.sort(key=lambda s: -s["index"])

    # The crew's suggested schedule: full agent pipeline, verified by the rule checker.
    r = plan_with_agent(crew_sc, playbook=playbook, use_llm=use_llm)
    suggestions = schedule_suggestions(crew_sc, r.plan)
    worst = out_sites[0]
    summary = (f"Tomorrow ({for_date}): highest hazard index is {worst['name']} at {worst['index']:.1f}/10. "
               f"Crew site {crew_site['name']}: {r.summary.replace('Recommended: ', 'plan ')}")
    return {
        "generated_at": generated_at, "for_date": for_date, "summary": summary,
        "sites": out_sites,
        "schedule_suggestions": suggestions + r.reasons,
        "schedule": frontend_schedule(crew_sc, r.plan, r.check, for_date, [r.summary] + suggestions),
        "plan": plan_view(crew_sc, r.plan, r.check),
        "crew_site_id": crew_site["id"],
        "stubs": sorted(stubs),
        "cost_usd": round(r.cost_usd, 4),
    }


def schedule_suggestions(scenario: Scenario, plan: Plan) -> list[str]:
    """A few plain sentences about the plan (no numbers about anyone's body)."""
    out = []
    blocked = sorted(scenario.stop_work_slots())
    if blocked:
        out.append(f"Outdoor work stops {scenario.slot_time(blocked[0])}-{scenario.slot_time(blocked[-1] + 1)} "
                   "for the storm window (indoor tasks and breaks then).")
    heavy = {t.id for t in scenario.tasks if t.intensity in ("heavy", "very_heavy") and t.outdoor}
    first_heavy = [k for row in plan.assignments.values() for k, a in enumerate(row) if a in heavy]
    if first_heavy:
        out.append(f"Heavy outdoor work is scheduled between {scenario.slot_time(min(first_heavy))} "
                   f"and {scenario.slot_time(max(first_heavy) + 1)}.")
    for w in scenario.crew:
        if w.acclim < config.PLAN_UNACCLIM_LEVEL:
            out.append(f"{w.name} is new to the heat: lighter tasks and extra breaks.")
    undone = [t.name for t in scenario.tasks if plan.units_done(scenario)[t.id] < t.units]
    if undone:
        out.append("Won't finish safely tomorrow: " + ", ".join(undone) + ".")
    return out


def render_text(b: dict) -> str:
    """Plain-text version for the console or an email."""
    lines = [f"PlanIT Control 6 PM briefing for {b['for_date']}", "", b["summary"], ""]
    for s in b["sites"]:
        src = ", ".join(f"{k}: {v}" for k, v in s["data_sources"].items())
        lines.append(f"{s['name']} ({s['site_id']}): index {s['index']:.1f}/10 {s.get('band') or ''}  [{src}]")
        for h in s["top_hazards"]:
            lines.append(f"   - {h['kind']} ({h['level']}): {h['text']}")
        for w in s["windows"]:
            lines.append(f"   - window: {w['text']}")
        for p in s["past_incidents"]:
            lines.append(f"   - past: {p['date']} {p['text']} [{p['source']}]")
    lines += ["", f"Suggested schedule for {b['crew_site_id']} (checked by rule checker: {b['schedule']['checked_by_rules']}):"]
    times = [dt.datetime.fromtimestamp(x["start"], TZ).strftime("%H:%M") for x in b["schedule"]["rows"][0]["slots"]]
    lines.append("   " + " ".join(f"{t:>5s}" for t in times))
    short = {"heavy": "HEAVY", "moderate": " mod ", "light": "light", "break": "  .  ", "stop_work": " STOP", "off": " off "}
    for row in b["schedule"]["rows"]:
        lines.append(f"   {row['name']}")
        lines.append("   " + " ".join(f"{short[x['task']]:>5s}" for x in row["slots"]))
    lines += [""] + [f"* {x}" for x in b["schedule_suggestions"]]
    if b["stubs"]:
        lines += ["", "STUB DATA (other sessions not merged yet):"] + [f"  - {x}" for x in b["stubs"]]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="the day being briefed, YYYY-MM-DD (default: tomorrow)")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    b = make_briefing(for_date=args.date, playbook=Playbook.load_learned(), use_llm=not args.no_llm)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(b, indent=1))
    print(render_text(b))
    print(f"\nSaved {args.out}")
