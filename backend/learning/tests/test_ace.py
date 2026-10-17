"""Loop 2 (ACE playbook): curator ops, safety filter, near-duplicate merging, the gate, versions."""

import json

from backend.learning import ace
from backend.learning.ace import apply_ops, code_curate, curate, find_facts, reflect, restore, run_ace
from backend.learning.fake_llm import FakeLLM
from backend.learning.scenarios import build
from backend.planner.archon import AgentSettings
from backend.planner.playbook import Lesson, Playbook

FAST = AgentSettings(objectives=("balanced",), fuse=False, time_limit_s=1.0)
GOOD_HINT = {"type": "avoid_intensity", "who": "unacclimatized", "intensities": ["moderate", "heavy", "very_heavy"],
             "from_hour": 11, "penalty": 15}


def test_curator_rejects_unsafe_lessons_from_the_llm():
    llm = FakeLLM()
    refl = {"root_cause": "x", "insights": [], "by": "llm"}
    ops, by = curate(llm, Playbook(), refl, {"findings": [], "alerts": []}, "day 1")
    assert by == "llm+code" and len(ops) == 2 and all(o["by"] == "llm" for o in ops)
    book, log = apply_ops(Playbook(), ops, "day 1")
    assert [l.text for l in book.lessons] == ["Schedule new hires' heavy work before 11:00."]
    bad = next(e for e in log if e["result"] == "rejected")
    assert "handle more" in bad["text"] and "filter" in bad["why"]


def test_small_updates_edit_and_merge_never_rewrite():
    book = Playbook([Lesson("L1", "New hires heat up faster on heavy work from 12:00. Give it to them earlier.",
                            {**GOOD_HINT, "from_hour": 12}, source="day 1")])
    # a near-duplicate add becomes a merge into L1 (earlier hour wins)
    new, log = apply_ops(book, [{"op": "add", "text": "New hires slow down after lunch on moderate work.",
                                 "hint": GOOD_HINT}], "day 2")
    assert len(new.lessons) == 1 and new.lessons[0].hint["from_hour"] == 11 and log[0]["op"] == "merge"
    assert book.lessons[0].hint["from_hour"] == 12                  # the old version is untouched (we work on a copy)
    new, log = apply_ops(new, [{"op": "edit", "id": "L1", "text": "Give new hires light work after 11:00.",
                                "hint": GOOD_HINT}], "day 3")
    assert new.lessons[0].text.startswith("Give new hires") and log[0]["result"] == "applied"
    new2, log = apply_ops(new, [{"op": "edit", "id": "L1", "text": "New hires can handle more after a week.",
                                 "hint": None}], "day 4")
    assert log[0]["result"] == "rejected" and new2.lessons[0].text == new.lessons[0].text


def test_code_curator_turns_a_finding_into_a_hint():
    facts = {"findings": [{"worker": "w6", "name": "Kofi (new hire)", "first_name": "Kofi", "unacclimatized": True,
                           "intensities": ["moderate", "heavy", "very_heavy"], "from_hour": 11.0,
                           "excess_c_per_slot": 0.12, "n_slots": 3, "tasks": ["Pipe insulation"]}], "alerts": []}
    ops = code_curate(Playbook(), facts, "day 1")
    assert ops[0]["op"] == "add" and ops[0]["hint"]["who"] == "unacclimatized"
    refl = reflect(None, "day 1", facts)
    assert "Kofi" in refl["root_cause"] and refl["by"] == "code"


def test_reflector_finds_the_hidden_afternoon_trait():
    """Kofi works moderate tasks all afternoon; the reflector should flag him, not the others."""
    from backend.learning.world import score_outcome, simulate_actual
    from backend.planner.rules import check_plan
    from backend.planner.scenario import REST, Plan
    sc = build({"seed": 11, "heavy_bias": 0.0, "demand": 0.6})
    mod = next(t for t in sc.tasks if t.intensity in ("moderate", "heavy"))
    kofi = next(w for w in sc.crew if w.name.startswith("Kofi"))
    pattern = [REST, mod.id, mod.id, REST]
    row = (pattern * 4)[:sc.n_slots]
    plan = Plan({w.id: (row if w.id in (kofi.id, "w4") else [REST] * sc.n_slots) for w in sc.crew})
    planned = check_plan(sc, plan).trace
    actual = simulate_actual(sc, [plan], 0)[0]
    facts = find_facts(sc, plan, planned, actual, score_outcome(sc, plan, actual))
    flagged = {f["worker"] for f in facts["findings"]}
    assert kofi.id in flagged and "w4" not in flagged


def test_ace_loop_gates_logs_and_restores(tmp_path):
    days = [build({"seed": s, "dead_watch": True, "heat_c": 3, "demand": 0.9, "heavy_bias": 0.8}) for s in (21, 22)]
    gate = [build({"seed": 23, "dead_watch": True, "heat_c": 3, "demand": 0.9, "heavy_bias": 0.8})]
    res = run_ace(days, gate, llm=FakeLLM(), agent_settings=FAST, verbose=False)
    assert len(res["timeline"]) == 2
    for e in res["timeline"]:
        assert e["safety_violations"] == 0
        if e["gate"]:
            assert e["gate"]["after"]["safety_violations"] == 0
            assert e["gate"]["kept"] == (e["gate"]["gain"] >= 0.002)
            assert all(o["result"] != "applied" or "handle more" not in o.get("text", "") for o in e["operations"])
    book = res["playbook"]
    assert book.version == len(res["versions"]) - 1
    v0 = restore(0)
    assert v0.lessons == [] and v0.version == 0
    saved = json.loads((ace.VERSIONS_DIR / "v0.json").read_text())
    assert saved["change"] == "empty playbook"
