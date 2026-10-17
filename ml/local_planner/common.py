"""
Shared bits: where files go, and keeping the laptop's CPU polite.

Big files never go in git:
    data/local_planner/          teacher runs, cost log, training data, eval results
    ml/checkpoints/local_planner base model + LoRA adapter
Both folders get their own ".gitignore" with "*" the first time we write to them.
Set LOCAL_PLANNER_DATA / LOCAL_PLANNER_CKPT in the environment to put them somewhere else
(for example the main checkout when running from a git worktree).
"""

import os
from pathlib import Path

# Keep numpy single-threaded per process (other sessions train on this laptop too).
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

DATA_DIR = Path(os.getenv("LOCAL_PLANNER_DATA", "data/local_planner"))
CKPT_DIR = Path(os.getenv("LOCAL_PLANNER_CKPT", "ml/checkpoints/local_planner"))
BASE_MODEL_DIR = CKPT_DIR / "Qwen3-1.7B-4bit"       # mlx-community/Qwen3-1.7B-4bit (Apache 2.0)
ADAPTER_DIR = CKPT_DIR / "adapter"                  # our LoRA adapter
BASE_MODEL_REPO = "mlx-community/Qwen3-1.7B-4bit"

# CP-SAT threads per solve. B's optimizer asks for 8; we run a few scenarios at once.
SOLVER_THREADS = int(os.getenv("LOCAL_PLANNER_SOLVER_THREADS", "2"))


def out_dir(path: Path) -> Path:
    """Make a data folder (and a .gitignore inside it so nothing in it is ever committed)."""
    path.mkdir(parents=True, exist_ok=True)
    root = path
    # put the .gitignore at the top of our own folder (DATA_DIR or CKPT_DIR)
    for top in (DATA_DIR, CKPT_DIR):
        if top in path.parents or top == path:
            root = top
    gi = root / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n")
    return path


def limit_solver_threads(n: int = SOLVER_THREADS) -> None:
    """
    Cap CP-SAT's threads without editing the planner (same trick as session F):
    clamp num_workers right before each solve, and turn the wall-clock time limit into a
    deterministic work limit so the same scenario gives the same plan however busy the laptop is.
    """
    from ortools.sat.python import cp_model
    if getattr(cp_model.CpSolver, "_local_planner_capped", False) or getattr(cp_model.CpSolver, "_learning_capped", False):
        return
    original = cp_model.CpSolver.solve

    def solve(self, *args, **kwargs):
        if self.parameters.num_workers == 0 or self.parameters.num_workers > n:
            self.parameters.num_workers = n
        if self.parameters.max_time_in_seconds < 1e6:
            self.parameters.max_deterministic_time = self.parameters.max_time_in_seconds
            self.parameters.max_time_in_seconds = self.parameters.max_time_in_seconds * 10   # safety stop only
        return original(self, *args, **kwargs)

    cp_model.CpSolver.solve = solve
    cp_model.CpSolver._local_planner_capped = True
