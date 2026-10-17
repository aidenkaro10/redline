"""
The working personal twin: one learned number per worker, "your body runs X °C hotter
or colder than our model expects", learned from quick thermometer spot checks.

Why this and not a heart-rate twin: on real people most of the model's error is a
steady per-person offset, it repeats across a person's sessions (corr +0.57), and heart
rate cannot reveal it (corr -0.02). Heart-rate twins were learning from the wrong signal
(docs/results.md). A spot check (a 10-second ear or oral thermometer reading at the start
of a shift and on breaks) measures the thing we predict, so the learning signal is aligned.

How it learns (Bayesian shrinkage, gets more confident with every check):
    each check gives a miss = model estimate - thermometer reading
    offset = sum(misses) / (number of checks + PRIOR_CHECKS)
With few checks the offset stays close to 0 (the average person); with many it trusts
the worker's own history. It never touches any safety threshold, and it is capped.

Tested on 19 unseen real people (3 checks from one session, applied to the other):
NOW RMSE 0.306 -> 0.285 °C, +20 min RMSE 0.308 -> 0.270 °C.
"""

from dataclasses import dataclass, field

import numpy as np

PRIOR_CHECKS = 2.0        # how many checks' worth of trust the "average person" starts with
MAX_OFFSET_C = 0.6        # hard cap on how far one worker's offset can move
MAX_CHECK_MISS_C = 1.5    # a check this far off is probably a bad reading: ignore it


@dataclass
class PersonalOffset:
    worker_id: str
    misses: list[float] = field(default_factory=list)

    def add_check(self, model_estimate_c: float, thermometer_c: float) -> bool:
        """Record one spot check. Returns False (and ignores it) if it looks like a bad reading."""
        miss = model_estimate_c - thermometer_c
        if not np.isfinite(miss) or abs(miss) > MAX_CHECK_MISS_C:
            return False
        self.misses.append(float(miss))
        return True

    @property
    def offset_c(self) -> float:
        """How much hotter (+) the model reads this worker than they really are."""
        if not self.misses:
            return 0.0
        raw = sum(self.misses) / (len(self.misses) + PRIOR_CHECKS)
        return float(np.clip(raw, -MAX_OFFSET_C, MAX_OFFSET_C))

    @property
    def confidence(self) -> float:
        """0 = average person only, approaching 1 = mostly the worker's own history."""
        n = len(self.misses)
        return n / (n + PRIOR_CHECKS)

    def correct(self, values_c: np.ndarray) -> np.ndarray:
        """Apply the personal offset to core temp estimates or forecast percentiles."""
        return np.asarray(values_c) - self.offset_c

    def to_dict(self) -> dict:
        return {"worker_id": self.worker_id, "misses": self.misses,
                "offset_c": self.offset_c, "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "PersonalOffset":
        return cls(d["worker_id"], list(d.get("misses", [])))
