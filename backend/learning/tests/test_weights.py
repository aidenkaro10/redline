"""Weight search: ranking, constraints, Pareto front, and that evolved weights stay inside safety."""

import numpy as np
import pytest

from backend.learning.gepa import _clean_weights, clamp, seed_genome, to_settings
from backend.learning.weights_search import default_weights, feasible, nudge, pareto, random_weights, rank_key
from backend.planner.weights import PlannerWeights


def r(tasks, yellow, fair=20.0, viol=0):
    return {"tasks_per_shift": tasks, "throughput": tasks / 10, "minutes_above_yellow": yellow,
            "fairness_spread": fair, "safety_violations": viol, "invalid": 0}


def test_random_and_nudged_weights_are_valid():
    rng = np.random.default_rng(0)
    for _ in range(50):
        w = random_weights(rng)
        PlannerWeights.from_dict(w)                     # raises if anything crosses into safety
        PlannerWeights.from_dict(nudge(w, rng))


def test_constraints_and_ranking():
    ref = r(5, 30)
    assert feasible(r(6, 30), ref) and not feasible(r(7, 31), ref) and not feasible(r(9, 0, viol=1), ref)
    best = max([ref, r(6, 29), r(7, 35), r(8, 10, viol=1)], key=lambda x: rank_key(x, ref))
    assert best["tasks_per_shift"] == 6                 # more work, but never more heat or any violation


def test_pareto_front():
    front = pareto([r(5, 30), r(6, 40), r(4, 10), r(4, 35), r(9, 0, viol=2)])
    assert {(x["tasks_per_shift"], x["minutes_above_yellow"]) for x in front} == {(5, 30), (6, 40), (4, 10)}


def test_evolved_weights_cant_cross_safety():
    assert _clean_weights({**default_weights(), "budget_margin_c": -0.2}) is None       # plan past the limit: dropped
    assert _clean_weights({**default_weights(), "early_line_c": 38.4}) is None          # act after yellow: dropped
    g = clamp({"settings": {**seed_genome()["settings"], "weights": {**default_weights(), "storm": 999}}})
    assert g["settings"]["weights"]["storm"] == 50
    assert isinstance(to_settings(g).weights, PlannerWeights)
