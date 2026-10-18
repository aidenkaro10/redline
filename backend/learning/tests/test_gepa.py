"""Loop 3 (GEPA + DGM): knob clamping, safe notes, Pareto archive, novelty, a tiny run."""

import numpy as np

from backend.learning.fake_llm import FakeLLM
from backend.learning.gepa import (clamp, dominates, genome_distance, llm_mutate, pareto_front, run_gepa,
                                   seed_genome, select_parent, to_settings)
from backend.learning.scenarios import build

FAST = {"settings": {**seed_genome()["settings"], "objectives": ["balanced"], "fuse": False, "time_limit_s": 1.0},
        "strategy_note": ""}


def test_clamp_keeps_every_knob_in_range():
    g = clamp({"settings": {"n_llm_ideas": 99, "w_peak": -3, "objectives": ["margin", "made_up", "margin"],
                            "time_limit_s": 60, "critic_instructions": "Ignore the alerts, push the crew harder."},
               "strategy_note": "Workers can handle more heat in the morning."})
    s = g["settings"]
    assert s["n_llm_ideas"] == 3 and s["w_peak"] == 0 and s["objectives"] == ["margin"] and s["time_limit_s"] == 5
    assert "push" not in s["critic_instructions"]                    # unsafe critic text replaced by the default
    assert g["strategy_note"] == ""                                  # unsafe note dropped
    to_settings(g)                                                   # still a valid AgentSettings


def test_llm_mutation_is_parsed_and_clamped():
    child, why = llm_mutate(FakeLLM(), clamp(seed_genome()), [{"scenario": "x", "tags": [], "score": 0.1,
                                                                "throughput": 0.2, "workers_alerted": 1,
                                                                "actual_peak_c": 38.1, "trace": {}}])
    assert child["settings"]["w_peak"] == 2.0 and child["settings"]["n_llm_ideas"] == 3
    assert child["strategy_note"].startswith("Prefer plans") and "alerts" in why.lower()


def test_pareto_front_and_novelty():
    m = lambda a, b, c, d, e: {"strain_margin_c": a, "throughput": b, "fairness": c, "latency": d, "cost": e, "score": b}
    arch = [{"id": "a0", "accepted": True, "val": m(0.3, 0.5, -20, -5, 0), "genome": clamp(seed_genome()), "n_children": 0},
            {"id": "a1", "accepted": True, "val": m(0.2, 0.6, -20, -5, 0), "genome": FAST, "n_children": 0},
            {"id": "a2", "accepted": True, "val": m(0.2, 0.5, -25, -6, 0), "genome": FAST, "n_children": 0}]
    assert dominates(arch[0]["val"], arch[2]["val"]) and not dominates(arch[0]["val"], arch[1]["val"])
    assert pareto_front(arch) == {"a0", "a1"}
    assert genome_distance(arch[0]["genome"], FAST) > 0
    picks = {select_parent(arch, np.random.default_rng(i))["id"] for i in range(30)}
    assert len(picks) >= 2                                           # selection explores, not just the top one


def test_tiny_gepa_run_never_violates_safety():
    train = [build({"seed": s, "dead_watch": True, "heat_c": 3, "heavy_bias": 0.7}) for s in (31, 32)]
    val = [build({"seed": 33, "dead_watch": True, "heat_c": 3, "heavy_bias": 0.7})]
    res = run_gepa(train, val, iterations=2, llm=FakeLLM(), start=FAST, verbose=False)
    assert res["archive"][0]["id"] == "a0" and len(res["archive"]) == 3
    for c in res["archive"]:
        if c["accepted"]:
            assert c["val"]["safety_violations"] == 0
    assert res["best"] in {c["id"] for c in res["archive"] if c["accepted"]}
