"""
What replay plays: a list of scheduled items, each at an offset (seconds)
from the start of the replay.

    ScheduledItem(offset_s, kind, worker_id, data)
        kind = "reading"  -> data = Reading fields (hr, skin_temp, tilt, activity, ...)
               "button"   -> worker pressed the band button
               "context"  -> data = {"wbgt": ..., "workload": ...} for the next minute

Three ways to build a schedule:
  - from_sim():     simulated shifts in data/sim/sim.npz (main demo worker + crewmates)
  - synthetic():    a tiny built-in fake shift, if sim.npz isn't there (fresh clone, tests)
  - from_jsonl():   a session recorded by backend/storage/recorder.py

The band itself doesn't send heart rate (Pulsoid does), but in replay one
item carries every field so the whole pipeline gets exercised.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

READ_EVERY_S = 5.0              # Apple Watch via Pulsoid sends about every 5 s
SIM_PATH = Path("data/sim/sim.npz")

# Demo shifts from data/sim/sim.npz, picked by running them through this pipeline
# with forecaster v1 (replay starts at minute 116):
# - DEMO_SHIFT: true core temp hits 38.5 at minute 183. The forecaster warns
#   (yellow) at minute 125 and goes red at 153, half an hour early.
# - CREW_SHIFTS: workers with no heat alerts over the first 150 replay minutes.
# If sim.npz is regenerated these may change; then pass --shift.
DEMO_SHIFT = 9647
CREW_SHIFTS = (9568, 8130, 4464)
CREW_NAMES = ("Maria", "Dev", "Tasha")
PREROLL_BEFORE_38_MIN = 45      # start the replay this long before true core temp hits 38.0


@dataclass(order=True)
class ScheduledItem:
    offset_s: float
    kind: str = field(compare=False)
    worker_id: str = field(compare=False)
    data: dict = field(default_factory=dict, compare=False)


@dataclass
class ReplayWorker:
    worker_id: str
    name: str
    auto_ack_s: float | None = None    # presses the button this many seconds after a red alert (None = never)
    meta: dict = field(default_factory=dict)


@dataclass
class ReplayPlan:
    workers: list[ReplayWorker]
    items: list[ScheduledItem]          # sorted by offset; offsets < 0 are "pre-roll", played instantly
    description: str = ""


def _num(x) -> float | None:
    x = float(x)
    return None if np.isnan(x) else round(x, 2)


def _minutes_to_items(worker_id: str, minutes: dict[str, np.ndarray], first_minute: int,
                      fall: tuple[float, float] | None = None) -> list[ScheduledItem]:
    """
    Turn per-minute signals into readings every 5 s. Minute `first_minute` is offset 0.
    fall = (start_offset_s, duration_s): tipped over and not moving during that window.
    """
    items = []
    n = len(minutes["hr"])
    for m in range(n):
        base = (m - first_minute) * 60.0
        # Context lands just after the minute's first reading (which closes the previous
        # minute), so each minute is stored with its own weather and workload.
        context = {}
        for name in ("wbgt", "workload"):
            if name in minutes:
                # Now, plus the next 30 minutes as "known future" (forecast weather, planned work).
                context[name] = _num(minutes[name][m])
                context[f"future_{name}"] = [_num(v) for v in minutes[name][m + 1:m + 31]]
        items.append(ScheduledItem(base + 1.0, "context", worker_id, context))
        for k in range(int(60 / READ_EVERY_S)):
            t = base + k * READ_EVERY_S + 0.5
            fallen = fall is not None and fall[0] <= t < fall[0] + fall[1]
            items.append(ScheduledItem(t, "reading", worker_id, {
                "hr": _num(minutes["hr"][m]),
                "skin_temp": _num(minutes["skin_temp"][m]) if "skin_temp" in minutes else None,
                "tilt": 1 if fallen else 0,
                "activity": 0.0 if fallen else _num(minutes["activity"][m]) if "activity" in minutes else None,
            }))
    return items


def from_sim(path: Path = SIM_PATH, shift: int = DEMO_SHIFT, crew: tuple[int, ...] = CREW_SHIFTS,
             fall_worker: int | None = 1, fall_at_min: float = 40.0, fall_for_min: float = 5.0,
             auto_ack_s: float | None = 20.0, preroll_min: int | None = None) -> ReplayPlan:
    """
    Main worker = `shift`, crewmates = `crew`. A scripted fall happens to crew
    member `fall_worker` (1 = first crewmate, 0 = main worker, None = no fall)
    `fall_at_min` minutes after the replay starts.
    """
    keys = ("hr", "core_temp", "skin_temp", "activity", "wbgt", "workload")
    with np.load(path) as f:
        rows = [shift, *crew]
        z = {k: f[k][rows] for k in keys}
    core = z["core_temp"][0]
    if preroll_min is None:
        crossed = np.flatnonzero(core >= 38.0)
        preroll_min = max(0, int(crossed[0]) - PREROLL_BEFORE_38_MIN) if len(crossed) else 0

    workers = [ReplayWorker("demo-1", "Jake", auto_ack_s=auto_ack_s, meta={"sim_shift": int(shift)})]
    workers += [ReplayWorker(f"demo-{i + 2}", CREW_NAMES[i % len(CREW_NAMES)], auto_ack_s=auto_ack_s,
                             meta={"sim_shift": int(c)}) for i, c in enumerate(crew)]
    items = []
    for i, w in enumerate(workers):
        minutes = {k: z[k][i] for k in keys if k != "core_temp"}
        fall = (fall_at_min * 60.0, fall_for_min * 60.0) if fall_worker == i else None
        items += _minutes_to_items(w.worker_id, minutes, preroll_min, fall)
        # Truth from the simulator, so the chart can show estimate vs real core temp.
        w.meta["true_core_temp"] = [round(float(v), 3) for v in z["core_temp"][i]]
        w.meta["first_minute"] = preroll_min
    desc = f"sim shift {shift} + crew {list(crew)}, starting at minute {preroll_min}"
    return ReplayPlan(workers, sorted(items), desc)


def synthetic(minutes: int = 120, fall_worker: int | None = 1, fall_at_min: float = 40.0,
              fall_for_min: float = 5.0, auto_ack_s: float | None = 20.0, n_crew: int = 2) -> ReplayPlan:
    """A built-in fake shift: the main worker's heart rate climbs, crewmates stay easy."""
    t = np.arange(minutes)
    rng = np.random.default_rng(0)
    shapes = [95 + 80 * np.clip(t / 60, 0, 1)]                     # main: 95 -> 175 bpm over an hour
    shapes += [100 + 8 * np.sin(t / 9 + i) for i in range(n_crew)]  # crew: steady ~100 bpm
    workers, items = [], []
    for i, hr in enumerate(shapes):
        wid = f"demo-{i + 1}"
        name = "Jake" if i == 0 else CREW_NAMES[(i - 1) % len(CREW_NAMES)]
        workers.append(ReplayWorker(wid, name, auto_ack_s=auto_ack_s))
        sig = {
            "hr": hr + rng.normal(0, 2, minutes),
            "skin_temp": 33.5 + 1.5 * np.clip(t / 90, 0, 1),
            "activity": np.full(minutes, 6.0),
        }
        fall = (fall_at_min * 60.0, fall_for_min * 60.0) if fall_worker == i else None
        items += _minutes_to_items(wid, sig, 0, fall)
    return ReplayPlan(workers, sorted(items), f"synthetic {minutes}-minute shift")


def from_jsonl(path: Path, auto_ack_s: float | None = None) -> ReplayPlan:
    """
    Replay a recorded session. Each line is a Reading or ButtonPress as JSON
    (with its original timestamp). Offsets keep the original spacing.
    """
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r.get("type") in ("reading", "button", "sos")]
    if not rows:
        raise ValueError(f"No readings in {path}")
    t0 = min(r["timestamp"] for r in rows)
    items, names = [], {}
    for r in rows:
        wid = r["worker_id"]
        names.setdefault(wid, wid)
        data = {k: v for k, v in r.items() if k not in ("type", "worker_id", "timestamp")}
        items.append(ScheduledItem(r["timestamp"] - t0, r["type"], wid, data))
    workers = [ReplayWorker(w, n, auto_ack_s=auto_ack_s) for w, n in names.items()]
    return ReplayPlan(workers, sorted(items), f"recording {Path(path).name}")
