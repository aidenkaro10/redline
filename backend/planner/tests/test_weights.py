"""Tests for PlannerWeights and score(weights, scenarios). Small and quick (CPU is shared)."""

import pytest

from backend.planner.benchmark import score
from backend.planner.generate import demo_scenario
from backend.planner.optimizer import OBJECTIVES, objectives_for
from backend.planner.weights import PlannerWeights


def test_defaults_reproduce_original_presets():
    old = {"throughput": (10, 0, 0, 0.05), "balanced": (10, 1, 1, 0.10), "margin": (10, 3, 0, 0.20),
           "fairness": (10, 1, 4, 0.10)}
    for name, (tp, m, f, b) in old.items():
        o = OBJECTIVES[name]
        assert (o.throughput, o.margin, o.fairness) == (tp, m, f) and o.budget_margin_c == pytest.approx(b)


def test_weights_cant_reach_safety():
    with pytest.raises(ValueError):
        PlannerWeights(early_line_c=38.2).validate()          # above the yellow line
    with pytest.raises(ValueError):
        PlannerWeights(budget_margin_c=-0.1).validate()       # planning past the limit
    with pytest.raises(ValueError):
        PlannerWeights(storm=-1).validate()
    w = PlannerWeights.from_dict({**PlannerWeights().to_dict(), "priority_values": {"1": 20, "2": 5, "3": 1}, "junk": 1})
    assert w.priority_values == {1: 20, 2: 5, 3: 1}
    assert objectives_for(PlannerWeights(margin=2)).get("margin").margin == 5


def test_score_returns_metrics_and_zero_violations():
    m = score(PlannerWeights(), [demo_scenario()])
    for key in ("tasks_completed_per_shift", "throughput", "minutes_above_yellow", "peak_p90_c",
                "fairness_spread", "safety_violations"):
        assert key in m
    assert m["safety_violations"] == 0 and m["peak_p90_c"] < 38.0 and 0 < m["throughput"] <= 1
    cautious = score(PlannerWeights(budget_margin_c=0.4), [demo_scenario()])
    assert cautious["peak_p90_c"] <= m["peak_p90_c"] + 1e-6 and cautious["safety_violations"] == 0
