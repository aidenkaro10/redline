"""Nightly loop: profiles learn with shrinkage + cap, only the hotter side reaches the planner, risk + calibration."""

import numpy as np

from backend.learning.nightly import (apply_profiles, belief_simulate, calibration, make_variations, new_profiles,
                                      profile_view, risk_report, run_nightly)
from backend.learning.fake_llm import FakeLLM
from backend.learning.scenarios import build
from backend.planner.scenario import REST, Plan


def test_profile_shrinkage_cap_and_hot_side_only():
    sc = build({"seed": 2, "dead_watch": True})
    prof = new_profiles(sc.crew)
    prof["Kofi"]["misses"] = [-0.3] * 2            # model read him 0.3 °C too cool, twice
    v = profile_view(prof["Kofi"])
    assert abs(v["runs_hotter_c"] - 0.15) < 1e-9   # shrunk: 0.6 / (2 + 2 prior checks)
    prof["Kofi"]["misses"] = [-1.4] * 200
    assert profile_view(prof["Kofi"])["runs_hotter_c"] == 0.6      # capped like personal_offset.py
    prof["Ana"]["misses"] = [0.5] * 20             # Ana runs cooler than the model
    psc = apply_profiles(sc, prof)
    assert next(w for w in psc.crew if w.name.startswith("Ana")).core_offset_c == 0.0    # never adds heat for "cooler"
    assert next(w for w in psc.crew if w.name.startswith("Kofi")).core_offset_c == 0.6
    assert next(w for w in sc.crew if w.name.startswith("Kofi")).core_offset_c == 0.0   # original untouched


def test_risk_number_rises_with_a_hotter_belief():
    sc = build({"seed": 3, "dead_watch": True, "heat_c": 3, "heavy_bias": 0.8})
    t = next(t for t in sc.tasks if t.intensity in ("moderate", "heavy", "very_heavy"))
    plan = Plan({w.id: ([t.id, t.id, REST] * 3)[:sc.n_slots] for w in sc.crew})
    rng = np.random.default_rng(0)
    cool = risk_report(sc, plan, make_variations(sc, new_profiles(sc.crew), 0.5, 10, rng))
    hot = new_profiles(sc.crew)
    for p in hot.values():
        p["misses"] = [-0.8] * 30
    hot_r = risk_report(sc, plan, make_variations(sc, hot, 0.5, 10, np.random.default_rng(0)))
    assert hot_r["risk"] >= cool["risk"] and hot_r["risk_n"] == 10 and "worker" in hot_r["weak_spot"]


def test_calibration_bins():
    rows = [{"risk": 0.05, "actual_anyone_above_yellow": 0.0}, {"risk": 0.5, "actual_anyone_above_yellow": 0.5},
            {"risk": 0.9, "actual_anyone_above_yellow": 1.0}]
    c = calibration(rows)
    assert len(c["bins"]) == 3 and c["brier"] < 0.01


def test_two_simulated_nights_end_to_end(monkeypatch):
    import backend.learning.nightly as n
    monkeypatch.setattr(n, "N_VARIATIONS", 4)
    monkeypatch.setattr(n, "N_PRACTICE", 2)
    monkeypatch.setattr(n, "N_GEPA_VAL", 1)
    fast = {"objectives": ["balanced"], "fuse": False, "time_limit_s": 1.0}
    orig = n.seed_genome
    monkeypatch.setattr(n, "seed_genome", lambda: {"settings": {**orig()["settings"], **fast}, "strategy_note": ""})
    days = [build({"seed": s, "dead_watch": True, "heat_c": 3, "heavy_bias": 0.7}) for s in (41, 42)]
    gate = [build({"seed": 43, "dead_watch": True, "heat_c": 3, "heavy_bias": 0.7})]
    run = run_nightly(days, gate, llm=FakeLLM(), verbose=False)
    assert len(run["timeline"]) == 2
    for e in run["timeline"]:
        assert e["safety_violations"] == 0 and 0 <= e["forecast_risk"]["risk"] <= 1
        assert all(p["checks"] > 0 for p in e["profiles"])
    six = run["timeline"][0]["six_pm"]
    assert six["for_day"] == 2 and six["rule_checker_ok"] and "of 4 variations" in six["risk_text"]
