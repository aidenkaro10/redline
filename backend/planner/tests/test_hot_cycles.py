"""Tests for the hot-hour 20/10 work/rest pattern (config.PLAN_HOT_CYCLE_*). Training scenarios only."""

import numpy as np
import pytest

from backend import config
from backend.planner.generate import make_scenario
from backend.planner.heat_model import PHSHeatModel
from backend.planner.optimizer import optimize
from backend.planner.rules import check_plan
from backend.planner.scenario import REST, Plan, Scenario, Task


@pytest.fixture(scope="module")
def hot():
    sc = make_scenario(42, prefix="train", tags=["heat_dome"])
    sc.tasks = [Task("H", "dig", "heavy", 99), Task("L", "checks", "light", 99, outdoor=False)]
    return sc


def with_pattern(sc: Scenario, pattern: str) -> Scenario:
    out = Scenario.from_dict(sc.to_dict())
    out.hot_pattern = pattern
    return out


def test_only_hot_slots_are_cycled(hot):
    hot_slots = {k for k, w in enumerate(hot.weather) if w.wbgt >= config.PLAN_HOT_WBGT_C}
    assert hot_slots and with_pattern(hot, "cycles").cycled_slots() == hot_slots
    assert with_pattern(hot, "blocks").cycled_slots() == set()
    assert with_pattern(hot, "cycles").work_minutes(min(hot_slots)) == config.PLAN_HOT_CYCLE_WORK_MIN
    assert config.PLAN_HOT_CYCLE_WORK_MIN + config.PLAN_HOT_CYCLE_REST_MIN == hot.slot_min


def test_cycled_slot_counts_two_thirds(hot):
    sc = with_pattern(hot, "cycles")
    k = min(sc.cycled_slots())
    plan = Plan({w.id: [REST] * sc.n_slots for w in sc.crew})
    plan.assignments[sc.crew[0].id][k] = "H"
    plan.assignments[sc.crew[0].id][0] = "H"            # slot 0 is cool: a full unit
    assert plan.units_done(sc)["H"] == pytest.approx(1 + 2 / 3)


def test_rule_checker_understands_cycles(hot):
    """Three hot work slots in a row break the 60-min hot rule as blocks, but not as 20/10 cycles."""
    k = min(with_pattern(hot, "cycles").cycled_slots())
    row = [REST] * hot.n_slots
    row[k:k + 3] = ["L"] * 3
    plan = Plan({w.id: (row if i == 0 else [REST] * hot.n_slots) for i, w in enumerate(hot.crew)})
    rules = lambda p: {v.rule for v in check_plan(with_pattern(hot, p), plan).violations}
    assert "breaks" in rules("blocks")
    assert "breaks" not in rules("cycles")


def test_heat_model_sees_the_micro_break(hot):
    """Same heavy plan: with 20/10 cycles the core is cooler at the end of every hot slot."""
    plan = Plan({w.id: ["H"] * hot.n_slots for w in hot.crew})
    m = PHSHeatModel()
    blocks = m.simulate(with_pattern(hot, "blocks"), [plan])[0][hot.crew[0].id][:, 1]
    cycles = m.simulate(with_pattern(hot, "cycles"), [plan])[0][hot.crew[0].id][:, 1]
    ends = [(k + 1) * hot.slot_min - 1 for k in with_pattern(hot, "cycles").cycled_slots()]
    assert np.all(cycles[ends] < blocks[ends])
    first_hot = min(with_pattern(hot, "cycles").cycled_slots()) * hot.slot_min
    assert np.allclose(cycles[:first_hot], blocks[:first_hot])          # cool slots are untouched


def test_optimizer_cycles_plan_is_verified(hot):
    sc = with_pattern(hot, "cycles")
    sc.tasks = [Task("H", "dig", "heavy", 12), Task("L", "checks", "light", 12, outdoor=False)]
    r = optimize(sc, "throughput", deterministic=True)
    assert r.verified and r.check.n_safety == 0
    done = r.plan.units_done(sc)
    assert all(done[t.id] <= t.units + 1e-9 for t in sc.tasks)
