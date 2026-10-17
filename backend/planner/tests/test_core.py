"""Tests for the scenario format, heat model, rule checker and optimizer."""

import numpy as np
import pytest

from backend import config
from backend.planner.generate import TEST_DIR, assert_not_test, demo_scenario, load_test_set, make_scenario
from backend.planner.heat_model import PHSHeatModel
from backend.planner.optimizer import optimize
from backend.planner.rules import check_plan
from backend.planner.scenario import INTENSITIES, REST, REST_MET, REST_SHADE, HazardWindow, Plan, Scenario, all_rest_plan


@pytest.fixture(scope="module")
def demo():
    return demo_scenario()


def test_intensities_match_the_simulator():
    from ml import simulate
    assert INTENSITIES == simulate.TASKS
    assert (REST_MET, REST_SHADE) == (simulate.REST_MET, simulate.REST_SHADE)


def test_scenario_json_round_trip(demo, tmp_path):
    demo.save(tmp_path / "s.json")
    again = Scenario.load(tmp_path / "s.json")
    assert again.to_dict() == demo.to_dict()


def test_test_set_is_fixed_and_guarded():
    tests = load_test_set()
    assert len(tests) == 12 and len(list(TEST_DIR.glob("*.json"))) == 12
    # regenerating from the seeds gives the same files (the set can't silently drift)
    from backend.planner.generate import TEST_SEEDS, TEST_TAGS
    assert make_scenario(TEST_SEEDS[0], "test", TEST_TAGS[0]).to_dict() == tests[0].to_dict()
    with pytest.raises(ValueError):
        assert_not_test(tests[0])
    assert_not_test(make_scenario(3))   # training scenarios are fine


def test_lightning_window_gets_30_min_all_clear(demo):
    lightning = [h for h in demo.hazards if h.kind == "lightning"][0]
    blocked = demo.stop_work_slots()
    assert set(range(lightning.start_slot, min(demo.n_slots, lightning.end_slot + 1))) <= blocked   # +1 slot = 30 min
    assert HazardWindow("heat_advisory", 0, 4).stops_outdoor_work() is False


def test_heat_model_harder_work_is_hotter(demo):
    task = demo.tasks[0]
    light = Plan({w.id: [REST] * demo.n_slots for w in demo.crew})
    heavy = Plan({w.id: [REST] * demo.n_slots for w in demo.crew})
    demo_t = Scenario.from_dict(demo.to_dict())
    demo_t.tasks = [type(task)(id="vh", name="dig", intensity="very_heavy", units=99)]
    heavy.assignments = {w.id: ["vh"] * demo.n_slots for w in demo.crew}
    a, b = PHSHeatModel().simulate(demo_t, [light, heavy])
    w = demo.crew[0].id
    assert a[w].shape == (demo.n_slots * demo.slot_min, 3)
    assert np.all(a[w][:, 0] <= a[w][:, 1]) and np.all(a[w][:, 1] <= a[w][:, 2])   # p10 <= p50 <= p90
    assert b[w][:, 2].max() > a[w][:, 2].max() + 0.5


def test_rule_checker_catches_each_rule(demo):
    # Everyone does the hardest outdoor task, all day, no breaks.
    t = max(demo.tasks, key=lambda t: (t.outdoor, list(INTENSITIES).index(t.intensity)))
    for w in demo.crew:
        if t.skill:
            w.skills.append(t.skill)
    plan = Plan({w.id: [t.id] * demo.n_slots for w in demo.crew})
    rules = {v.rule for v in check_plan(demo, plan).violations}
    assert {"heat", "breaks", "lightning"} <= rules
    assert not check_plan(demo, Plan({"w1": [REST]})).ok          # wrong shape is rejected


def test_all_rest_is_safe(demo):
    r = check_plan(demo, all_rest_plan(demo))
    assert r.ok and max(r.peak_p90.values()) < config.PLAN_MAX_P90_C


def test_optimizer_plan_is_verified_and_useful(demo):
    r = optimize(demo, "throughput")
    assert r.verified and r.check.ok and r.check.n_safety == 0
    assert sum(r.plan.units_done(demo).values()) > 0
    assert r.plan.source.startswith("optimizer")


def test_optimizer_respects_what_if_forbid(demo):
    from dataclasses import replace
    from backend.planner.optimizer import OBJECTIVES
    t = demo.tasks[0]
    obj = replace(OBJECTIVES["throughput"], forbid=[("w1", None, t.id)])
    r = optimize(demo, obj)
    assert r.verified and t.id not in r.plan.assignments["w1"]
