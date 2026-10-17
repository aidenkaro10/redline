"""
The 6 PM briefing: what tomorrow looks like at every site, plus the crew's suggested schedule.

Per site:
  index           tomorrow's 0-10 hazard index            (session C2: ml.hazard_index.predict.predict_index)
  top_hazards     live + forecast hazards                  (session C1: backend.hazards.picture.hazard_picture)
  windows         forecast hazard windows (heat, storms)   (C1's tomorrow block / our scenario)
  past_incidents  past incidents nearby                    (session C2: ml.hazard_index.predict.past_incidents)
For the crew site: the planner's suggested schedule (archon.py), verified by the rule checker.

Real data comes through backend/planner/sources.py (C1 weather + events, C2 index + incidents).
When any of it isn't available (not merged, feed down, nothing cached), clearly marked STUBS
fill in: every stubbed piece is listed in briefing["stubs"] and each site says where its numbers
came from (data_sources). Workers' personal offsets (thermometer spot checks) shape the schedule.
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
from backend.planner.personal import apply_offsets, describe, load_offsets
from backend.planner.playbook import Playbook
from backend.planner.rules import CheckResult
from backend.planner.scenario import REST, Plan, Scenario
from backend.planner.storm_risk import add_storm_probs
from backend.planner.sources import (c1_entry, c1_hourly, c1_picture, c1_site, c2_incidents, c2_index,
                                     hazard_windows_from_events, slot_weather_from_hourly)

TZ = ZoneInfo("America/New_York")
OUT_PATH = Path("data/planner/briefing.json")
# The crew's site: Deerhaven Generating Station, Gainesville (C1's id; the frontend mock calls it fl-deerhaven)
CREW_SITE = {"id": "eia-663", "name": "Deerhaven Generating Station", "lat": 29.7597, "lon": -82.3893}

# scenario hazard kinds -> the frontend's HazardKind
KIND_MAP = {"lightning": "lightning", "severe_thunderstorm": "lightning", "tornado": "lightning",
            "flash_flood": "flood", "flood": "flood", "hurricane": "flood", "tropical_storm": "flood",
            "heat_advisory": "heat", "extreme_heat": "heat", "red_flag_fire": "fire"}


# ---------------------------------------------------------------------------
# Stubs: used only when C1 / C2 data isn't available (see sources.py for the real adapters)
# ---------------------------------------------------------------------------
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
    for w in sc.crew:                 # stub sites have no crew data of their own
        w.core_offset_c, w.core_sd_c = 0.0, config.PLAN_PERSON_SD_C
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
def resolve_sites(site_ids: list[str] | None, picture: dict | None, crew_site: dict, max_sites: int) -> list[dict]:
    """Which sites go in the briefing: the ones asked for, else the crew site + C1's worst-ranked, else stubs."""
    if site_ids:
        out = []
        for sid in site_ids:
            site = (crew_site if sid == crew_site["id"] else None) or c1_site(sid) \
                or next((x for x in SITES if x["id"] == sid), None)
            if site:
                out.append(site)
        return out
    if picture:
        ranked = [{"id": e["site"]["site_id"], "name": e["site"].get("name", ""), "lat": e["site"].get("lat"),
                   "lon": e["site"].get("lon")} for e in picture.get("sites", []) if e.get("site")]
        return [crew_site] + [x for x in ranked if x["id"] != crew_site["id"]][:max_sites - 1]
    return [crew_site] + [x for x in SITES if x["name"] != crew_site["name"]][:max_sites - 1]


def top_hazards_from_events(entry: dict, for_date: str) -> list[dict]:
    """
    Up to 3 hazards in the frontend's top_hazards shape. Tomorrow first (NWS alerts running into
    tomorrow, max WBGT, HeatRisk), then what's happening right now, marked "Now:".
    """
    levels = {1: "low", 2: "moderate", 3: "high", 4: "extreme"}
    kinds = ("heat", "lightning", "flood", "fire", "soil", "wildlife")
    day = dt.date.fromisoformat(for_date)
    day_start = dt.datetime(day.year, day.month, day.day, tzinfo=TZ)
    tomorrow, now = [], []
    for e in entry.get("events", []):
        kind = e["hazard"] if e.get("hazard") in kinds else "lightning"
        item = {"kind": kind, "level": levels.get(e.get("level", 2), "moderate"), "text": e.get("title", "")}
        try:
            ends = dt.datetime.fromisoformat(e["ends"].replace("Z", "+00:00")) if e.get("ends") else None
        except ValueError:
            ends = None
        if ends is not None and ends > day_start:
            tomorrow.append(item)
        else:
            now.append({**item, "text": "Now: " + item["text"]})
    t = entry.get("tomorrow") or {}
    wb = t.get("wbgt_max") or {}
    if wb.get("wbgt_c") is not None:
        at = dt.datetime.fromisoformat(wb["at"].replace("Z", "+00:00")).astimezone(TZ) if wb.get("at") else None
        level = {"black": "extreme", "red": "high", "yellow": "moderate", "green": "low"}.get(wb.get("flag"), "low")
        tomorrow.append({"kind": "heat", "level": level, "text": f"Max WBGT {wb['wbgt_c']:.0f} °C"
                         + (f" around {fmt_hour(at.hour + at.minute / 60)}" if at else "")})
    hr = t.get("heat_risk") or {}
    if hr.get("label"):
        tomorrow.append({"kind": "heat", "level": levels.get(int(hr.get("value") or 0), "low"),
                         "text": f"NWS HeatRisk: {hr['label']}"})
    return (tomorrow + now)[:3]


def make_briefing(crew_scenario: Scenario | None = None, for_date: str | None = None,
                  playbook: Playbook | None = None, site_ids: list[str] | None = None, use_llm: bool = True,
                  live: bool = True, max_sites: int = 6, offsets: dict | None = None) -> dict:
    """
    Build tomorrow's briefing.
      crew_scenario  the crew's site, crew and tasks (default: the demo crew at Deerhaven)
      site_ids       which sites to brief (default: crew site + C1's worst-ranked, up to max_sites)
      live           False = never call C1/C2 (stubs only, for tests and offline demos)
      offsets        {worker_id: personal offset} (default: load_offsets())
    """
    for_date = for_date or (dt.datetime.now(TZ).date() + dt.timedelta(days=1)).isoformat()
    day = dt.date.fromisoformat(for_date)
    generated_at = int(dt.datetime.combine(day - dt.timedelta(days=1), dt.time(18, 0), TZ).timestamp())
    if crew_scenario is None:
        crew_sc = demo_scenario()
        crew_sc.site = c1_site(CREW_SITE["id"]) or CREW_SITE
    else:
        crew_sc = Scenario.from_dict(crew_scenario.to_dict())     # never change the caller's copy
    crew_sc.date = for_date
    crew_site = crew_sc.site
    personalized = apply_offsets(crew_sc, load_offsets() if offsets is None else offsets)

    stubs = set()
    picture = c1_picture() if live else None
    if picture is None:
        stubs.add("live site hazards (session C1 not available): planner scenario hazards")
    c1_label = "C1 (fake demo data)" if (picture or {}).get("mode") == "fake" else "C1"
    if c1_label != "C1":
        stubs.add("C1 is in HAZARD_MODE=fake: weather and hazards are its built-in demo day, not real feeds")
    out_sites, crew_entry = [], None
    for site in resolve_sites(site_ids, picture, crew_site, max_sites):
        is_crew = site["id"] == crew_site["id"]
        sc = crew_sc if is_crew else site_scenario(site, for_date)
        sources = {"index": "stub", "hazards": "stub", "weather": "stub", "incidents": "stub"}
        entry = c1_entry(picture, site["id"])
        hourly = c1_hourly(site["id"]) if live else None
        wx = slot_weather_from_hourly(hourly, for_date, sc.shift_start, sc.n_slots, sc.slot_min) if hourly else None
        if wx:
            sc.weather, sources["weather"] = wx, c1_label
        else:
            stubs.add("tomorrow's hourly weather (C1 forecast not available): simulated Gainesville summer day")
        if entry is not None:
            sc.hazards = hazard_windows_from_events(entry.get("events", []), for_date, sc.shift_start,
                                                    sc.n_slots, sc.slot_min)
            sources["hazards"] = c1_label
        index = c2_index(site["id"], for_date) if live else None
        if index is None:
            index = stub_index(site, sc, for_date)
            stubs.add("0-10 hazard index (session C2 not available): rough WBGT/storm formula")
        else:
            sources["index"] = "C2"
        incidents = c2_incidents(site["id"]) if live else None
        if incidents is None:
            incidents = stub_incidents(site)
            stubs.add("past incidents (C2 records not available): examples, not real records")
        else:
            sources["incidents"] = "C2"
        top = (top_hazards_from_events(entry, for_date) if entry else []) or top_hazards_from(index, sc)
        out_sites.append({"site_id": site["id"], "name": site["name"], "index": index["index"],
                          "band": index.get("band"), "top_hazards": top, "windows": windows_from(sc),
                          "past_incidents": incidents, "index_reasons": index.get("top_reasons", []),
                          "index_notes": index.get("notes", []), "data_sources": sources})
        if is_crew:
            crew_entry = out_sites[-1]
    out_sites.sort(key=lambda s: -s["index"])

    # Storm chances for the crew's day: a soft cost in the optimizer (the lightning rule stays hard).
    storm_source = add_storm_probs(crew_sc, (c1_entry(picture, crew_site["id"]) or {}).get("events"), live)
    if storm_source == "none":
        stubs.add("storm chances (Open-Meteo not reachable): none used")

    # The crew's suggested schedule: full agent pipeline on tomorrow's forecast, verified by the rule checker.
    r = plan_with_agent(crew_sc, playbook=playbook, use_llm=use_llm)
    suggestions = schedule_suggestions(crew_sc, r.plan)
    if personalized:
        suggestions.append("Personalized from thermometer spot checks: " + "; ".join(
            f"{crew_sc.worker(w).name} {describe(crew_sc.worker(w))}" for w in personalized) + ".")
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
        "crew_data_sources": crew_entry["data_sources"] if crew_entry else None,
        "personalized_workers": personalized,
        "storm_source": storm_source,
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
            heavy_slots = [scenario.slot_time(k) for k, a in enumerate(plan.assignments[w.id]) if a in heavy]
            out.append(f"{w.name} isn't used to the heat yet: "
                       + (f"heavy work at {', '.join(heavy_slots)}, kept under the heat limit with breaks."
                          if heavy_slots else "no heavy outdoor work planned."))
    for w in scenario.crew:
        if w.available != [] and all(a == REST for a in plan.assignments[w.id]):
            out.append(f"{w.name} rests all day: no task {w.name.split(' ')[0]} can do fits under the heat limit "
                       "tomorrow. Consider indoor or light work for them.")
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
    ap.add_argument("--offline", action="store_true", help="never call C1/C2 (stubs only)")
    ap.add_argument("--sites", default=None, help="comma-separated site ids, e.g. eia-663,eia-628")
    ap.add_argument("--max-sites", type=int, default=6, help="without --sites: crew site + the worst-ranked others")
    ap.add_argument("--text-out", type=Path, default=None, help="also save the readable text version here")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    b = make_briefing(for_date=args.date, playbook=Playbook.load_learned(), use_llm=not args.no_llm,
                      live=not args.offline, site_ids=args.sites.split(",") if args.sites else None,
                      max_sites=args.max_sites)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(b, indent=1))
    text = render_text(b)
    if args.text_out:
        args.text_out.parent.mkdir(parents=True, exist_ok=True)
        args.text_out.write_text(text + "\n")
    print(text)
    print(f"\nSaved {args.out}")
