"""
Scenario knobs for the simulator: "what kind of shift do we simulate?"

ml/simulate.py draws every setting at random (normal Gainesville summer, random
fitness, random job). For the curriculum we need to TURN THE KNOBS on purpose:
"a 37 °C humid day, an unfit new hire in heavy coveralls, pulse sensor on the
band, watch keeps dropping out". A Scenario holds those knob settings.

simulate_scenarios() simulates a few shifts per scenario. Each shift still has
its own randomness (weather noise, schedule, heart rate quirks), so the same
scenario played several times is like asking the model the same question
several times (Absolute Zero "rollouts").

We reuse ml/simulate.py's physiology (PHS heat model, heart rate model, pulse
sensor artifacts) and never change its defaults. Only the knob sampling lives here.
"""

import dataclasses
import warnings
from dataclasses import dataclass

import numpy as np

from ml.data import Session
from ml.simulate import (HEIGHT_BUCKETS, MET_TO_WM2, SHIFT_MIN, SHIFT_START_MIN, SIGNAL_KEYS, WEIGHT_BUCKETS,
                         _simulate_group, add_ppg_motion_artifacts, sample_person, schedule_day, wbgt_outdoor)

# Every knob with its allowed range (low, high). The proposer searches inside these.
# Ranges are wider than the everyday simulator on purpose (that's where the model breaks),
# but still physically plausible for a Florida outdoor worker.
KNOBS = {
    "tmax": (28.0, 38.5),          # afternoon high, °C (simulate.py: ~33 ± 1.5)
    "humidity": (0.0, 1.0),        # 0 = drier day, 1 = muggy (scales the RH range)
    "cloud": (0.0, 0.9),           # cloud cover all day (0 = full sun)
    "storm": (0.0, 1.0),           # > 0.5 = afternoon thunderstorm (sudden cool-down)
    "wind": (0.2, 4.0),            # m/s
    "forecast_err": (0.3, 2.5),    # WBGT forecast error SD, °C (simulate.py: 0.8)
    "heavy_job": (0.0, 1.0),       # 0 = mostly light tasks, 1 = mostly heavy/very heavy
    "fitness": (0.0, 1.0),         # 1 = very fit (lower resting HR, smaller HR per met)
    "acclim": (0.0, 1.0),          # 0 = new hire, 1 = fully acclimatized
    "clo": (0.4, 1.2),             # clothing insulation (1.0+ = coveralls)
    "stop_temp": (38.3, 39.9),     # core temp at which the worker stops to rest
    "body": (0.0, 1.0),            # body size: picks a weight bucket (0 = 58 kg, 1 = 106 kg)
    "ppg": (0.0, 1.0),             # > 0.5 = heart rate from the band pulse sensor (motion artifacts)
    "hr_gaps": (0.0, 0.08),        # chance per minute that a short heart rate gap starts (simulate.py: 0.01)
    "watch_dies": (0.0, 1.0),      # > 0.5 = heart rate stops for good partway through the shift
    "skin_missing": (0.0, 1.0),    # > 0.5 = skin diode not attached
}


@dataclass
class Scenario:
    tmax: float = 33.0
    humidity: float = 0.5
    cloud: float = 0.2
    storm: float = 0.0
    wind: float = 1.75
    forecast_err: float = 0.8
    heavy_job: float = 0.5
    fitness: float = 0.5
    acclim: float = 0.5
    clo: float = 0.65
    stop_temp: float = 39.0
    body: float = 0.5
    ppg: float = 0.0
    hr_gaps: float = 0.01
    watch_dies: float = 0.0
    skin_missing: float = 0.0

    def clipped(self) -> "Scenario":
        """Same scenario with every knob forced back inside its allowed range."""
        return Scenario(**{k: float(np.clip(v, *KNOBS[k])) for k, v in dataclasses.asdict(self).items()})

    def short(self) -> str:
        """One-line human description, for logs."""
        flags = [n for n in ("storm", "ppg", "watch_dies", "skin_missing") if getattr(self, n) > 0.5]
        return (f"tmax {self.tmax:.1f} hum {self.humidity:.2f} heavy {self.heavy_job:.2f} fit {self.fitness:.2f} "
                f"accl {self.acclim:.2f} clo {self.clo:.2f} stop {self.stop_temp:.1f} fcerr {self.forecast_err:.1f} "
                f"gaps {self.hr_gaps:.3f}" + (" " + "+".join(flags) if flags else ""))


def random_scenario(rng: np.random.Generator) -> Scenario:
    """Every knob drawn uniformly from its range."""
    return Scenario(**{k: float(rng.uniform(lo, hi)) for k, (lo, hi) in KNOBS.items()})


def mutate(s: Scenario, rng: np.random.Generator, scale: float = 0.15) -> Scenario:
    """A nearby scenario: nudge every knob by ~15% of its range (and flip on/off knobs sometimes)."""
    d = dataclasses.asdict(s)
    for k, (lo, hi) in KNOBS.items():
        d[k] += rng.normal(0, scale * (hi - lo))
    return Scenario(**d).clipped()


# ---------------------------------------------------------------------------
# Weather with knobs (same shape as ml/simulate.py's weather_day)
# ---------------------------------------------------------------------------
def scenario_weather(s: Scenario, rng: np.random.Generator) -> dict[str, np.ndarray]:
    hour = (SHIFT_START_MIN + np.arange(SHIFT_MIN)) / 60
    tmax = s.tmax + rng.normal(0, 0.5)                       # small day-to-day wobble
    tmin = tmax - rng.uniform(8, 11)
    phase = np.clip((hour - 6.5) / 9.0, 0, 1)                # peaks ~3:30 pm
    tdb = tmin + (tmax - tmin) * np.sin(phase * np.pi / 2)
    # humidity knob slides the whole RH range: 0 -> 35-75 %, 1 -> 65-98 %
    rh_min = 35 + 30 * s.humidity + rng.uniform(-3, 3)
    rh_max = 75 + 23 * s.humidity + rng.uniform(-3, 3)
    rh = rh_max - (rh_max - rh_min) * (tdb - tmin) / (tmax - tmin)
    elev = np.clip(np.sin((hour - 6.7) / 13.6 * np.pi), 0, 1)
    cloud = np.full(SHIFT_MIN, np.clip(s.cloud + rng.normal(0, 0.05), 0, 0.95))
    if s.storm > 0.5:
        start = int(rng.integers(300, 460))
        cloud[start:] = 0.9
        tdb[start:] -= rng.uniform(2, 5) * (1 - np.exp(-np.arange(SHIFT_MIN - start) / 15))
    solar = 1000 * elev * (1 - 0.75 * cloud)
    wind = np.clip(s.wind + rng.normal(0, 0.3, SHIFT_MIN), 0.2, 6)
    return {"tdb": tdb, "rh": np.clip(rh, 20, 99), "solar": solar, "wind": wind}


def scenario_person(s: Scenario, i: int, rng: np.random.Generator, prefix: str):
    """A random person (ml/simulate.py), then the scenario's knobs override body, fitness, clothing, limits."""
    p = sample_person(i, rng)
    mix = np.array([0.3, 0.35, 0.25, 0.1]) * (1 - s.heavy_job) + np.array([0.05, 0.15, 0.45, 0.35]) * s.heavy_job
    w_idx = int(round(s.body * (len(WEIGHT_BUCKETS) - 1)))
    h_idx = int(np.clip(round(s.body * (len(HEIGHT_BUCKETS) - 1) + rng.normal(0, 0.5)), 0, len(HEIGHT_BUCKETS) - 1))
    return dataclasses.replace(
        p,
        person_id=f"{prefix}-{i:05d}",
        weight=float(WEIGHT_BUCKETS[w_idx]),
        height=float(HEIGHT_BUCKETS[h_idx]),
        # same fitness formulas as sample_person, with the scenario's fitness
        rest_hr=float(rng.normal(72 - 12 * s.fitness, 5)),
        hr_per_met=float((rng.uniform(14, 20) - 5 * s.fitness) * 0.5),
        stop_temp=float(s.stop_temp + rng.normal(0, 0.1)),
        clo=float(s.clo),
        acclim_start=float(s.acclim),
        job_mix=tuple(float(x) for x in mix / mix.sum()),
    )


# ---------------------------------------------------------------------------
# Simulate many scenarios at once
# ---------------------------------------------------------------------------
def simulate_scenarios(scenarios: list[Scenario], shifts_each: int, seed: int, prefix: str = "cur") -> list[list[Session]]:
    """
    Simulate `shifts_each` shifts for every scenario. Every shift is a new virtual
    person (so curriculum people never overlap the base simulator's people).
    Returns one list of Sessions per scenario, in the same order.
    """
    rng = np.random.default_rng(seed)
    people, rows, scn_of_row = [], [], []
    for k, s in enumerate(scenarios):
        for j in range(shifts_each):
            person = scenario_person(s, len(people), rng, prefix)
            people.append(person)
            met, shade = schedule_day(person, rng)
            rows.append({"p": len(people) - 1, "day": 0, "w": scenario_weather(s, rng),
                         "met": met, "shade": shade, "acclim": s.acclim})
            scn_of_row.append(k)

    n = len(rows)
    out = {k: np.zeros((n, SHIFT_MIN), np.float32) for k in
           ("hr", "core_temp", "skin_temp", "activity", "wbgt", "workload", "wbgt_actual", "met_actual",
            "sweat_loss_g", "forced_rest")}
    # Same batching trick as ml/simulate.py: shifts with the same body size + acclimatized flag share a PHS call.
    groups = {}
    for i, r in enumerate(rows):
        p = people[r["p"]]
        groups.setdefault((p.weight, p.height, 100 if r["acclim"] >= 0.5 else 0), []).append(i)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for (weight, height, flag), idx in sorted(groups.items()):
            _simulate_group(rows, people, np.array(idx), weight, height, flag, rng, out)

    # Scenario-controlled sensor problems (instead of ml/simulate.py's fixed rates).
    sessions = [[] for _ in scenarios]
    for i, r in enumerate(rows):
        s = scenarios[scn_of_row[i]]
        person = people[r["p"]]
        hr = out["hr"][i].astype(float)
        if s.ppg > 0.5:
            hr = add_ppg_motion_artifacts(hr, out["met_actual"][i], rng)
        t = 0
        while t < SHIFT_MIN:                                   # short heart rate gaps
            if rng.uniform() < s.hr_gaps:
                gap = int(rng.integers(1, 6))
                hr[t:t + gap] = np.nan
                t += gap
            t += 1
        if s.watch_dies > 0.5:
            hr[int(rng.integers(60, SHIFT_MIN - 60)):] = np.nan
        skin = (out["skin_temp"][i] + person.diode_offset + np.cumsum(rng.normal(0, 0.01, SHIFT_MIN))
                + rng.normal(0, 0.1, SHIFT_MIN))
        if s.skin_missing > 0.5:
            skin[:] = np.nan
        # The WBGT the model sees ahead of time = planned WBGT + forecast error (scenario-controlled size).
        w = r["w"]
        planned = wbgt_outdoor(w["tdb"], w["rh"], w["solar"], w["wind"], r["shade"])
        err = rng.normal(0, s.forecast_err, SHIFT_MIN // 60 + 1).repeat(60)[:SHIFT_MIN]
        signals = {
            "hr": hr, "core_temp": out["core_temp"][i].astype(float), "skin_temp": skin,
            "activity": out["activity"][i].astype(float), "wbgt": planned + err,
            "workload": r["met"] * MET_TO_WM2,
        }
        assert set(signals) == set(SIGNAL_KEYS)
        sessions[scn_of_row[i]].append(Session(
            person_id=person.person_id, session_id=f"{person.person_id}-d00", source="sim",
            condition="curriculum", signals=signals,
            meta={"acclim": s.acclim, "forced_rest": out["forced_rest"][i].astype(bool),
                  "hr_source": "band_ppg" if s.ppg > 0.5 else "watch", "scenario": scn_of_row[i]},
        ))
    return sessions
