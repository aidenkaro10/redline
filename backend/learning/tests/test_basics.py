"""Scenario specs, the validator, the simulated world, the budget cap and the safety filters."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from backend.learning import settings
from backend.learning.llm_budget import BudgetExceeded, BudgetLLM
from backend.learning.safety import lesson_ok, note_ok
from backend.learning.scenarios import build, normalize_spec, spec_id, validate
from backend.learning.world import outcomes, score_outcome, simulate_actual
from backend.planner.playbook import Lesson
from backend.planner.scenario import REST, Plan


def test_spec_bounds_and_unknown_knobs():
    with pytest.raises(ValueError):
        normalize_spec({"heat_c": 99})
    with pytest.raises(ValueError):
        normalize_spec({"max_core_temp": 39})            # not a knob: proposers can't invent fields
    assert spec_id({"seed": 3}) == spec_id({"seed": 3, "crew": "home"})


def test_build_is_deterministic_and_tagged():
    a, b = build({"seed": 7, "heat_c": 4, "storm_start": 13}), build({"seed": 7, "heat_c": 4, "storm_start": 13})
    assert a.to_dict() == b.to_dict()
    assert "heat_dome" in a.tags and "storm_2pm" in a.tags and "home_crew" in a.tags
    assert any(h.kind == "lightning" for h in a.hazards)
    dw = build({"seed": 2, "dead_watch": True})
    assert dw.n_slots == 8 and dw.shift_start == "11:00"
    assert sum(w.core_sd_c >= 0.3 for w in dw.crew) == 1        # exactly one watch is offline


def test_validator_rejects_bad_days():
    assert validate({"seed": 1, "heat_c": 99})[0] is None
    sc, why = validate({"seed": 1})
    assert sc is not None and why == "ok" and not sc.id.startswith("test-")
    sc, why = validate({"seed": 1, "callouts": 2, "crew": "random"})
    assert sc is None or sum(w.available != [] for w in sc.crew) >= 2


def test_unacclimatized_worker_runs_hotter_in_the_afternoon():
    """The hidden trait: Kofi (new hire) heats up faster on moderate work from 11:00."""
    sc = build({"seed": 3, "dead_watch": True})                 # 11:00-15:00 shift, fast to simulate
    kofi = next(w for w in sc.crew if w.name.startswith("Kofi"))
    jada = next(w for w in sc.crew if w.name.startswith("Jada"))
    task = next(t for t in sc.tasks if t.intensity in ("moderate", "heavy")) if any(
        t.intensity in ("moderate", "heavy") for t in sc.tasks) else None
    if task is None:
        pytest.skip("no moderate task in this scenario")
    work = [task.id, task.id, REST] * 2 + [REST, REST]
    plan = Plan({w.id: (work if w.id in (kofi.id, jada.id) else [REST] * 8) for w in sc.crew})
    from backend.planner.rules import check_plan
    planned = check_plan(sc, plan).trace
    actual = simulate_actual(sc, [plan], 0)[0]
    gap_kofi = actual[kofi.id].max() - planned[kofi.id][:, 1].max()
    gap_jada = actual[jada.id].max() - planned[jada.id][:, 1].max()
    assert gap_kofi > gap_jada


def test_outcome_score_fields():
    sc = build({"seed": 4})
    plan = Plan({w.id: [REST] * sc.n_slots for w in sc.crew})
    o = outcomes(sc, plan, (0, 1))
    assert len(o) == 2 and o[0]["throughput"] == 0 and o[0]["workers_alerted"] == 0
    assert not o[0]["solved"]                                    # resting all day leaves priority-1 work undone


class _FakeClient:
    """Pretends to be the planner's LLM wrapper: returns a response with usage numbers."""
    def __init__(self, fail=False):
        self.fail = fail

    def create(self, messages, system=None, max_tokens=0, effort=None):
        if self.fail:
            raise RuntimeError("network down")
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="{}")],
                               usage=SimpleNamespace(input_tokens=10_000, output_tokens=2_000,
                                                     cache_creation_input_tokens=0, cache_read_input_tokens=0))


def test_budget_cap_blocks_calls_and_logs_cost():
    llm = BudgetLLM(cap_usd=0.30, loop="test", client=_FakeClient(), fail_soft=False)
    llm.text("hello")                                            # 10k in * $4 + 2k out * $20 = $0.08
    assert abs(llm.cost_usd - 0.08) < 1e-9
    with pytest.raises(BudgetExceeded):                          # worst case of a 2nd call (8k out tokens) is over the cap
        for _ in range(5):
            llm.text("hello")
    assert llm.cost_usd <= 0.30
    rows = [json.loads(l) for l in settings.COST_LOG.read_text().splitlines()]
    assert rows[0]["event"] == "call" and rows[-1]["event"] == "blocked_by_budget"


def test_budget_fail_soft_and_errors_dont_crash():
    llm = BudgetLLM(cap_usd=0.01, loop="test", client=_FakeClient())
    assert llm.text("x") == "" and llm.blocked == 1 and llm.cost_usd == 0
    bad = BudgetLLM(cap_usd=1.0, loop="test", client=_FakeClient(fail=True))
    assert bad.text("x") == "" and bad.errors == 1


@pytest.mark.parametrize("text", [
    "Kofi can handle more heat now.",
    "Skip the break for Marcus on cool mornings.",
    "Marcus is used to the heat, push him on roofing.",
    "Ignore the heat alert if Ana says she's fine.",
    "Raise the limit to 38.5 for acclimatized workers.",
])
def test_safety_filter_rejects_unsafe_lessons(text):
    assert not lesson_ok(Lesson("x", text))[0]
    assert not note_ok(text)[0]


def test_safety_filter_keeps_good_lessons():
    good = Lesson("L1", "New hires heat up faster than forecast on moderate and heavy work from 11:00. "
                        "Give them that work before 11:00.",
                  {"type": "avoid_intensity", "who": "unacclimatized", "intensities": ["moderate", "heavy"],
                   "from_hour": 11, "penalty": 15})
    assert lesson_ok(good)[0]
    loosen = Lesson("L2", "Fine.", {"type": "allow_intensity", "who": "all", "intensities": ["heavy"], "penalty": 5})
    assert not lesson_ok(loosen)[0]                              # only avoid_intensity hints exist
