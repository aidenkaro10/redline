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
# Threads per CP-SAT solve inside the loops. B's optimizer asks for 8; with 4 processes that
# would be 32 threads on a laptop the ML session also trains on. Default 1 thread per process.
SOLVER_THREADS = int(os.getenv("LEARNING_SOLVER_THREADS", "1"))
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")       # numpy math stays single-threaded per process


def limit_solver_threads(n: int = SOLVER_THREADS) -> None:
    """Cap CP-SAT's thread count without editing the planner: clamp num_workers right before each solve."""
    from ortools.sat.python import cp_model
    if getattr(cp_model.CpSolver, "_learning_capped", False):
        return
    original = cp_model.CpSolver.solve

    def solve(self, *args, **kwargs):
        if self.parameters.num_workers == 0 or self.parameters.num_workers > n:
            self.parameters.num_workers = n
        return original(self, *args, **kwargs)

    cp_model.CpSolver.solve = solve
    cp_model.CpSolver._learning_capped = True


limit_solver_threads()


def pmap(fn, items: list, workers: int | None = None) -> list:
    """Like [fn(x) for x in items], but in parallel. fn must be a top-level function."""
    workers = WORKERS if workers is None else workers
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ProcessPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))
