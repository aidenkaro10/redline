"""
The simulated "real world": what actually happens when the crew works a plan.

The planner plans with its heat model (population-average people, forecast weather).
Real days differ, and that difference is what the learning loops learn from:

  1. Weather: the actual air temp drifts off the forecast (about ±0.8 °C).
  2. People: everyone runs a little hotter or cooler than average, and works a
     little harder or easier (small random, fixed per person).
  3. Things the planning model doesn't know (the "hidden traits"):
       - Workers who aren't acclimatized tire faster late in the day: from 11:00, any
         work above light heats them like ~35% harder work (cumulative strain and
         dehydration, which the planning model under-counts).
       - Marcus (home crew) runs hot on moderate/heavy work after lunch (from 12:00).
       - A dead watch: that worker is really warmer than their last reading.
     The planner is never told these. The playbook loop has to find them from outcomes.

This is a simulator, so the "14 days" are simulated days, and we say so.
Same engine as the planner's model (ISO 7933 PHS), stepped minute by minute.

outcome(scenario, plan, realization) runs one simulated day and returns what a crew
lead would see the next morning: who got hot, whose temp ran above forecast, alerts,
and the outcome score the loops try to raise.
"""

import zlib
from dataclasses import replace

import numpy as np

from backend import config
from backend.learning import settings
from backend.planner.benchmark import throughput
from backend.planner.heat_model import PHSHeatModel, activity_of, met_shade
from backend.planner.scenario import REST, Plan, Scenario, SlotWeather

HOT = ("heavy", "very_heavy")
NOT_LIGHT = ("moderate", "heavy", "very_heavy")
# Hidden traits. {who: [(intensities, from_hour, work_factor)]}. "unacclimatized" = anyone below PLAN_UNACCLIM_LEVEL.
HIDDEN_FATIGUE = {
    "unacclimatized": [(NOT_LIGHT, 11.0, 1.35)],
    "Marcus": [(NOT_LIGHT, 12.0, 1.25)],
}
DEAD_WATCH_EXTRA_C = 0.25        # the worker with the dead watch is really this much warmer than their last reading
WEATHER_ERR_SD_C = 0.8
PERSON_OFFSET_SD_C = 0.08
PERSON_MET_SD = 0.04


def _rng(*parts) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32("|".join(map(str, parts)).encode()))


def _first_name(name: str) -> str:
    return name.split(" ")[0]


def person_traits(scenario: Scenario, worker) -> dict:
    """Fixed per person: home crew by name (same body every day), others by scenario + id."""
    home = "home_crew" in scenario.tags
    key = _first_name(worker.name) if home else f"{scenario.id}|{worker.id}"
    r = _rng("person", key)
    fatigue = list(HIDDEN_FATIGUE.get(_first_name(worker.name), [])) if home else []
    if worker.acclim < config.PLAN_UNACCLIM_LEVEL:
        fatigue += HIDDEN_FATIGUE["unacclimatized"]
    return {"offset": float(r.normal(0, PERSON_OFFSET_SD_C)), "met_x": float(np.clip(r.normal(1, PERSON_MET_SD), 0.9, 1.1)),
            "fatigue": fatigue, "dead_watch": worker.core_sd_c >= 0.3 and worker.start_core_c is not None}


def actual_weather(scenario: Scenario, realization: int) -> list[SlotWeather]:
    r = _rng("weather", scenario.id, realization)
    base = r.normal(0, WEATHER_ERR_SD_C * 0.8)
    err = base + np.cumsum(r.normal(0, 0.15, scenario.n_slots))
    return [replace(w, tdb=round(w.tdb + float(e), 2)) for w, e in zip(scenario.weather, err)]


def _slot_hour(scenario: Scenario, slot: int) -> float:
    h, m = map(int, scenario.shift_start.split(":"))
    return h + m / 60 + slot * scenario.slot_min / 60


def simulate_actual(scenario: Scenario, plans: list[Plan], realization: int = 0) -> list[dict[str, np.ndarray]]:
    """Actual core temp, minute by minute, for every worker under each plan. {worker: array(minutes)}."""
    model = PHSHeatModel(n_members=1)
    actual_sc = replace(scenario, weather=actual_weather(scenario, realization),
                        crew=[replace(w) for w in scenario.crew])
    traits = {w.id: person_traits(scenario, w) for w in scenario.crew}
    for w in actual_sc.crew:
        if traits[w.id]["dead_watch"]:
            w.start_core_c = w.start_core_c + DEAD_WATCH_EXTRA_C
    day_r = _rng("effort", scenario.id, realization)
    rows = []
    for p_i, plan in enumerate(plans):
        for w in actual_sc.crew:
            t = traits[w.id]
            slots = plan.assignments.get(w.id, [REST] * scenario.n_slots)
            acts = [activity_of(scenario, w, s, slots[s] if s < len(slots) else REST) for s in range(scenario.n_slots)]
            ms = np.array([met_shade(a) for a in acts])
            factor = np.ones(scenario.n_slots)
            for s, a in enumerate(acts):
                if a == REST:
                    continue
                for intensities, from_hour, f in t["fatigue"]:
                    if a[0] in intensities and _slot_hour(scenario, s) >= from_hour:
                        factor[s] *= f
            outdoor = np.array([a != REST and a[1] for a in acts], float)
            rows.append({"plan": p_i, "worker": w, "met": ms[:, 0] * t["met_x"] * factor * float(day_r.normal(1, 0.02)),
                         "shade": ms[:, 1], "indoor": 1 - outdoor, "dtdb": 0.0, "clo": w.clo, "offset": t["offset"]})
    core = model._run(actual_sc, rows, scenario.n_slots * scenario.slot_min)
    out = [{} for _ in plans]
    for row, c in zip(rows, core):
        out[row["plan"]][row["worker"].id] = c
    return out


def score_outcome(scenario: Scenario, plan: Plan, actual: dict[str, np.ndarray]) -> dict:
    """The outcome score the loops try to raise, plus the facts behind it."""
    on_site = [w for w in scenario.crew if w.available != []] or scenario.crew
    peaks = {w.id: float(actual[w.id].max()) for w in on_site}
    alerts = {w.id: int((actual[w.id] >= config.HEAT_WARNING_C).sum()) for w in on_site}
    n_alert = sum(m > 0 for m in alerts.values())
    tp = throughput(scenario, plan)
    peak = max(peaks.values())
    doses = [float(np.clip(actual[w.id] - 37.0, 0, None).sum()) for w in on_site]
    p1 = [t for t in scenario.tasks if t.priority == 1]
    done = plan.units_done(scenario)
    p1_done = all(done[t.id] >= t.units for t in p1)
    score = tp - settings.ALERT_PENALTY * n_alert - settings.PEAK_PENALTY * max(0.0, peak - settings.PEAK_COMFORT_C)
    return {"score": round(score, 4), "throughput": round(tp, 4), "actual_peak_c": round(peak, 3),
            "strain_margin_c": round(config.HEAT_WARNING_C - peak, 3), "workers_alerted": n_alert,
            "alert_minutes": alerts, "actual_peaks": {k: round(v, 3) for k, v in peaks.items()},
            "heat_spread": round(float(np.std(doses)), 1), "priority1_done": p1_done,
            "solved": n_alert == 0 and p1_done}


def outcomes(scenario: Scenario, plan: Plan, realizations=(0,)) -> list[dict]:
    """Run the same plan on several simulated days (different weather/effort luck)."""
    return [score_outcome(scenario, plan, simulate_actual(scenario, [plan], r)[0]) for r in realizations]


def mean_outcome(rows: list[dict]) -> dict:
    keys = ("score", "throughput", "actual_peak_c", "strain_margin_c", "workers_alerted", "heat_spread")
    out = {k: round(float(np.mean([r[k] for r in rows])), 4) for k in keys}
    out["solve_rate"] = round(float(np.mean([r["solved"] for r in rows])), 3)
    return out
