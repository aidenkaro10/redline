"""
Scenario generator: makes realistic Florida power-plant crew days for the benchmark.

Each scenario is built from a seed, so the same seed always gives the same day.
Families (tags) cover the stress cases from the design doc:
    normal       ordinary hot, humid Gainesville summer day
    heat_dome    3-5 °C hotter than normal
    storm_2pm    lightning in the early afternoon (stop-work window + 30-min all-clear)
    warning      an NWS severe thunderstorm warning window
    new_hire     one worker isn't acclimatized
    callout      one worker calls out (not on site)
    replan       planning mid-shift: crew already warm, fewer slots left

The TEST set (scenarios/test/*.json) is fixed and committed. It is never used for
learning: every learning loop calls assert_not_test() before touching a scenario.

Run:  python -m backend.planner.generate          (rewrites the test + demo files)
"""

from pathlib import Path

import numpy as np

from backend.planner.scenario import HazardWindow, Scenario, SlotWeather, Task, Worker

HERE = Path(__file__).parent
TEST_DIR = HERE / "scenarios" / "test"
DEMO_PATH = HERE / "scenarios" / "demo_heatwave.json"
TEST_SEEDS = range(1000, 1012)          # 12 held-out scenarios, never learned from
# Each test seed gets a fixed family mix, so every stress case is covered.
TEST_TAGS = [["normal"], ["normal", "callout"], ["new_hire"], ["storm_2pm"], ["warning"], ["heat_dome"],
             ["heat_dome", "new_hire"], ["heat_dome", "storm_2pm"], ["heat_dome", "warning", "new_hire"],
             ["replan"], ["heat_dome", "replan"], ["storm_2pm", "new_hire", "callout"]]
N_SLOTS = 16                            # 07:00 to 15:00 in 30-min slots

NAMES = ["Marcus", "Ana", "Luis", "Jada", "Tom", "Priya", "Dev", "Rosa", "Kofi", "Emma", "Sam", "Wei"]
SITES = [
    {"id": "deerhaven", "name": "Deerhaven Generating Station", "lat": 29.7597, "lon": -82.3893},
    {"id": "crystal-river", "name": "Crystal River Energy Complex", "lat": 28.9572, "lon": -82.6989},
    {"id": "babcock-solar", "name": "Babcock Ranch Solar Energy Center", "lat": 26.8340, "lon": -81.7470},
    {"id": "turkey-point", "name": "Turkey Point", "lat": 25.4358, "lon": -80.3305},
]
# (name, intensity, outdoor, skill)
TASK_LIBRARY = [
    ("Panel washing", "light", True, None),
    ("Inverter inspection", "light", True, "electrical"),
    ("Transformer maintenance", "moderate", True, "electrical"),
    ("Pipe insulation", "moderate", True, None),
    ("Cable pulling", "heavy", True, "electrical"),
    ("Vegetation clearing", "heavy", True, None),
    ("Scaffold setup", "heavy", True, "climbing"),
    ("Trenching for conduit", "very_heavy", True, None),
    ("Control room checks", "light", False, None),
    ("Shop equipment repair", "moderate", False, None),
]
SKILLS = ["electrical", "climbing"]


def is_test_scenario(scenario_id: str) -> bool:
    return scenario_id.startswith("test-")


def assert_not_test(scenario: Scenario) -> None:
    """Learning loops call this. The held-out test set must never shape the planner."""
    if is_test_scenario(scenario.id):
        raise ValueError(f"{scenario.id} is a held-out TEST scenario; learning loops may not use it")


def _stull_wet_bulb(t, rh):
    """Wet-bulb temperature (Stull 2011), same as ml/simulate.py."""
    return (t * np.arctan(0.151977 * np.sqrt(rh + 8.313659)) + np.arctan(t + rh)
            - np.arctan(rh - 1.676331) + 0.00391838 * rh**1.5 * np.arctan(0.023101 * rh) - 4.686035)


def _wbgt_sun(tdb, rh, solar, wind):
    """WBGT in full sun, same simple formula as ml/simulate.py (Liljegren comes from C1 later)."""
    sun = solar / 1000
    return 0.7 * (_stull_wet_bulb(tdb, rh) + sun) + 0.2 * (tdb + 12 * sun / (1 + 0.3 * wind)) + 0.1 * tdb


def weather_slots(rng, hotter: float = 0.0, storm_slot: int | None = None) -> list[SlotWeather]:
    """A Gainesville-like summer shift, 07:00-15:00, one value per 30-min slot (slot midpoint)."""
    hour = 7 + (np.arange(N_SLOTS) + 0.5) / 2
    tmax = float(np.clip(rng.normal(33.0, 1.5), 29, 37.5)) + hotter
    tmin = tmax - rng.uniform(8, 11)
    phase = np.clip((hour - 6.5) / 9.0, 0, 1)
    tdb = tmin + (tmax - tmin) * np.sin(phase * np.pi / 2)
    rh_min, rh_max = rng.uniform(45, 60), rng.uniform(85, 95)
    rh = rh_max - (rh_max - rh_min) * (tdb - tmin) / (tmax - tmin)
    elev = np.clip(np.sin((hour - 6.7) / 13.6 * np.pi), 0, 1)
    cloud = np.full(N_SLOTS, rng.uniform(0, 0.4))
    if storm_slot is not None:
        cloud[storm_slot:] = 0.9
        k = np.arange(N_SLOTS - storm_slot)
        tdb[storm_slot:] -= rng.uniform(2, 5) * (1 - np.exp(-k * 30 / 15))
    solar = 1000 * elev * (1 - 0.75 * cloud)
    wind = np.clip(rng.uniform(0.5, 3.0) + rng.normal(0, 0.3, N_SLOTS), 0.2, 6)
    rh = np.clip(rh, 20, 99)
    wbgt = _wbgt_sun(tdb, rh, solar, wind)
    return [SlotWeather(tdb=round(float(a), 2), rh=round(float(b), 1), solar=round(float(c), 1),
                        wind=round(float(d), 2), wbgt=round(float(e), 2))
            for a, b, c, d, e in zip(tdb, rh, solar, wind, wbgt)]


def make_scenario(seed: int, prefix: str = "train", tags: list[str] | None = None) -> Scenario:
    """Build one scenario. If tags is None, the seed picks a random mix of families."""
    rng = np.random.default_rng(seed)
    if tags is None:
        tags = ["normal"]
        for tag, p in (("heat_dome", 0.35), ("storm_2pm", 0.3), ("warning", 0.15),
                       ("new_hire", 0.35), ("callout", 0.2), ("replan", 0.15)):
            if rng.uniform() < p:
                tags.append(tag)
        if len(tags) > 1:
            tags.remove("normal")

    hotter = float(rng.uniform(3, 5)) if "heat_dome" in tags else 0.0
    storm = int(rng.integers(12, 15)) if "storm_2pm" in tags else None     # 13:00-14:30 start
    weather = weather_slots(rng, hotter, storm)

    hazards = []
    if storm is not None:
        hazards.append(HazardWindow("lightning", storm, storm + int(rng.integers(1, 3)), source="GOES-19 GLM",
                                    note="2+ flashes within 10 miles"))
    if "warning" in tags:
        s = int(rng.integers(9, 13))
        hazards.append(HazardWindow("severe_thunderstorm", s, s + 2, source="NWS",
                                    note="Severe Thunderstorm Warning"))
    if hotter > 0:
        hazards.append(HazardWindow("heat_advisory", 0, N_SLOTS, source="NWS", note="Heat Advisory (no stop-work)"))

    n_crew = int(rng.integers(4, 7))
    names = rng.choice(NAMES, n_crew, replace=False)
    crew = []
    for i, name in enumerate(names):
        skills = [s for s in SKILLS if rng.uniform() < 0.45]
        crew.append(Worker(
            id=f"w{i + 1}", name=str(name),
            weight=float(rng.choice([66, 74, 82, 90, 98])), height=float(rng.choice([1.65, 1.75, 1.85])),
            age=int(rng.integers(20, 60)), acclim=float(rng.choice([0.7, 1.0, 1.0])),
            clo=round(float(rng.uniform(0.5, 0.8)), 2), skills=skills,
        ))
    if not any("electrical" in w.skills for w in crew):
        crew[0].skills.append("electrical")
    if "new_hire" in tags:
        crew[-1].acclim = 0.0
        crew[-1].name += " (new hire)"
    if "callout" in tags:
        crew[int(rng.integers(0, n_crew))].available = []
    if "replan" in tags:
        # It's 11:00. The morning is done; plan the last 8 slots for a warm crew.
        weather = weather[8:]
        for w in crew:
            w.start_core_c = round(float(rng.uniform(37.3, 37.8)), 2)
        hazards = [HazardWindow(h.kind, max(0, h.start_slot - 8), h.end_slot - 8, h.source, h.note)
                   for h in hazards if h.end_slot > 8]

    n_slots = len(weather)
    on_site = sum(1 for w in crew if w.available != [])
    capacity = on_site * n_slots
    lib_idx = rng.choice(len(TASK_LIBRARY), int(rng.integers(5, 8)), replace=False)
    demand = rng.uniform(0.6, 0.85) * capacity                  # ~60-85% of everyone's time
    shares = rng.dirichlet(np.ones(len(lib_idx)))
    tasks = []
    for j, (k, share) in enumerate(zip(lib_idx, shares)):
        name, intensity, outdoor, skill = TASK_LIBRARY[k]
        deadline = None
        if rng.uniform() < 0.2:
            deadline = int(rng.integers(n_slots // 2, n_slots))
        tasks.append(Task(id=f"t{j + 1}", name=name, intensity=intensity, outdoor=outdoor,
                          units=max(1, int(round(share * demand))), priority=int(rng.choice([1, 2, 2, 3])),
                          deadline=deadline, skill=skill))

    site = SITES[int(rng.integers(0, len(SITES)))]
    return Scenario(id=f"{prefix}-{seed}", site=site, date="2026-07-15",
                    shift_start="11:00" if "replan" in tags else "07:00",
                    weather=weather, crew=crew, tasks=tasks, hazards=hazards, tags=tags)


def demo_scenario() -> Scenario:
    """The demo heat wave: hot, a 2 pm storm, and a new hire. Used by the briefing and day-1 vs day-14 demo."""
    return make_scenario(7, prefix="demo", tags=["heat_dome", "storm_2pm", "new_hire"])


def train_scenarios(n: int, start_seed: int = 0) -> list[Scenario]:
    """Training scenarios for the learning loops (never overlap the test seeds)."""
    seeds = [s for s in range(start_seed, start_seed + n + len(TEST_SEEDS)) if s not in TEST_SEEDS][:n]
    return [make_scenario(s, prefix="train") for s in seeds]


def load_test_set() -> list[Scenario]:
    return [Scenario.load(p) for p in sorted(TEST_DIR.glob("*.json"))]


if __name__ == "__main__":
    for s, tags in zip(TEST_SEEDS, TEST_TAGS):
        sc = make_scenario(s, prefix="test", tags=tags)
        sc.save(TEST_DIR / f"{sc.id}.json")
        print(sc.id, sc.tags, f"{len(sc.crew)} crew, {len(sc.tasks)} tasks")
    demo_scenario().save(DEMO_PATH)
    print("demo ->", DEMO_PATH)
