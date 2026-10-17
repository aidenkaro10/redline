"""
The proposer: hunts for simulator scenarios where the current model gets it wrong.

How it searches (simple evolution, no new libraries):
    1. Try a batch of random scenarios (every knob drawn from its range).
    2. Play each scenario a few times (different random shifts) and score the model.
    3. Keep the most interesting ones as "parents", make nudged copies ("children"), repeat.

"Interesting" = the model sometimes fails (learnable, see learnability.py). If nothing
is failing yet, the proposer climbs toward the scenarios with the biggest errors.

What "solved" means for ONE shift (judged at the +20 min forecast, the one that drives yellow alerts):
    - the true core temp is at or under the model's 90th percentile on at least 80% of minutes, AND
    - the median forecast's error (RMSE) over the shift is at most 0.3 °C.
"""

import dataclasses
import time
from dataclasses import dataclass

import numpy as np

from ml.curriculum.scenario import Scenario, mutate, random_scenario, simulate_scenarios
from ml.data import Session
from ml.evaluate import EVAL_START_MIN

SOLVE_HORIZON_MIN = 20
SOLVE_MIN_COVERAGE = 0.80      # truth under the 90th percentile on >= 80% of minutes
SOLVE_MAX_RMSE_C = 0.30        # median forecast error over the shift


@dataclass
class ShiftScore:
    coverage_p90: float     # share of minutes with truth <= 90th percentile (+20 min)
    rmse_median: float      # RMSE of the +20 min median forecast, °C
    solved: bool


@dataclass
class ScenarioResult:
    scenario: Scenario
    shifts: list            # list[ShiftScore]
    generation: int = 0

    @property
    def solve_rate(self) -> float:
        return float(np.mean([s.solved for s in self.shifts]))

    @property
    def mean_rmse(self) -> float:
        return float(np.mean([s.rmse_median for s in self.shifts]))

    @property
    def mean_coverage(self) -> float:
        return float(np.mean([s.coverage_p90 for s in self.shifts]))

    def as_dict(self) -> dict:
        return {"scenario": dataclasses.asdict(self.scenario), "solve_rate": self.solve_rate,
                "mean_rmse": self.mean_rmse, "mean_coverage_p90": self.mean_coverage,
                "generation": self.generation}


def score_shift(session: Session, prediction, h: int = SOLVE_HORIZON_MIN) -> ShiftScore:
    """Did the model 'solve' this shift? Uses only minutes where core temp is known."""
    ct = session.signals["core_temp"]
    t = np.arange(EVAL_START_MIN, session.n_minutes - h)
    ok = session.core_temp_measured[t + h] & ~np.isnan(prediction.forecast[h][0.5][t])
    t = t[ok]
    y = ct[t + h]
    cov = float(np.mean(y <= prediction.forecast[h][0.9][t]))
    rmse = float(np.sqrt(np.mean((prediction.forecast[h][0.5][t] - y) ** 2)))
    return ShiftScore(cov, rmse, cov >= SOLVE_MIN_COVERAGE and rmse <= SOLVE_MAX_RMSE_C)


def evaluate_scenarios(predictor, scenarios: list[Scenario], shifts_each: int, seed: int,
                       generation: int = 0) -> list[ScenarioResult]:
    """Simulate every scenario `shifts_each` times and score the model on each shift."""
    sims = simulate_scenarios(scenarios, shifts_each, seed=seed, prefix=f"prop{seed}")
    return [ScenarioResult(s, [score_shift(x, predictor.predict(x)) for x in shifts], generation)
            for s, shifts in zip(scenarios, sims)]


def parent_key(r: ScenarioResult) -> tuple:
    """
    Ranking for picking parents: learnable scenarios first (by Absolute Zero reward),
    then the ones with the biggest error. A hopeless scenario (never solved) still
    ranks above an easy one, since a nudge may bring it to the edge.
    """
    from ml.curriculum.learnability import learnability_reward
    return (learnability_reward(r.solve_rate), r.solve_rate < 1.0, r.mean_rmse)


def propose(predictor, *, generations: int = 3, population: int = 32, parents: int = 8,
            shifts_each: int = 6, seed: int = 0, verbose: bool = True) -> list[ScenarioResult]:
    """
    Run the evolutionary search. Returns EVERY scenario tried (the archive), so the
    learnability filter can choose from all of them.
    """
    rng = np.random.default_rng(seed)
    archive: list[ScenarioResult] = []
    batch = [random_scenario(rng) for _ in range(population)]
    for gen in range(generations):
        t0 = time.time()
        results = evaluate_scenarios(predictor, batch, shifts_each, seed=seed * 1000 + gen, generation=gen)
        archive += results
        if verbose:
            sr = np.array([r.solve_rate for r in results])
            print(f"  proposer gen {gen}: {len(results)} scenarios, solved always {np.mean(sr == 1):.0%}, "
                  f"sometimes {np.mean((sr > 0) & (sr < 1)):.0%}, never {np.mean(sr == 0):.0%}  "
                  f"({time.time() - t0:.0f}s)")
        best = sorted(archive, key=parent_key, reverse=True)[:parents]
        # next generation: mostly children of the best, plus a few fresh random scenarios to keep exploring
        n_random = population // 4
        batch = [mutate(best[i % len(best)].scenario, rng) for i in range(population - n_random)]
        batch += [random_scenario(rng) for _ in range(n_random)]
    return archive
