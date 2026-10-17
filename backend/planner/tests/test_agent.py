"""Tests for the Archon pipeline, the playbook filter and the benchmark (no API key needed)."""

import json

import pytest

from backend.planner.archon import meta_check, plan_with_agent
from backend.planner.benchmark import run_benchmark, score_plans
from backend.planner.generate import demo_scenario
from backend.planner.llm import extract_json
from backend.planner.playbook import Lesson, Playbook, is_safe_lesson
from backend.planner.scenario import REST, Plan


@pytest.fixture(scope="module")
def demo():
    return demo_scenario()


class FakeLLM:
    """Stands in for Claude in tests: proposes one idea and one UNSAFE direct schedule."""
    cost_usd = 0.0

    def __init__(self, scenario):
        self.sc = scenario

    def text(self, prompt, system=None, cache_key=None, max_tokens=0, effort=None):
        if "strategy ideas" in prompt:
            hard = next(t for t in self.sc.tasks if t.outdoor and t.skill is None)
            return json.dumps({
                "ideas": [{"name": "front-load", "why": "storm later", "objective": "balanced",
                           "soft": [{"worker": self.sc.crew[0].id, "intensities": ["heavy"], "from_slot": 8,
                                     "to_slot": 16, "penalty": 10}], "forbid": []}],
                # everyone on an outdoor task all day: breaks + lightning violations
                "direct_plan": {"assignments": {w.id: [hard.id] * self.sc.n_slots for w in self.sc.crew}},
            })
        if "complaints" in prompt:
            # a FALSE complaint (claims heavy work in slots where the worker rests)
            shown = json.loads(prompt.split("Verified plans:\n")[1].split("\n\nList")[0])
            return json.dumps({"complaints": [{"plan": name, "worker": self.sc.crew[0].id,
                                               "slots": list(range(self.sc.n_slots)),
                                               "intensities": ["very_heavy"], "text": "too much digging"}
                                              for name in shown]})
        return "- Safe plan.\n- Storm handled.\n- Fair."


def test_agent_no_key_gives_verified_plan(demo):
    r = plan_with_agent(demo, use_llm=False)
    assert r.check.ok and r.check.n_safety == 0
    assert r.plan.source.startswith("agent:")
    assert len(r.candidates) >= 4


def test_agent_with_llm_rejects_unsafe_llm_plan(demo):
    r = plan_with_agent(demo, llm=FakeLLM(demo))
    assert r.check.ok
    direct = [c for c in r.candidates if c.name == "llm:direct"][0]
    assert not direct.check.ok and direct.check.n_safety > 0           # the LLM proposed, the checker said no
    assert any("rule checker rejected llm:direct" in line for line in r.log)
    assert any(c.name.startswith("llm-idea:") for c in r.candidates)
    assert any("meta-check dropped" in line for line in r.log)          # false complaint caught


def test_meta_check(demo):
    plan = Plan({w.id: [REST] * demo.n_slots for w in demo.crew})
    assert not meta_check(demo, plan, {"worker": demo.crew[0].id, "slots": [3]})
    heavy = next(t for t in demo.tasks if t.intensity in ("heavy", "very_heavy"))
    plan.assignments[demo.crew[0].id][3] = heavy.id
    assert meta_check(demo, plan, {"worker": demo.crew[0].id, "slots": [3]})


def test_playbook_rejects_safety_lessons():
    bad = ["Marcus can handle more heat now.", "Skip the break when behind schedule.",
           "Raise the limit to 38.5 °C for fit workers.", "Ignore lightning if it's far away."]
    for text in bad:
        assert not is_safe_lesson(Lesson("x", text))[0], text
    good = Lesson("g", "Schedule new hires' heavy work before 11:00.",
                  {"type": "avoid_intensity", "who": "unacclimatized", "intensities": ["heavy"], "from_hour": 11, "penalty": 20})
    assert is_safe_lesson(good)[0]
    assert not is_safe_lesson(Lesson("h", "Fine text.", {"type": "loosen_limit"}))[0]
    book = Playbook()
    assert book.add(good)[0] and not book.add(Lesson("g2", "Schedule new hires' heavy work before 11:00 please."))[0]


def test_benchmark_scores_and_runs(demo):
    bad = Plan({w.id: [demo.tasks[0].id] * demo.n_slots for w in demo.crew})
    s_none, s_bad = score_plans(demo, [None, bad])
    assert not s_none["success"] and not s_bad["success"]
    res = run_benchmark(["fixed", "optimizer"], [demo], runs=2, verbose=False)
    assert res["optimizer"]["summary"]["safety_violations_total"] == 0
    assert res["optimizer"]["summary"]["pass_all_runs"] == 1.0
    assert res["fixed"]["summary"]["n_runs"] == 2


def test_extract_json():
    assert extract_json('Sure!\n```json\n{"a": {"b": 1}}\n```') == {"a": {"b": 1}}
    assert extract_json("no json here") is None
