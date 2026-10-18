"""Demo JSON shapes (the frontend reads these): playbook growth and the family tree."""

from backend.learning.demo import family_tree, playbook_growth


def test_playbook_growth_shape():
    gate = {"score": 0.4, "safety_violations": 0}
    run = {"timeline": [{"day": 1, "scenario": "cur-x", "tags": ["heat_dome", "home_crew"],
                         "outcome": {"score": 0.3, "workers_alerted": 1}, "lessons_used": [],
                         "reflection": {"root_cause": "Kofi ran hot", "by": "code"},
                         "operations": [{"op": "add", "text": "t", "result": "applied"}],
                         "gate": {"kept": True, "gain": 0.05, "why": "better", "before": gate,
                                  "after": {"score": 0.45, "safety_violations": 0}},
                         "playbook_version": 1, "n_lessons": 1, "safety_violations": 0}],
           "versions": [{"version": 0, "day": 0, "gate": gate, "lessons": []}], "llm": {"calls": 0}}
    out = playbook_growth(run)
    d = out["days"][0]
    assert d["tags"] == ["heat_dome"] and d["gate"]["kept"] and d["gate"]["score_after"] == 0.45
    assert out["label"].startswith("Simulated")


def test_family_tree_shape():
    g = {"settings": {"objectives": ["balanced"], "fuse": True, "w_peak": 0.5, "w_fair": 0.002, "w_complaint": 0.03,
                      "n_llm_ideas": 3}, "strategy_note": ""}
    val = {"score": 0.4, "strain_margin_c": 0.2, "throughput": 0.5, "fairness": -20, "latency": -5, "cost": 0,
           "workers_alerted": 0.1, "safety_violations": 0}
    run = {"archive": [{"id": "a0", "parent": None, "generation": 0, "accepted": True, "why": "seed", "genome": g, "val": val},
                       {"id": "a1", "parent": "a0", "generation": 1, "accepted": False, "why": "w_peak up", "genome": g,
                        "val": None, "mutator": "code", "minibatch": {"parent": 0.3, "child": 0.2}}],
           "best": "a0", "seed": "a0", "pareto_front": ["a0"], "gain_val": 0.0, "llm": {}}
    t = family_tree(run)
    assert [n["id"] for n in t["nodes"]] == ["a0", "a1"]
    assert t["nodes"][0]["pareto"] and t["nodes"][0]["best"] and t["nodes"][1]["scores"] is None
