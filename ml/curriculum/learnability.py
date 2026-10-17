"""
The learnability filter: keep only scenarios the model gets right SOMETIMES.

Same idea in two papers from the course notes (docs/research/cs329a-notes.md):
- Absolute Zero: the proposer's reward is 1 - solve rate, but 0 if the task is never solved.
- DAPO dynamic sampling: drop groups that are all right or all wrong, they carry no signal.

So:
    solved every time   -> reward 0, dropped (too easy, nothing to learn)
    never solved        -> reward 0, dropped (hopeless, e.g. the watch dies in a heat dome)
    solved sometimes    -> reward 1 - solve rate, kept (harder = more reward)
"""

import numpy as np


def learnability_reward(solve_rate: float) -> float:
    """Absolute Zero reward: 1 - solve rate, or 0 if never or always solved."""
    if solve_rate <= 0.0 or solve_rate >= 1.0:
        return 0.0
    return 1.0 - solve_rate


def near_duplicate(a, b, tol: float = 0.05) -> bool:
    """Two scenarios whose knobs are all within 5% of their range count as the same scenario."""
    from ml.curriculum.scenario import KNOBS
    return all(abs(getattr(a, k) - getattr(b, k)) <= tol * (hi - lo) for k, (lo, hi) in KNOBS.items())


def filter_learnable(results: list, max_keep: int = 40) -> list:
    """
    results: ScenarioResults from the proposer.
    Returns the kept ones, most rewarding first, at most max_keep, without near-duplicates.
    """
    learnable = [r for r in results if learnability_reward(r.solve_rate) > 0]
    # most learnable first; ties broken by bigger error
    learnable.sort(key=lambda r: (learnability_reward(r.solve_rate), r.mean_rmse), reverse=True)
    kept = []
    for r in learnable:
        if not any(near_duplicate(r.scenario, k.scenario) for k in kept):
            kept.append(r)
        if len(kept) >= max_keep:
            break
    return kept


def summarize(results: list) -> dict:
    """How the archive splits into easy / learnable / hopeless."""
    sr = np.array([r.solve_rate for r in results])
    return {"tried": len(results), "always_solved": int(np.sum(sr == 1)),
            "learnable": int(np.sum((sr > 0) & (sr < 1))), "never_solved": int(np.sum(sr == 0))}
