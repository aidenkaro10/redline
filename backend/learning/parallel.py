"""
Run independent jobs (one scenario each) in parallel processes.

Planning one day takes ~5-20 s, almost all of it in the heat model, so the loops
plan several days at once. LEARNING_WORKERS in .env sets how many (default 4,
other sessions share this laptop). LEARNING_WORKERS=1 runs everything in order,
which is what the tests use.
"""

import os
from concurrent.futures import ProcessPoolExecutor

WORKERS = int(os.getenv("LEARNING_WORKERS", "4"))


def pmap(fn, items: list, workers: int | None = None) -> list:
    """Like [fn(x) for x in items], but in parallel. fn must be a top-level function."""
    workers = WORKERS if workers is None else workers
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ProcessPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))
