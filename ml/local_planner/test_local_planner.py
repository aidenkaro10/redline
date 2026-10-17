"""Small tests for the local planner. No network, no model download, no GPU needed."""

import json

import pytest

from backend.planner.archon import IDEAS_SYSTEM
from backend.planner.baselines import LLM_SYSTEM
from backend.planner.generate import load_test_set, make_scenario
from ml.local_planner import dataset, scenarios, teacher
from ml.local_planner.evaluate import parse_plan, summarize


def test_test_scenarios_are_refused():
    """The held-out test set can never become training data, by id or by content."""
    test = load_test_set()[0]
    with pytest.raises(ValueError):
        scenarios.is_safe_to_learn_from(test)
    copy = make_scenario(int(test.id.split("-")[1]), prefix="train", tags=test.tags)   # same day, new name
    assert not scenarios.is_safe_to_learn_from(copy)
    assert scenarios.is_safe_to_learn_from(make_scenario(3, prefix="train"))


def test_split_is_stable_and_mostly_train():
    ids = [f"train-{i}" for i in range(500)]
    splits = [scenarios.split_of(i) for i in ids]
    assert splits == [scenarios.split_of(i) for i in ids]
    assert 0.05 < splits.count("val") / len(ids) < 0.2


def test_budget_refuses_calls_over_the_cap():
    b = teacher.Budget(cap_usd=1.0, already_spent=0.7)
    b.reserve(0.2)                       # 0.9 <= 1.0: fine
    with pytest.raises(teacher.BudgetExceeded):
        b.reserve(0.2)                   # 0.7 + 0.2 in flight + 0.2 > 1.0
    assert b.settle(0.2, 0.05) == pytest.approx(0.75)
    b.reserve(0.2)                       # in flight went back down


def test_call_kind():
    assert teacher.call_kind(IDEAS_SYSTEM, "Scenario:\n{...}") == "ideas"
    assert teacher.call_kind(IDEAS_SYSTEM, "Check...\n\nVerified plans:\n{}") == "critic"
    assert teacher.call_kind(None, "Write 3 short plain-language bullet points ...") == "explain"


def _fake_run(sc, final_ok=True, throughput=0.7, direct_ok=False, direct_rank=0.1):
    rest = {w.id: ["rest"] * sc.n_slots for w in sc.crew}
    work = {w.id: [sc.tasks[0].id] + ["rest"] * (sc.n_slots - 1) for w in sc.crew}
    return {
        "scenario": sc.to_dict(), "split": "train",
        "final": {"assignments": work, "source": "agent:fused", "ok": final_ok, "n_safety": 0,
                  "summary": "", "reasons": ["**Short day.** Storm at 2.", "Kofi stays light.", "Peak 90th pct core temp 37.9"]},
        "final_score": {"throughput": throughput, "rank": 0.4},
        "candidates": [{"name": "optimizer:throughput", "ok": True, "score": {"rank": 0.38}},
                       {"name": "llm:direct", "ok": direct_ok, "score": {"rank": direct_rank}, "assignments": rest}],
        "calls": [{"kind": "ideas", "system": IDEAS_SYSTEM, "prompt": "Scenario:\n...",
                   "response": json.dumps({"ideas": [{"name": "early", "why": "cool", "objective": "margin",
                                                      "soft": [{"worker": "w1", "intensities": ["heavy"], "from_slot": 8,
                                                                "to_slot": 16, "penalty": 10},
                                                               {"worker": "nobody", "intensities": ["heavy"], "penalty": 10}],
                                                      "forbid": [["w1", None, "t999"]]}],
                                           "direct_plan": {"assignments": rest}})},
                  {"kind": "critic", "skipped": True, "cost_usd": 0.0},
                  {"kind": "explain", "system": None, "prompt": "Write 3 short ...", "response": "- a\n- b"}],
    }


def test_dataset_keeps_only_verified_targets():
    sc = make_scenario(3, prefix="train")
    exs, source = dataset.examples_from_run(_fake_run(sc))
    assert source == "agent_final" and [e["kind"] for e in exs] == ["plan", "ideas", "explain"]
    plan = json.loads(exs[0]["messages"][-1]["content"])
    assert plan["assignments"] == _fake_run(sc)["final"]["assignments"]
    assert plan["notes"] == "Short day. Kofi stays light."
    assert exs[0]["messages"][0]["content"] == LLM_SYSTEM
    ideas = json.loads(exs[1]["messages"][-1]["content"])
    assert ideas["direct_plan"]["assignments"] == plan["assignments"]       # verified plan, not Claude's
    assert len(ideas["ideas"][0]["soft"]) == 1 and ideas["ideas"][0]["forbid"] == []
    # Claude's own direct plan is used when it passed and scored as well
    _, source = dataset.examples_from_run(_fake_run(sc, direct_ok=True, direct_rank=0.39))
    assert source == "claude_direct"
    # unverified or low-throughput finals teach nothing
    assert dataset.examples_from_run(_fake_run(sc, final_ok=False))[0] == []
    assert dataset.examples_from_run(_fake_run(sc, throughput=0.1))[0] == []


def test_dataset_refuses_test_scenarios():
    run = _fake_run(load_test_set()[0])
    with pytest.raises(ValueError):
        dataset.examples_from_run(run)


def test_parse_plan_and_summary():
    p = parse_plan('```json\n{"assignments": {"w1": ["t1", "rest"]}, "notes": "hi"}\n```', "x")
    assert p.assignments == {"w1": ["t1", "rest"]} and p.notes == "hi"
    assert parse_plan("no json here", "x").assignments == {}
    rows = [{"scenario": "a", "success": True, "valid": True, "n_safety": 0, "throughput": 0.6, "peak_p90": 37.9,
             "fairness": 10, "latency_s": 2.0, "cost_usd": 0.0},
            {"scenario": "a", "success": False, "valid": True, "n_safety": 2, "throughput": 0.8, "peak_p90": 38.4,
             "fairness": 12, "latency_s": 4.0, "cost_usd": 0.0}]
    s = summarize(rows)
    assert s["pass_at_1"] == 0.5 and s["pass_all_runs"] == 0.0 and s["safety_violations_per_run"] == 1.0
    assert s["throughput_if_passed"] == 0.6


def test_compact_prompt_keeps_the_plan_facts():
    from backend.planner.archon import plan_view
    from backend.planner.scenario import Plan
    from ml.local_planner.prompts import compact_prompt
    sc = make_scenario(3, prefix="train")
    plan = Plan({w.id: [sc.tasks[0].id] * sc.n_slots for w in sc.crew}, source="optimizer:throughput")
    view = json.dumps(plan_view(sc, plan))
    prompt = f"Scenario:\n{{}}\n\nBest optimizer plan so far:\n{view}\n\nSuggest up to 3 different strategy ideas"
    small = compact_prompt(prompt)
    assert len(small) < len(prompt) / 2
    body = json.loads(small.split("so far:\n")[1].split("\n\nSuggest")[0])
    assert body["assignments"] == plan.assignments and body["source"] == "optimizer:throughput"
    assert compact_prompt("Write 3 short bullets") == "Write 3 short bullets"            # other prompts untouched
    assert compact_prompt(prompt.replace(view, "{broken")) == prompt.replace(view, "{broken")
