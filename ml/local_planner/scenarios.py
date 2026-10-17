"""
Training scenarios for the local planner. NEVER the held-out test set.

Three sources, mixed:
  1. B's training generator: backend/planner/generate.train_scenarios (seeds skip the test seeds)
  2. Generated variations: the same generator with seeds far from B's ranges
  3. Session F's curriculum bank (branch claude/agent-learning-d87832, read with `git show`),
     plus new-seed variations of each bank spec (same stress knobs, different day)

Every scenario passes two checks before we use it:
  - assert_not_test(): its id is not a test-* id
  - its content fingerprint doesn't match any held-out test scenario (catches copies under another name)

Which scenarios are "val" (for the fine-tune's validation loss) is decided by a hash of the id,
so it never changes between runs.
"""

import hashlib
import json
import subprocess
import types
from pathlib import Path

from backend.planner.generate import assert_not_test, load_test_set, make_scenario, train_scenarios
from backend.planner.scenario import Scenario

F_BRANCH = "claude/agent-learning-d87832"
VAL_SHARE = 0.1


def fingerprint(sc: Scenario) -> str:
    """Hash of the scenario's content without its id/tags (same idea as F's validator)."""
    d = sc.to_dict()
    d.pop("id"), d.pop("tags")
    return hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()


_TEST_FPS = None


def is_safe_to_learn_from(sc: Scenario) -> bool:
    """True if the scenario is not (a copy of) a held-out test scenario."""
    global _TEST_FPS
    assert_not_test(sc)                       # raises on test-* ids
    if _TEST_FPS is None:
        _TEST_FPS = {fingerprint(t) for t in load_test_set()}
    return fingerprint(sc) not in _TEST_FPS


def split_of(scenario_id: str) -> str:
    """'val' for about 10% of scenarios (fixed by a hash of the id), 'train' for the rest."""
    h = int(hashlib.sha1(scenario_id.encode()).hexdigest()[:8], 16)
    return "val" if (h % 1000) < VAL_SHARE * 1000 else "train"


def _git_show(path: str) -> str | None:
    try:
        return subprocess.run(["git", "show", f"{F_BRANCH}:{path}"], capture_output=True, text=True,
                              check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def load_f_module():
    """F's scenarios.py (spec -> Scenario builder + validator), loaded from its branch without merging it."""
    src = _git_show("backend/learning/scenarios.py")
    if src is None:
        return None
    mod = types.ModuleType("f_learning_scenarios")
    exec(compile(src, "f_learning_scenarios.py", "exec"), mod.__dict__)
    return mod


def f_specs() -> list[dict]:
    """Specs from F's curriculum bank first, then the other specs F tried (F's validator re-checks them)."""
    raw = _git_show("backend/learning/bank/train_bank.json")
    if raw is None:
        return []
    d = json.loads(raw)
    specs = [b["spec"] for b in d.get("bank", [])]
    for t in d.get("tried", []) or []:
        s = t.get("spec") if isinstance(t, dict) else None
        if isinstance(s, dict) and s not in specs:
            specs.append(s)
    return specs


def f_scenarios(n_variations: int = 2) -> list[Scenario]:
    """F's bank days + `n_variations` new-seed copies of each bank spec. Only ones F's validator accepts."""
    mod = load_f_module()
    if mod is None:
        return []
    raw = json.loads(_git_show("backend/learning/bank/train_bank.json"))
    bank = [b["spec"] for b in raw.get("bank", [])]
    out, seen = [], set()
    candidates = list(f_specs())
    for spec in bank:                                   # variations: same knobs, a different day
        for k in range(1, n_variations + 1):
            candidates.append({**spec, "seed": (int(spec.get("seed", 0)) + 7919 * k) % 10**6})
    for spec in candidates:
        sc, why = mod.validate(spec)
        if sc is None or sc.id in seen:
            continue
        seen.add(sc.id)
        sc.tags = list(sc.tags) + ["f_curriculum"]
        out.append(sc)
    return out


def training_scenarios(n_b: int = 80, n_var: int = 40, n_f_variations: int = 2) -> list[Scenario]:
    """The full training pool, interleaved by source so a budget stop still leaves a good mix."""
    b = train_scenarios(n_b)                                             # B's training set (seeds 0..)
    var = [make_scenario(s, prefix="trainvar") for s in range(50_000, 50_000 + n_var)]   # far from B's seeds
    f = f_scenarios(n_f_variations)
    pools = [b, var, f]
    mixed, i = [], 0
    while any(pools):
        p = pools[i % len(pools)]
        if p:
            mixed.append(p.pop(0))
        i += 1
    return [sc for sc in mixed if is_safe_to_learn_from(sc)]


if __name__ == "__main__":
    from collections import Counter
    scs = training_scenarios()
    print(len(scs), "training scenarios")
    print(Counter(sc.id.split("-")[0] for sc in scs))
    print(Counter(split_of(sc.id) for sc in scs))
    print(Counter(t for sc in scs for t in sc.tags).most_common())
