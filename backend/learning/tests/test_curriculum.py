"""Loop 4 (scenario curriculum): proposer, validator, learnability filter, test-set guard."""

import pytest

from backend.learning import curriculum
from backend.learning.curriculum import code_propose, llm_propose, run_curriculum, solve_rate
from backend.learning.fake_llm import FakeLLM
from backend.learning.scenarios import FAMILIES, build, validate
from backend.planner.generate import load_test_set
from backend.planner.optimizer import optimize
from backend.planner.scenario import all_rest_plan

import numpy as np


def fast_planner(scenario, run=0):
    """A quick stand-in for the agent so tests stay fast: one optimizer solve."""
    return optimize(scenario, "balanced", time_limit_s=1.0).plan


def test_reward_is_zero_when_always_or_never_solved(monkeypatch):
    sc = build({"seed": 2, "dead_watch": True})
    rest = solve_rate(sc, planner=lambda s, r: all_rest_plan(s), runs=2)
    assert rest["solve_rate"] == 0 and rest["reward"] == 0          # never solved: no reward
    fake = [{"solved": True, "score": 1, "workers_alerted": 0, "priority1_done": True},
            {"solved": False, "score": 0, "workers_alerted": 1, "priority1_done": True},
            {"solved": False, "score": 0, "workers_alerted": 1, "priority1_done": True},
            {"solved": False, "score": 0, "workers_alerted": 1, "priority1_done": True}]
    monkeypatch.setattr(curriculum, "outcomes", lambda s, p, r: fake)
    res = solve_rate(sc, planner=lambda s, r: all_rest_plan(s), runs=4)
    assert res["solve_rate"] == 0.25 and res["reward"] == 0.75      # Absolute Zero: 1 - solve rate


def test_code_proposer_covers_all_families():
    specs = code_propose(np.random.default_rng(0), {f: 0.5 for f in FAMILIES}, 10, 0)
    assert {s["family"] for s in specs} == set(FAMILIES)
    assert all(validate({k: v for k, v in s.items() if k != "family"})[0] is not None for s in specs[:3])


def test_llm_proposer_output_is_validated_by_code():
    llm = FakeLLM()
    specs = llm_propose(llm, [], 6)
    assert len(specs) == 6 and llm.prompts[0].startswith("TASK: PROPOSE")
    results = [validate({k: v for k, v in s.items() if k != "family"}) for s in specs]
    assert results[4][0] is None and "heat_c" in results[4][1]       # the fake's out-of-range day is rejected
    assert sum(sc is not None for sc, _ in results) >= 4


def test_curriculum_keeps_only_learnable_days():
    res = run_curriculum(rounds=1, per_round=6, llm=FakeLLM(), planner=fast_planner, runs=2, verbose=False)
    assert res["n_tried"] >= 3
    for row in res["bank"]:
        assert 0 < row["solve_rate"] < 1 and row["reward"] > 0
        assert not row["id"].startswith("test-") and row["split"] in ("train", "val")
    rejected = [l for l in res["log"] if not l["kept"]]
    assert any("heat_c" in l["reason"] for l in rejected)


def test_held_out_test_set_never_enters_the_bank():
    """RLEF split: B's test scenarios are never learned from (they can't be built from specs, and the guard checks ids)."""
    test_ids = {t.id for t in load_test_set()}
    from backend.learning.scenarios import spec_id
    assert not any(spec_id({"seed": s}) in test_ids for s in range(1000, 1012))
    from backend.planner.generate import assert_not_test
    with pytest.raises(ValueError):
        assert_not_test(load_test_set()[0])
