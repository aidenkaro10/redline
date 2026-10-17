import dataclasses

import numpy as np

from ml.curriculum.learnability import filter_learnable, learnability_reward, summarize
from ml.curriculum.proposer import ScenarioResult, ShiftScore, score_shift
from ml.curriculum.rounds import gate, rehearsal_sample
from ml.curriculum.scenario import KNOBS, Scenario, mutate, random_scenario, simulate_scenarios
from ml.data import Session
from ml.evaluate import HORIZONS, QUANTILES, Prediction


# --- learnability filter -----------------------------------------------------
def test_reward_is_zero_for_easy_and_hopeless():
    assert learnability_reward(1.0) == 0.0
    assert learnability_reward(0.0) == 0.0
    assert learnability_reward(0.25) == 0.75       # harder -> more reward
    assert learnability_reward(0.75) == 0.25


def _result(solve_rate, tmax=33.0, n=4):
    solved = int(round(solve_rate * n))
    shifts = [ShiftScore(0.9, 0.2, i < solved) for i in range(n)]
    return ScenarioResult(Scenario(tmax=tmax), shifts)


def test_filter_keeps_only_sometimes_solved_and_drops_duplicates():
    results = [_result(1.0, 30), _result(0.0, 31), _result(0.5, 33), _result(0.25, 36), _result(0.25, 36.1)]
    kept = filter_learnable(results)
    assert [r.scenario.tmax for r in kept] == [36, 33]     # most learnable first; 36.1 is a near-duplicate of 36
    assert summarize(results) == {"tried": 5, "always_solved": 1, "learnable": 3, "never_solved": 1}


# --- solved / not solved -----------------------------------------------------
def _session_and_pred(offset):
    n = 120
    ct = 37.0 + np.linspace(0, 1.0, n)
    s = Session("p", "p-s", "sim", "x", {"hr": np.full(n, 120.0), "core_temp": ct})
    fc = {}
    for h in HORIZONS:
        future = np.full(n, np.nan)
        future[:n - h] = ct[h:] + offset
        fc[h] = {q: future + {0.1: -0.2, 0.5: 0.0, 0.9: 0.2}[q] for q in QUANTILES}
    return s, Prediction(now=ct, forecast=fc)


def test_score_shift_solved_and_missed():
    s, good = _session_and_pred(0.0)
    assert score_shift(s, good).solved
    s, too_low = _session_and_pred(-0.5)           # 90th percentile sits under the truth: a miss
    sc = score_shift(s, too_low)
    assert not sc.solved and sc.coverage_p90 == 0.0


# --- scenarios ---------------------------------------------------------------
def test_mutate_stays_inside_ranges():
    rng = np.random.default_rng(0)
    s = random_scenario(rng)
    for _ in range(50):
        s = mutate(s, rng, scale=0.5)
        for k, (lo, hi) in KNOBS.items():
            assert lo <= getattr(s, k) <= hi


def test_knobs_change_the_simulation():
    hot = Scenario(tmax=38.0, humidity=1.0, heavy_job=1.0, fitness=0.0, acclim=0.0, clo=1.1, stop_temp=39.8,
                   watch_dies=1.0, skin_missing=1.0)
    mild = dataclasses.replace(Scenario(), tmax=29.0, humidity=0.0, heavy_job=0.0, clo=0.45)
    out = simulate_scenarios([hot, mild], shifts_each=2, seed=0)
    assert [len(x) for x in out] == [2, 2]
    peak = lambda shifts: np.mean([np.nanmax(s.signals["core_temp"]) for s in shifts])
    assert peak(out[0]) > peak(out[1]) + 0.3       # hot heavy day in coveralls runs hotter
    hot_shift = out[0][0]
    assert np.all(np.isnan(hot_shift.signals["skin_temp"]))       # diode off
    assert np.isnan(hot_shift.signals["hr"][-1])                  # watch died
    assert hot_shift.person_id != out[1][0].person_id


# --- gate ----------------------------------------------------------------------
BEST = {"qloss": 0.08, "now_rmse": 0.24, "fc_rmse": 0.28, "red_caught_pct": 75.0}


def test_gate_keeps_only_real_improvements():
    assert gate({**BEST, "qloss": 0.07}, BEST)[0]
    assert not gate({**BEST, "qloss": 0.08}, BEST)[0]                          # no improvement
    assert not gate({**BEST, "qloss": 0.07, "fc_rmse": 0.30}, BEST)[0]         # RMSE got worse
    assert not gate({**BEST, "qloss": 0.07, "red_caught_pct": 62.5}, BEST)[0]  # missed a red episode
    assert gate({**BEST, "qloss": 0.07, "now_rmse": 0.243}, BEST)[0]           # within noise tolerance


def test_gate_min_gain_blocks_gains_smaller_than_noise():
    assert not gate({**BEST, "qloss": 0.0795}, BEST, min_gain=0.001)[0]
    assert gate({**BEST, "qloss": 0.0785}, BEST, min_gain=0.001)[0]


def test_rehearsal_sample_is_half_curriculum():
    base, cur = [("b", i) for i in range(100)], [("c", i) for i in range(30)]
    out = rehearsal_sample(base, cur, 20, seed=0)
    assert len(out) == 20 and sum(x[0] == "c" for x in out) == 10 and len(set(out)) == 20
    assert all(x[0] == "b" for x in rehearsal_sample(base, [], 20, seed=0))
