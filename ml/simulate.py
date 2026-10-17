"""
The physiology simulator: thousands of virtual outdoor workers in Gainesville summer heat.

What drives what:
    weather + task schedule + body  ->  ISO 7933 heat strain model (pythermalcomfort PHS)
                                    ->  true core temp, skin temp, sweat loss
    workload + core temp + fitness  ->  our heart rate model (NOT the Kalman's curve,
                                        so the baseline isn't handed the answer)
    then sensor realism: watch dropouts, diode offset and drift, tilt flicker noise,
    and a weather FORECAST (what the model sees) that differs from the actual weather.

Every person has an ID and several consecutive days, so we can split by person and
train the personal twins. Acclimatization rises with hot days and fades on days off.

Speed trick: PHS accepts arrays, so we step a whole batch of shifts one minute at a
time. Body size and acclimatized status must be single numbers per call, so batches
are grouped by those.

Approximations (documented on purpose):
- Weather is a Gainesville-like summer generator, not historical data (Open-Meteo later).
- Mean radiant temp and WBGT use simple sun/shade formulas (Liljegren comes in Phase 8).

Run:  python -m ml.simulate --people 1000 --days 10      (writes data/sim/sim.npz)
"""

import argparse
import json
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from pythermalcomfort.models import phs

from ml.data import Session

SHIFT_START_MIN = 7 * 60       # 7:00 am
SHIFT_MIN = 480                # 8-hour shift
MET_TO_WM2 = 58.15             # 1 met = 58.15 W/m²
REST_MET, REST_SHADE = 1.2, 0.8
PHS_STATE = ("t_re", "t_sk", "t_cr", "t_cr_eq", "t_sk_t_cr_wg", "sweat_rate_watt", "evap_load_wm2_min")

WEIGHT_BUCKETS = (58, 66, 74, 82, 90, 98, 106)   # kg
HEIGHT_BUCKETS = (1.65, 1.75, 1.85)               # m

# name: (metabolic rate in met, fraction of time in shade)
# Heart rate model calibration knobs, tuned so the Kalman filter behaves the same on
# simulated shifts as on real people (see ml/sim_check.py). Provisional until we
# know the Mendeley study protocol.
# Result on 2026-10-17: Kalman on sim RMSE ~0.40, bias ~+0.05 vs real 0.46, +0.04.
HR_PER_MET_SCALE = 0.5
HR_PER_DEGC_SCALE = 1.0
HR_PERSON_SD = 12.0     # bpm: some people just run higher or lower than others
HR_DAY_SD = 5.0         # bpm: day-to-day differences (sleep, caffeine, hydration)
HR_NOISE_SD = 3.0       # bpm: minute-to-minute noise

TASKS = {
    "light": (2.0, 0.4),          # inspection, light tools
    "moderate": (3.0, 0.2),       # carrying, framing
    "heavy": (4.0, 0.0),          # digging, roofing
    "very_heavy": (5.0, 0.0),     # shoveling, heavy lifting
}


@dataclass
class Person:
    person_id: str
    weight: float           # kg (snapped to a bucket)
    height: float           # m  (snapped to a bucket)
    age: int
    rest_hr: float          # bpm
    max_hr: float           # bpm
    hr_per_met: float       # bpm added per met above rest (lower = fitter)
    hr_per_degc: float      # bpm added per °C of core temp rise
    hr_offset: float        # personal HR offset vs. the average person, bpm
    drift_per_min: float    # cardiac drift during work, bpm per minute
    stop_temp: float        # core temp at which this person stops to rest (some push too far)
    diode_offset: float     # their skin sensor reads this much off true skin temp, °C
    clo: float              # usual clothing insulation
    acclim_start: float     # 0 = not acclimatized, 1 = fully
    job_mix: tuple          # probabilities over TASKS


def sample_person(i: int, rng: np.random.Generator) -> Person:
    fitness = rng.uniform(0, 1)                 # 1 = very fit
    age = int(rng.integers(19, 61))
    heavy_job = rng.uniform(0, 1)
    mix = np.array([0.3, 0.35, 0.25, 0.1]) * (1 - heavy_job) + np.array([0.1, 0.25, 0.4, 0.25]) * heavy_job
    return Person(
        person_id=f"sim-{i:05d}",
        weight=float(rng.choice(WEIGHT_BUCKETS)),
        height=float(rng.choice(HEIGHT_BUCKETS)),
        age=age,
        rest_hr=float(rng.normal(72 - 12 * fitness, 5)),
        max_hr=208 - 0.7 * age,
        hr_per_met=float((rng.uniform(14, 20) - 5 * fitness) * HR_PER_MET_SCALE),
        hr_per_degc=float(rng.uniform(22, 36) * HR_PER_DEGC_SCALE),
        hr_offset=float(rng.normal(0, HR_PERSON_SD)),
        drift_per_min=float(rng.uniform(0.03, 0.12)),
        stop_temp=float(rng.uniform(38.6, 39.5)),
        diode_offset=float(rng.uniform(-1.5, -0.3)),
        clo=float(rng.uniform(0.45, 0.85)),
        acclim_start=float(rng.choice([0.0, 0.0, 0.3, 0.7, 1.0])),
        job_mix=tuple(float(x) for x in mix / mix.sum()),
    )


# ---------------------------------------------------------------------------
# Weather: a Gainesville-like summer day (hot, humid, afternoon storms)
# ---------------------------------------------------------------------------
def stull_wet_bulb(t: np.ndarray, rh: np.ndarray) -> np.ndarray:
    """Wet-bulb temperature from air temp (°C) and RH (%) (Stull 2011 approximation)."""
    return (t * np.arctan(0.151977 * np.sqrt(rh + 8.313659)) + np.arctan(t + rh)
            - np.arctan(rh - 1.676331) + 0.00391838 * rh**1.5 * np.arctan(0.023101 * rh) - 4.686035)


def weather_day(rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Minute-by-minute weather for the shift (7:00 to 15:00)."""
    minute_of_day = SHIFT_START_MIN + np.arange(SHIFT_MIN)
    hour = minute_of_day / 60
    tmax = np.clip(rng.normal(33.0, 1.5), 29, 37.5)
    tmin = tmax - rng.uniform(8, 11)
    # temperature peaks around 3:30 pm, lowest around 6:30 am
    phase = np.clip((hour - 6.5) / 9.0, 0, 1)
    tdb = tmin + (tmax - tmin) * np.sin(phase * np.pi / 2)
    rh_min, rh_max = rng.uniform(45, 60), rng.uniform(85, 95)
    rh = rh_max - (rh_max - rh_min) * (tdb - tmin) / (tmax - tmin)
    # sun: solar elevation with solar noon around 1:30 pm (EDT), plus clouds
    elev = np.clip(np.sin((hour - 6.7) / 13.6 * np.pi), 0, 1)
    cloud = np.full(SHIFT_MIN, rng.uniform(0, 0.4))
    if rng.uniform() < 0.4:                       # afternoon thunderstorm
        start = int(rng.integers(300, 460))       # between noon and 2:40 pm
        cloud[start:] = 0.9
        tdb[start:] -= rng.uniform(2, 5) * (1 - np.exp(-np.arange(SHIFT_MIN - start) / 15))
    solar = 1000 * elev * (1 - 0.75 * cloud)
    wind = np.clip(rng.uniform(0.5, 3.0) + rng.normal(0, 0.3, SHIFT_MIN), 0.2, 6)
    return {"tdb": tdb, "rh": np.clip(rh, 20, 99), "solar": solar, "wind": wind}


def wbgt_outdoor(tdb, rh, solar, wind, shade):
    """Approximate outdoor WBGT. Replaced by the Liljegren method in Phase 8."""
    sun = solar / 1000 * (1 - shade)
    tnwb = stull_wet_bulb(tdb, rh) + 1.0 * sun
    tg = tdb + 12 * sun / (1 + 0.3 * wind)
    return 0.7 * tnwb + 0.2 * tg + 0.1 * tdb


def mean_radiant(tdb, solar, shade):
    """Mean radiant temperature: hotter than air in the sun, roughly air temp in shade."""
    return tdb + 2 + 15 * solar / 1000 * (1 - shade)


# ---------------------------------------------------------------------------
# Task schedules
# ---------------------------------------------------------------------------
def schedule_day(person: Person, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Planned metabolic rate (met) and shade fraction for each minute of the shift."""
    met = np.empty(SHIFT_MIN)
    shade = np.empty(SHIFT_MIN)
    lunch = int(rng.integers(255, 300))          # lunch between 11:15 and noon
    t, since_break = 0, 0
    names = list(TASKS)
    while t < SHIFT_MIN:
        if lunch <= t < lunch + 30:
            n, m, s = 30 - (t - lunch), REST_MET, REST_SHADE
            since_break = 0
        elif since_break >= rng.integers(90, 150):
            n, m, s = int(rng.integers(10, 16)), REST_MET, REST_SHADE
            since_break = 0
        else:
            task = names[rng.choice(len(names), p=person.job_mix)]
            n = int(rng.integers(20, 91))
            m, s = TASKS[task]
            m = m * rng.uniform(0.9, 1.1)
            if t < lunch < t + n:
                n = lunch - t
            since_break += n
        met[t:t + n], shade[t:t + n] = m, s
        t += n
    return met, shade


# ---------------------------------------------------------------------------
# The simulation
# ---------------------------------------------------------------------------
def _acclim_series(person: Person, day_hot: list[float], off_days: list[bool]) -> list[float]:
    """Acclimatization rises with hot work days and fades on days off (0..1)."""
    a, out = person.acclim_start, []
    for hot, off in zip(day_hot, off_days):
        out.append(a)
        if off:
            a = max(0.0, a - 0.04)
        else:
            a = min(1.0, a + 0.10 * hot * (1 - a))
    return out


def simulate(n_people: int, n_days: int, seed: int = 0, verbose: bool = True) -> dict:
    """
    Simulate n_people x n_days shifts. Returns a dict of arrays, one row per shift:
      signals: hr, core_temp, skin_temp, activity, wbgt (forecast), workload (planned W/m²)
      truth:   wbgt_actual, met_actual, sweat_loss_g, forced_rest
      ids/meta: person index, day, acclim, plus the list of Person records
    """
    rng = np.random.default_rng(seed)
    people = [sample_person(i, rng) for i in range(n_people)]

    # --- Plan every shift: weather, schedule, which days are off, acclimatization.
    rows = []
    for p_idx, person in enumerate(people):
        off_days = [(d % 7) >= 5 for d in range(n_days)]   # weekends off
        days = []
        for d in range(n_days):
            w = weather_day(rng)
            met, shade = schedule_day(person, rng)
            hot = float(np.mean(wbgt_outdoor(w["tdb"], w["rh"], w["solar"], w["wind"], shade) > 28))
            days.append((d, w, met, shade, hot))
        acclim = _acclim_series(person, [x[4] for x in days], off_days)
        for (d, w, met, shade, _), a, off in zip(days, acclim, off_days):
            if not off:
                rows.append({"p": p_idx, "day": d, "w": w, "met": met, "shade": shade, "acclim": a})

    n = len(rows)
    out = {k: np.zeros((n, SHIFT_MIN), np.float32) for k in
           ("hr", "core_temp", "skin_temp", "activity", "wbgt", "workload", "wbgt_actual", "met_actual", "sweat_loss_g", "forced_rest")}

    # --- Group shifts that can share one PHS call: same body size and acclimatized flag.
    groups = {}
    for i, r in enumerate(rows):
        person = people[r["p"]]
        key = (person.weight, person.height, 100 if r["acclim"] >= 0.5 else 0)
        groups.setdefault(key, []).append(i)

    t0 = time.time()
    for g_num, ((weight, height, acclim_flag), idx) in enumerate(sorted(groups.items())):
        idx = np.array(idx)
        _simulate_group(rows, people, idx, weight, height, acclim_flag, rng, out)
        if verbose:
            print(f"  group {g_num + 1}/{len(groups)} ({len(idx)} shifts) done, {time.time() - t0:.0f}s")

    out["person_index"] = np.array([r["p"] for r in rows])
    out["day"] = np.array([r["day"] for r in rows])
    out["acclim"] = np.array([r["acclim"] for r in rows], np.float32)
    out["people"] = people
    return out


def _simulate_group(rows, people, idx, weight, height, acclim_flag, rng, out):
    g = len(idx)
    P = [people[rows[i]["p"]] for i in idx]
    col = lambda attr: np.array([getattr(p, attr) for p in P])
    tdb = np.stack([rows[i]["w"]["tdb"] for i in idx])
    rh = np.stack([rows[i]["w"]["rh"] for i in idx])
    solar = np.stack([rows[i]["w"]["solar"] for i in idx])
    wind = np.stack([rows[i]["w"]["wind"] for i in idx])
    met_plan = np.stack([rows[i]["met"] for i in idx])
    shade_plan = np.stack([rows[i]["shade"] for i in idx])
    acclim = np.array([rows[i]["acclim"] for i in idx])
    clo = np.clip(col("clo") + rng.normal(0, 0.05, g), 0.3, 1.0)

    stop, rest_hr, max_hr = col("stop_temp"), col("rest_hr"), col("max_hr")
    hr_per_met, hr_per_degc, drift_rate = col("hr_per_met"), col("hr_per_degc"), col("drift_per_min")
    hr_shift = col("hr_offset") + rng.normal(0, HR_DAY_SD, g)     # person + today

    state = {}
    forced = np.zeros(g, bool)
    forced_for = np.zeros(g)
    hr = rest_hr + rng.normal(0, 3, g)
    drift = np.zeros(g)
    noise = np.zeros(g)
    t_cr0 = None
    for t in range(SHIFT_MIN):
        resting_now = forced.copy()                 # the state actually used this minute
        met = np.where(forced, REST_MET, met_plan[:, t])
        shade = np.where(forced, REST_SHADE, shade_plan[:, t])
        tr = mean_radiant(tdb[:, t], solar[:, t], shade)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = phs(tdb=tdb[:, t], tr=tr, v=wind[:, t], rh=rh[:, t], met=met, clo=clo,
                    posture="standing", round_output=False, limit_inputs=False, model="7933-2004",
                    weight=weight, height=height, acclimatized=acclim_flag, duration=1, **state)
        state = {k: np.asarray(getattr(r, k), dtype=float) for k in PHS_STATE}
        t_re, t_cr, t_sk = state["t_re"], state["t_cr"], state["t_sk"]
        if t_cr0 is None:
            t_cr0 = t_cr.copy()
        sweat = np.asarray(r.sweat_loss_g, dtype=float)

        # Self-pacing: past your limit, you stop and rest in the shade for at least 10 min.
        forced_for = np.where(forced, forced_for + 1, 0)
        start = ~forced & (t_re >= stop)
        end = forced & (forced_for >= 10) & (t_re < stop - 0.4)
        forced = (forced | start) & ~end

        # Heart rate: rest + workload + core temp rise + cardiac drift + dehydration - acclimatization.
        working = met > 2.0
        drift = np.where(working, drift + drift_rate, drift * 0.9)
        dehydration_pct = 0.5 * sweat / (weight * 1000) * 100       # drinking replaces half
        target = (rest_hr + hr_shift + hr_per_met * (met - 1) + hr_per_degc * np.maximum(0, t_cr - t_cr0)
                  + drift + 3 * dehydration_pct - 8 * acclim)
        hr = hr + (target - hr) * (1 - np.exp(-1 / 1.2))           # ~1 min lag
        noise = 0.6 * noise + rng.normal(0, HR_NOISE_SD, g)                  # smooth sensor/physio noise
        hr_obs = np.clip(hr + noise, 40, max_hr)

        i = idx
        out["core_temp"][i, t] = t_re
        out["hr"][i, t] = hr_obs
        out["skin_temp"][i, t] = t_sk
        out["met_actual"][i, t] = met
        out["sweat_loss_g"][i, t] = sweat
        out["forced_rest"][i, t] = resting_now
        out["wbgt_actual"][i, t] = wbgt_outdoor(tdb[:, t], rh[:, t], solar[:, t], wind[:, t], shade)
        out["activity"][i, t] = np.maximum(0, rng.poisson(np.maximum(0.3, 2.2 * (met - 1))) + rng.normal(0, 0.5, g))

    # Planned work (known ahead) and forecast weather (what the model sees ahead of time).
    out["workload"][idx] = met_plan * MET_TO_WM2
    hourly_error = rng.normal(0, 0.8, (g, SHIFT_MIN // 60 + 1)).repeat(60, axis=1)[:, :SHIFT_MIN]
    planned_wbgt = wbgt_outdoor(tdb, rh, solar, wind, shade_plan)
    out["wbgt"][idx] = planned_wbgt + hourly_error


def add_sensor_realism(sim: dict, seed: int = 1) -> dict:
    """Apple Watch dropouts, diode offset/drift/noise, missing sensors."""
    rng = np.random.default_rng(seed)
    n, m = sim["hr"].shape
    people = sim["people"]
    hr = sim["hr"].copy()
    for i in range(n):
        t = 0
        while t < m:                              # short watch gaps
            if rng.uniform() < 0.01:
                gap = int(rng.integers(1, 6))
                hr[i, t:t + gap] = np.nan
                t += gap
            t += 1
        if rng.uniform() < 0.02:                  # watch dies mid-shift
            hr[i, int(rng.integers(60, m)):] = np.nan
    offset = np.array([people[p].diode_offset for p in sim["person_index"]])[:, None]
    drift = np.cumsum(rng.normal(0, 0.01, (n, m)), axis=1)
    skin = sim["skin_temp"] + offset + drift + rng.normal(0, 0.1, (n, m))
    skin[rng.uniform(size=n) < 0.10] = np.nan     # diode not attached on 10% of shifts
    activity = sim["activity"].copy()
    activity[rng.uniform(size=n) < 0.05] = np.nan
    return {**sim, "hr": hr.astype(np.float32), "skin_temp": skin.astype(np.float32),
            "activity": activity.astype(np.float32), "skin_temp_true": sim["skin_temp"]}


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------
SIM_PATH = Path("data/sim/sim.npz")
SIGNAL_KEYS = ("hr", "core_temp", "skin_temp", "activity", "wbgt", "workload")


def save_sim(sim: dict, path: Path = SIM_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = {k: v for k, v in sim.items() if isinstance(v, np.ndarray)}
    np.savez_compressed(path, **arrays)
    path.with_suffix(".people.json").write_text(json.dumps([asdict(p) for p in sim["people"]]))


def load_sim(path: Path = SIM_PATH) -> list[Session]:
    """Load a saved simulation as Sessions (one per shift)."""
    z = np.load(path)
    people = json.loads(path.with_suffix(".people.json").read_text())
    sessions = []
    for i in range(len(z["person_index"])):
        p = people[int(z["person_index"][i])]
        sessions.append(Session(
            person_id=p["person_id"],
            session_id=f"{p['person_id']}-d{int(z['day'][i]):02d}",
            source="sim",
            condition="sim_shift",
            signals={k: z[k][i].astype(float) for k in SIGNAL_KEYS},
            meta={"acclim": float(z["acclim"][i]), "day": int(z["day"][i]),
                  "forced_rest": z["forced_rest"][i].astype(bool)},
        ))
    return sessions


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", type=int, default=1000)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=SIM_PATH)
    args = ap.parse_args()
    t0 = time.time()
    sim = add_sensor_realism(simulate(args.people, args.days, args.seed), seed=args.seed + 1)
    save_sim(sim, args.out)
    print(f"Saved {len(sim['person_index'])} shifts from {args.people} people to {args.out} in {time.time() - t0:.0f}s")
