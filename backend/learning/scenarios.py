"""
Scenario "specs": a small set of knobs that describe a stress day, and the code that
turns a spec into a full planner Scenario (backend/planner/scenario.py format).

The curriculum proposer (Claude or code) only ever writes specs. The builder and the
validator here are plain code, so a proposer can't sneak in a broken or impossible day.

Knobs (all bounded, see SPEC_BOUNDS):
    seed          picks weather, tasks and site, like the planner's own generator
    crew          "home" = our fixed 6-person crew (same people every day, used by the
                  14-day playbook loop), "random" = a random crew like the benchmark's
    heat_c        extra °C on top of a normal Gainesville summer day (heat dome = 3-6)
    storm_start   slot when lightning starts (12 = 13:00), or null
    storm_len     how many slots the lightning lasts
    warning_start slot of an NWS severe thunderstorm warning (2 slots), or null
    callouts      how many workers call out sick
    new_hires     extra workers who aren't acclimatized
    dead_watch    planning mid-shift at 11:00 and one worker's watch died: we only have
                  an old reading for them, so their current core temp is uncertain
    demand        how much work there is, as a share of everyone's time (0.5-1.0)
    heavy_bias    0 = any tasks, 1 = mostly heavy outdoor tasks
"""

import hashlib
import json

import numpy as np

from backend import config
from backend.planner.generate import (SITES, TASK_LIBRARY, N_SLOTS, _wbgt_sun, assert_not_test,
                                      load_test_set, weather_slots)
from backend.planner.rules import check_plan
from backend.planner.scenario import HazardWindow, Scenario, SlotWeather, Task, Worker, all_rest_plan

SPEC_BOUNDS = {
    "seed": (0, 10**6), "heat_c": (0.0, 6.0), "storm_start": (6, 15), "storm_len": (1, 3),
    "warning_start": (4, 13), "callouts": (0, 2), "new_hires": (0, 2), "demand": (0.5, 1.0),
    "heavy_bias": (0.0, 1.0),
}
SPEC_DEFAULTS = {"seed": 0, "crew": "home", "heat_c": 0.0, "storm_start": None, "storm_len": 1,
                 "warning_start": None, "callouts": 0, "new_hires": 0, "dead_watch": False,
                 "demand": 0.75, "heavy_bias": 0.3}
FAMILIES = ("heat_dome", "storm_2pm", "callout", "new_hire", "dead_watch")

# Our home crew: the same people every simulated day. Kofi joined this week.
HOME_SITE = SITES[0]                                  # Deerhaven Generating Station, Gainesville
HOME_CREW = [
    dict(id="w1", name="Marcus", weight=90, height=1.85, age=44, acclim=1.0, clo=0.7, skills=["electrical"]),
    dict(id="w2", name="Ana", weight=66, height=1.65, age=31, acclim=1.0, clo=0.6, skills=["climbing"]),
    dict(id="w3", name="Luis", weight=82, height=1.75, age=38, acclim=1.0, clo=0.6, skills=["electrical", "climbing"]),
    dict(id="w4", name="Jada", weight=74, height=1.75, age=27, acclim=1.0, clo=0.5, skills=[]),
    dict(id="w5", name="Tom", weight=98, height=1.85, age=55, acclim=0.7, clo=0.6, skills=["electrical"]),
    dict(id="w6", name="Kofi (new hire)", weight=74, height=1.75, age=23, acclim=0.0, clo=0.6, skills=[]),
]
TEMP_NAMES = ["Sam (temp)", "Wei (temp)"]


def normalize_spec(spec: dict) -> dict:
    """Fill defaults and coerce types. Raises ValueError for unknown keys or out-of-range values."""
    unknown = set(spec) - set(SPEC_DEFAULTS) - {"id", "family", "note"}
    if unknown:
        raise ValueError(f"unknown knobs: {sorted(unknown)}")
    s = {**SPEC_DEFAULTS, **{k: v for k, v in spec.items() if k in SPEC_DEFAULTS}}
    if s["crew"] not in ("home", "random"):
        raise ValueError("crew must be 'home' or 'random'")
    for k in ("seed", "storm_len", "callouts", "new_hires"):
        s[k] = int(s[k])
    for k in ("heat_c", "demand", "heavy_bias"):
        s[k] = round(float(s[k]), 2)
    for k in ("storm_start", "warning_start"):
        s[k] = None if s[k] is None else int(s[k])
    s["dead_watch"] = bool(s["dead_watch"])
    for k, (lo, hi) in SPEC_BOUNDS.items():
        if s[k] is not None and not (lo <= s[k] <= hi):
            raise ValueError(f"{k}={s[k]} outside {lo}..{hi}")
    return s


def spec_families(s: dict) -> list[str]:
    tags = []
    if s["heat_c"] >= 3:
        tags.append("heat_dome")
    if s["storm_start"] is not None:
        tags.append("storm_2pm" if 11 <= s["storm_start"] <= 15 else "storm")
    if s["warning_start"] is not None:
        tags.append("warning")
    if s["callouts"]:
        tags.append("callout")
    if s["new_hires"]:
        tags.append("new_hire")
    if s["dead_watch"]:
        tags.append("dead_watch")
    return tags or ["normal"]


def spec_id(s: dict) -> str:
    """Stable id from the knobs, so the same spec always gives the same scenario id."""
    h = hashlib.sha1(json.dumps(normalize_spec(s), sort_keys=True).encode()).hexdigest()[:8]
    return f"cur-{h}"


def build(spec: dict, scenario_id: str | None = None) -> Scenario:
    """Spec -> Scenario. Deterministic: the same spec always gives the same day."""
    s = normalize_spec(spec)
    rng = np.random.default_rng(s["seed"])
    storm = s["storm_start"]
    weather = weather_slots(rng, 0.0, storm)
    if s["heat_c"]:
        # Heat dome: hotter air, same humidity and sun. WBGT recomputed with the planner's formula.
        weather = [SlotWeather(tdb=round(w.tdb + s["heat_c"], 2), rh=w.rh, solar=w.solar, wind=w.wind,
                               wbgt=round(float(_wbgt_sun(w.tdb + s["heat_c"], w.rh, w.solar, w.wind)), 2))
                   for w in weather]

    hazards = []
    if storm is not None:
        hazards.append(HazardWindow("lightning", storm, min(N_SLOTS, storm + s["storm_len"]), source="GOES-19 GLM",
                                    note="2+ flashes within 10 miles"))
    if s["warning_start"] is not None:
        hazards.append(HazardWindow("severe_thunderstorm", s["warning_start"], s["warning_start"] + 2,
                                    source="NWS", note="Severe Thunderstorm Warning"))
    if s["heat_c"] >= 3:
        hazards.append(HazardWindow("heat_advisory", 0, N_SLOTS, source="NWS", note="Heat Advisory (no stop-work)"))

    # Crew
    if s["crew"] == "home":
        crew = [Worker(**{**w, "skills": list(w["skills"])}) for w in HOME_CREW]
        for i in range(s["new_hires"]):
            crew.append(Worker(id=f"w{len(crew) + 1}", name=TEMP_NAMES[i], weight=82, height=1.75, age=21,
                               acclim=0.0, clo=0.6))
        site = HOME_SITE
    else:
        n = int(rng.integers(4, 7))
        names = rng.choice(["Rosa", "Dev", "Priya", "Emma", "Sam", "Wei", "Jo", "Ike", "Lena", "Omar"], n, replace=False)
        crew = []
        for i, name in enumerate(names):
            crew.append(Worker(id=f"w{i + 1}", name=str(name), weight=float(rng.choice([66, 74, 82, 90, 98])),
                               height=float(rng.choice([1.65, 1.75, 1.85])), age=int(rng.integers(20, 60)),
                               acclim=float(rng.choice([0.7, 1.0, 1.0])), clo=round(float(rng.uniform(0.5, 0.8)), 2),
                               skills=[k for k in ("electrical", "climbing") if rng.uniform() < 0.45]))
        if not any("electrical" in w.skills for w in crew):
            crew[0].skills.append("electrical")
        for w in crew[len(crew) - s["new_hires"]:] if s["new_hires"] else []:
            w.acclim = 0.0
            w.name += " (new hire)"
        site = SITES[int(rng.integers(0, len(SITES)))]
    # Call-outs: never the new hires (so the new-hire stress stays in the day)
    regulars = [w for w in crew if w.acclim >= config.PLAN_UNACCLIM_LEVEL]
    n_out = min(s["callouts"], len(regulars) - 1)
    for i in (rng.choice(len(regulars), n_out, replace=False) if n_out > 0 else []):
        regulars[int(i)].available = []

    shift_start = "07:00"
    if s["dead_watch"]:
        # It's 11:00, replanning the last 8 slots. Everyone is warm. One watch died 40 min ago:
        # we only have their old reading, so the planner is told it's unsure (bigger core_sd_c).
        weather = weather[8:]
        hazards = [HazardWindow(h.kind, max(0, h.start_slot - 8), h.end_slot - 8, h.source, h.note)
                   for h in hazards if h.end_slot > 8]
        shift_start = "11:00"
        for w in crew:
            w.start_core_c = round(float(rng.uniform(37.3, 37.6)), 2)
        dead = crew[int(rng.integers(0, len(crew)))]
        dead.core_sd_c = 0.35
        dead.name += " (watch offline)"

    # Tasks: sized to `demand` of everyone's time, tilted toward heavy outdoor work by heavy_bias
    n_slots = len(weather)
    on_site = [w for w in crew if w.available != []]
    capacity = len(on_site) * n_slots
    heavy = np.array([1.0 + 3 * s["heavy_bias"] * (TASK_LIBRARY[k][1] in ("heavy", "very_heavy"))
                      for k in range(len(TASK_LIBRARY))])
    lib_idx = rng.choice(len(TASK_LIBRARY), int(rng.integers(5, 8)), replace=False, p=heavy / heavy.sum())
    shares = rng.dirichlet(np.ones(len(lib_idx)))
    skills_on_site = {k for w in on_site for k in w.skills}
    tasks = []
    for j, (k, share) in enumerate(zip(lib_idx, shares)):
        name, intensity, outdoor, skill = TASK_LIBRARY[k]
        if skill and skill not in skills_on_site:
            continue                                       # nobody on site can do it
        tasks.append(Task(id=f"t{len(tasks) + 1}", name=name, intensity=intensity, outdoor=outdoor,
                          units=max(1, int(round(share * s["demand"] * capacity))),
                          priority=int(rng.choice([1, 2, 2, 3]))))
    if not any(t.priority == 1 for t in tasks) and tasks:
        tasks[0].priority = 1

    sc = Scenario(id=scenario_id or spec_id(s), site=site, date="2026-07-15", shift_start=shift_start,
                  weather=weather, crew=crew, tasks=tasks, hazards=hazards,
                  tags=spec_families(s) + (["home_crew"] if s["crew"] == "home" else []))
    return sc


# ---------------------------------------------------------------------------
# Validator (code). A proposed scenario must pass every check to enter the bank.
# ---------------------------------------------------------------------------
_TEST_FINGERPRINTS = None


def fingerprint(sc: Scenario) -> str:
    d = sc.to_dict()
    d.pop("id"), d.pop("tags")
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


def validate(spec: dict) -> tuple[Scenario | None, str]:
    """Returns (scenario, "ok") or (None, reason it was rejected)."""
    global _TEST_FINGERPRINTS
    try:
        s = normalize_spec(spec)
        sc = build(s)
    except (ValueError, TypeError, KeyError) as e:
        return None, f"bad spec: {e}"
    try:
        assert_not_test(sc)
    except ValueError as e:
        return None, str(e)
    if _TEST_FINGERPRINTS is None:
        _TEST_FINGERPRINTS = {fingerprint(t) for t in load_test_set()}
    if fingerprint(sc) in _TEST_FINGERPRINTS:
        return None, "identical to a held-out test scenario"
    on_site = [w for w in sc.crew if w.available != []]
    if len(on_site) < 2:
        return None, "fewer than 2 workers on site"
    if not sc.tasks:
        return None, "no tasks anyone on site can do"
    if sum(t.units for t in sc.tasks) > len(on_site) * sc.n_slots:
        return None, "more work than the crew has time for"
    if any(not (15 <= w.tdb <= 44) or not (10 <= w.rh <= 100) or w.wbgt > 36 for w in sc.weather):
        return None, "weather outside the range our heat model is trusted for"
    blocked = sc.stop_work_slots()
    if len(blocked) >= sc.n_slots - 2:
        return None, "stop-work covers nearly the whole shift"
    # Everyone resting in the shade all day must be safe. If not, no plan exists: not a fair test.
    rest = check_plan(sc, all_rest_plan(sc))
    if not rest.ok:
        return None, "even resting all shift breaks a rule (impossible day)"
    return sc, "ok"
