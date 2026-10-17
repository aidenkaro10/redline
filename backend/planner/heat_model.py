"""
Heat model: how hot each worker gets under a plan.

Two jobs, one interface (HeatModel):

1. simulate(scenario, plans)  -> the FULL model. Minute by minute core temp for every
   worker under each plan, at the 10th / 50th / 90th percentile. The rule checker
   uses this to verify plans.

2. slot_costs(scenario)       -> a SIMPLE table for the optimizer. For each worker,
   slot and activity: "if you do this for 30 min, your core temp moves toward Teq
   by a fraction alpha". That's a straight line, which CP-SAT can handle. It's an
   approximation, so every optimizer plan is re-checked with simulate().

Default implementation: PHSHeatModel, the same ISO 7933 heat strain engine as our
physiology simulator (ml/simulate.py), run as a small ensemble of 16 "what if"
copies per worker (weather a bit hotter or cooler, working a bit harder, clothes,
and how this person differs from average). The 90th percentile is taken across them.

Personal twins plug in later (ML session): a twin can set each worker's
core_offset_c / core_sd_c, or provide a whole new HeatModel with the same two methods.

Note: live heat ALERTS use the forecaster's calibrated 90th percentile, not this.
This model is for planning ahead, when there's no heart rate yet.
"""

import warnings
import zlib
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from pythermalcomfort.models import phs

from backend.planner.scenario import INTENSITIES, REST, REST_MET, REST_SHADE, Plan, Scenario, Worker

PHS_STATE = ("t_re", "t_sk", "t_cr", "t_cr_eq", "t_sk_t_cr_wg", "sweat_rate_watt", "evap_load_wm2_min")
Z90 = 1.2816                 # 90th percentile of a normal distribution, in standard deviations

# Ensemble spread (how wrong our inputs could be)
WEATHER_SD_C = 0.8           # forecast air temp error, °C (same as the simulator's forecast error)
MET_SD = 0.07                # how much harder/easier the work really is (fraction)
CLO_SD = 0.05                # clothing
N_MEMBERS = 16

# An activity is "rest" or (intensity, outdoor). Indoor work has no sun.
Activity = str | tuple


def activity_of(scenario: Scenario, worker: Worker, slot: int, assignment: str) -> Activity:
    """What a worker is physically doing in a slot. Off-site or unknown counts as rest."""
    if not worker.is_available(slot) or assignment == REST:
        return REST
    try:
        t = scenario.task(assignment)
    except StopIteration:
        return REST          # the rule checker reports unknown task ids separately
    return (t.intensity, t.outdoor)


def met_shade(activity: Activity) -> tuple[float, float]:
    """Metabolic rate (met) and shade fraction for an activity."""
    if activity == REST:
        return REST_MET, REST_SHADE
    intensity, outdoor = activity
    met, shade = INTENSITIES[intensity]
    return met, (shade if outdoor else 1.0)


def mean_radiant(tdb, solar, shade):
    """Mean radiant temperature, same formula as ml/simulate.py."""
    return tdb + 2 + 15 * solar / 1000 * (1 - shade)


@dataclass
class CostTable:
    """
    Linear heat model for the optimizer, at the cautious (90th pct) end.
    Over one slot of activity a:  T_next = (1 - alpha) * T_now + alpha * teq
    alpha[w][s][a], teq[w][s][a] in °C;  start[w] = 90th pct core temp at shift start.
    """
    alpha: dict
    teq: dict
    start: dict
    activities: list


class HeatModel(Protocol):
    name: str

    def simulate(self, scenario: Scenario, plans: list[Plan]) -> list[dict[str, np.ndarray]]:
        """For each plan: {worker_id: array (minutes, 3)} core temp at the 10/50/90th pct."""
        ...

    def slot_costs(self, scenario: Scenario) -> CostTable:
        ...


class PHSHeatModel:
    """ISO 7933 Predicted Heat Strain, as a 16-member ensemble per worker."""

    name = "phs-ensemble"

    def __init__(self, n_members: int = N_MEMBERS):
        self.n_members = n_members

    # --- ensemble members -------------------------------------------------
    def _members(self, scenario: Scenario, worker: Worker) -> dict[str, np.ndarray]:
        """Fixed random perturbations per (scenario, worker), so results are repeatable."""
        seed = zlib.crc32(f"{scenario.id}|{worker.id}".encode())
        rng = np.random.default_rng(seed)
        k = self.n_members
        return {
            "dtdb": rng.normal(0, WEATHER_SD_C, k),
            "met_x": np.clip(rng.normal(1, MET_SD, k), 0.8, 1.2),
            "clo": np.clip(worker.clo + rng.normal(0, CLO_SD, k), 0.3, 1.0),
            "offset": worker.core_offset_c + worker.core_sd_c * rng.standard_normal(k),
        }

    # --- the full model -----------------------------------------------------
    def simulate(self, scenario: Scenario, plans: list[Plan]) -> list[dict[str, np.ndarray]]:
        n_min = scenario.n_slots * scenario.slot_min
        rows = []   # one row per (plan, worker, member)
        for p_i, plan in enumerate(plans):
            for w in scenario.crew:
                mem = self._members(scenario, w)
                slots = plan.assignments.get(w.id, [REST] * scenario.n_slots)
                acts = [activity_of(scenario, w, s, slots[s] if s < len(slots) else REST)
                        for s in range(scenario.n_slots)]
                ms = np.array([met_shade(a) for a in acts])                     # (slots, 2)
                outdoor = np.array([a != REST and a[1] for a in acts], float)
                for m in range(self.n_members):
                    rows.append({"plan": p_i, "worker": w, "met": ms[:, 0] * mem["met_x"][m],
                                 "shade": ms[:, 1], "indoor": 1 - outdoor, "dtdb": mem["dtdb"][m],
                                 "clo": mem["clo"][m], "offset": mem["offset"][m]})
        core = self._run(scenario, rows, n_min)                                  # (rows, minutes)

        out = [{} for _ in plans]
        k = self.n_members
        for r0 in range(0, len(rows), k):
            r = rows[r0]
            q = np.percentile(core[r0:r0 + k], [10, 50, 90], axis=0).T           # (minutes, 3)
            out[r["plan"]][r["worker"].id] = q
        return out

    def _run(self, scenario: Scenario, rows: list[dict], n_min: int) -> np.ndarray:
        """Step PHS minute by minute. Rows sharing body size + acclimatization go in one call."""
        sm = scenario.slot_min
        wx = scenario.weather
        tdb_s = np.array([w.tdb for w in wx]); rh_s = np.array([w.rh for w in wx])
        sol_s = np.array([w.solar for w in wx]); wind_s = np.array([w.wind for w in wx])
        core = np.zeros((len(rows), n_min))
        groups = {}
        for i, r in enumerate(rows):
            w = r["worker"]
            groups.setdefault((w.weight, w.height, 100 if w.acclim >= 0.5 else 0), []).append(i)
        for (weight, height, acc), idx in groups.items():
            idx = np.array(idx)
            g = [rows[i] for i in idx]
            met = np.stack([r["met"] for r in g]); shade = np.stack([r["shade"] for r in g])
            indoor = np.stack([r["indoor"] for r in g])
            dtdb = np.array([r["dtdb"] for r in g]); clo = np.array([r["clo"] for r in g])
            offset = np.array([r["offset"] for r in g])
            starts = np.array([r["worker"].start_core_c if r["worker"].start_core_c is not None else np.nan
                               for r in g])
            state = {}
            if not np.all(np.isnan(starts)):
                s0 = np.where(np.isnan(starts), 36.8, starts - offset)   # model space = estimate minus personal offset
                state = {"t_cr": s0, "t_re": s0}
            for t in range(n_min):
                s = t // sm
                tdb = tdb_s[s] + dtdb
                solar = sol_s[s] * (1 - indoor[:, s])
                tr = mean_radiant(tdb, solar, shade[:, s])
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    res = phs(tdb=tdb, tr=tr, v=np.full(len(g), wind_s[s]), rh=np.full(len(g), rh_s[s]),
                              met=met[:, s], clo=clo, posture="standing", round_output=False,
                              limit_inputs=False, model="7933-2004", weight=weight, height=height,
                              acclimatized=acc, duration=1, **state)
                state = {k: np.asarray(getattr(res, k), dtype=float) for k in PHS_STATE}
                core[idx, t] = state["t_re"] + offset
        return core

    # --- the simple table for the optimizer --------------------------------
    def slot_costs(self, scenario: Scenario) -> CostTable:
        """
        Fit T_next = (1 - alpha) * T_now + alpha * teq for every (worker, slot, activity)
        by running 30 min of PHS from two starting temps (37.0 and 38.0 °C).
        Uses one cautious member: a bit hotter weather, a bit harder work, plus the
        90th pct of the personal offset. The full-model check catches what this misses.
        """
        acts = [REST] + sorted({(t.intensity, t.outdoor) for t in scenario.tasks})
        sm, n_s = scenario.slot_min, scenario.n_slots
        lo, hi = 37.0, 38.0
        rows = []
        for w in scenario.crew:
            for s in range(n_s):
                for a in acts:
                    for t0 in (lo, hi):
                        rows.append((w, s, a, t0))
        groups = {}
        for i, (w, *_rest) in enumerate(rows):
            groups.setdefault((w.weight, w.height, 100 if w.acclim >= 0.5 else 0), []).append(i)
        end = np.zeros(len(rows))
        for (weight, height, acc), idx in groups.items():
            g = [rows[i] for i in idx]
            wx = [scenario.weather[s] for (_, s, _, _) in g]
            ms = np.array([met_shade(a) for (_, _, a, _) in g])
            indoor = np.array([0.0 if a == REST else float(not a[1]) for (_, _, a, _) in g])
            tdb = np.array([x.tdb for x in wx]) + Z90 * WEATHER_SD_C
            solar = np.array([x.solar for x in wx]) * (1 - indoor)
            tr = mean_radiant(tdb, solar, ms[:, 1])
            clo = np.array([w.clo for (w, *_r) in g]) + Z90 * CLO_SD
            met = ms[:, 0] * (1 + Z90 * MET_SD)
            t0 = np.array([r[3] for r in g])
            state = {"t_cr": t0, "t_re": t0}
            for _ in range(sm):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    res = phs(tdb=tdb, tr=tr, v=np.array([x.wind for x in wx]), rh=np.array([x.rh for x in wx]),
                              met=met, clo=clo, posture="standing", round_output=False, limit_inputs=False,
                              model="7933-2004", weight=weight, height=height, acclimatized=acc,
                              duration=1, **state)
                state = {k: np.asarray(getattr(res, k), dtype=float) for k in PHS_STATE}
            end[idx] = state["t_re"]

        alpha, teq, start = {}, {}, {}
        i = 0
        for w in scenario.crew:
            off = w.core_offset_c + Z90 * w.core_sd_c
            alpha[w.id], teq[w.id] = [], []
            for s in range(n_s):
                a_row, t_row = {}, {}
                for a in acts:
                    e_lo, e_hi = end[i], end[i + 1]
                    i += 2
                    keep = np.clip((e_hi - e_lo) / (hi - lo), 0.02, 0.98)       # = 1 - alpha
                    al = 1 - keep
                    a_row[a] = float(al)
                    t_row[a] = float((e_lo - keep * lo) / al + off)
                alpha[w.id].append(a_row)
                teq[w.id].append(t_row)
            base = w.start_core_c - w.core_offset_c if w.start_core_c is not None else 36.8
            start[w.id] = float(base + off)
        return CostTable(alpha=alpha, teq=teq, start=start, activities=acts)


def default_heat_model() -> HeatModel:
    return PHSHeatModel()
