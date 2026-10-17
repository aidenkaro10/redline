"""
The planner's data format: a scenario (one crew, one site, one shift) and a plan.

A scenario is everything the planner knows the evening before (or mid-shift):
    crew        who is working, body size, acclimatization, skills, current core temp
    tasks       what needs doing, how hard it is, how many 30-min "work units" it needs
    weather     forecast for each 30-min slot (air temp, humidity, sun, wind, WBGT)
    hazards     NWS / lightning windows when outdoor work must stop

A plan says what each worker does in each slot: a task id, or "rest" (a shade break).

Everything is plain JSON so the frontend, the LLM and the tests all read the same thing.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from backend import config

REST = "rest"   # the one activity that isn't a task: a 30-min break in the shade

# Task intensities (metabolic rate in met, fraction of time in shade when outdoors).
# Same numbers as the physiology simulator (ml/simulate.py TASKS), copied here so the
# planner doesn't depend on ml/ at runtime. A test checks they stay identical.
INTENSITIES = {
    "light": (2.0, 0.4),          # inspection, light tools
    "moderate": (3.0, 0.2),       # carrying, framing
    "heavy": (4.0, 0.0),          # digging, roofing
    "very_heavy": (5.0, 0.0),     # shoveling, heavy lifting
}
REST_MET, REST_SHADE = 1.2, 0.8   # also from ml/simulate.py


@dataclass
class Worker:
    id: str
    name: str
    weight: float = 75.0          # kg
    height: float = 1.75          # m
    age: int = 35
    acclim: float = 1.0           # 0 = new hire, not acclimatized; 1 = fully acclimatized
    clo: float = 0.6              # clothing insulation
    skills: list[str] = field(default_factory=list)
    start_core_c: float | None = None   # current core temp estimate (mid-shift replans); None = rested
    available: list[int] | None = None  # slots they're on site; None = the whole shift
    # Personal offset (thermometer spot checks, see personal.py). 0 / default = population average.
    core_offset_c: float = 0.0    # this person runs hotter (+) or cooler (-) than the model
    core_sd_c: float = config.PLAN_PERSON_SD_C   # how unsure we are about this person (smaller with spot checks)

    def is_available(self, slot: int) -> bool:
        return self.available is None or slot in self.available


@dataclass
class Task:
    id: str
    name: str
    intensity: str                # a key of INTENSITIES
    units: int                    # 30-min worker-slots of work needed to finish it
    outdoor: bool = True
    priority: int = 2             # 1 = must do today, 2 = should, 3 = nice to have
    earliest: int = 0             # first slot it can be worked
    deadline: int | None = None   # last slot + 1 (None = end of shift)
    skill: str | None = None      # only workers with this skill can do it


@dataclass
class SlotWeather:
    tdb: float                    # air temp °C
    rh: float                     # relative humidity %
    solar: float                  # sun W/m²
    wind: float                   # m/s
    wbgt: float                   # WBGT in full sun °C


@dataclass
class HazardWindow:
    kind: str                     # e.g. "lightning", "severe_thunderstorm", "heat_advisory"
    start_slot: int
    end_slot: int                 # exclusive
    source: str = "NWS"
    note: str = ""

    def stops_outdoor_work(self) -> bool:
        return self.kind in config.PLAN_STOP_WORK_HAZARDS


@dataclass
class Scenario:
    id: str
    site: dict                    # {"id", "name", "lat", "lon"}
    date: str
    shift_start: str              # "07:00"
    weather: list[SlotWeather]    # one per slot
    crew: list[Worker]
    tasks: list[Task]
    hazards: list[HazardWindow] = field(default_factory=list)
    slot_min: int = config.PLAN_SLOT_MIN
    tags: list[str] = field(default_factory=list)   # e.g. ["heat_dome", "storm_2pm"]

    @property
    def n_slots(self) -> int:
        return len(self.weather)

    def worker(self, wid: str) -> Worker:
        return next(w for w in self.crew if w.id == wid)

    def task(self, tid: str) -> Task:
        return next(t for t in self.tasks if t.id == tid)

    def slot_time(self, slot: int) -> str:
        """Clock time a slot starts, e.g. slot 2 of a 07:00 shift -> '08:00'."""
        h, m = map(int, self.shift_start.split(":"))
        total = h * 60 + m + slot * self.slot_min
        return f"{total // 60:02d}:{total % 60:02d}"

    def stop_work_slots(self) -> set[int]:
        """
        Slots where outdoor work is forbidden. Lightning windows end at the last
        strike, so they get the 30-min all-clear added on (PLAN_LIGHTNING_ALLCLEAR_MIN).
        """
        blocked = set()
        extra = -(-config.PLAN_LIGHTNING_ALLCLEAR_MIN // self.slot_min)   # round up to whole slots
        for h in self.hazards:
            if not h.stops_outdoor_work():
                continue
            end = h.end_slot + (extra if h.kind == "lightning" else 0)
            blocked.update(range(max(0, h.start_slot), min(self.n_slots, end)))
        return blocked

    # --- JSON ---------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        return cls(
            id=d["id"], site=d["site"], date=d["date"], shift_start=d["shift_start"],
            weather=[SlotWeather(**w) for w in d["weather"]],
            crew=[Worker(**w) for w in d["crew"]],
            tasks=[Task(**t) for t in d["tasks"]],
            hazards=[HazardWindow(**h) for h in d.get("hazards", [])],
            slot_min=d.get("slot_min", config.PLAN_SLOT_MIN),
            tags=d.get("tags", []),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        return cls.from_dict(json.loads(Path(path).read_text()))


@dataclass
class Plan:
    """What each worker does in each slot: a task id or "rest"."""
    assignments: dict[str, list[str]]
    source: str = ""              # who made it: "optimizer:throughput", "llm", "fixed", ...
    notes: str = ""               # plain-language reasons (from the LLM or the optimizer)
    lessons_used: list[str] = field(default_factory=list)   # playbook lesson ids it relied on

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(assignments={k: list(v) for k, v in d["assignments"].items()},
                   source=d.get("source", ""), notes=d.get("notes", ""),
                   lessons_used=list(d.get("lessons_used", [])))

    def units_done(self, scenario: Scenario) -> dict[str, int]:
        """How many work units each task gets (capped at what the task needs)."""
        done = {t.id: 0 for t in scenario.tasks}
        for slots in self.assignments.values():
            for a in slots:
                if a in done:
                    done[a] += 1
        return {tid: min(n, scenario.task(tid).units) for tid, n in done.items()}


def all_rest_plan(scenario: Scenario) -> Plan:
    """The always-safe fallback: everyone rests in the shade all shift."""
    return Plan({w.id: [REST] * scenario.n_slots for w in scenario.crew}, source="fallback:all_rest")
