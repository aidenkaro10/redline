"""
Settings for the learning loops (session F).

None of these are safety limits. Safety limits live in backend/config.py and no loop
can read-and-change them. Everything here is a learning knob: budgets, how many
scenarios, how the outcome score weighs things.
"""

import os
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = Path(os.getenv("LEARNING_DATA_DIR", "data/learning"))    # run logs, cost log, version snapshots
BANK_PATH = HERE / "bank" / "train_bank.json"                        # the training scenario bank (committed)
PLAYBOOK_PATH = HERE / "playbook.json"                               # the learned playbook the planner reads
DEMO_DIR = HERE / "demo"                                             # JSON for the frontend (committed)
COST_LOG = DATA_DIR / "cost_log.jsonl"                               # one line per Claude call

# --- API budget caps (US dollars). A loop stops calling Claude once it would go over. ---
BUDGET_CURRICULUM_USD = float(os.getenv("LEARNING_BUDGET_CURRICULUM", "0.50"))
BUDGET_ACE_USD = float(os.getenv("LEARNING_BUDGET_ACE", "1.50"))
BUDGET_GEPA_USD = float(os.getenv("LEARNING_BUDGET_GEPA", "3.00"))
LLM_MAX_TOKENS = 8000          # per call; caps the worst-case cost of one call (thinking counts as output)

# --- Outcome score (what "better" means). Learning weights, NOT safety limits. ---
# score = share of priority-weighted work done
#         - ALERT_PENALTY for every worker who would have triggered a heat alert in the simulated day
#         - PEAK_PENALTY per °C the hottest worker ran above PEAK_COMFORT_C
ALERT_PENALTY = 0.25
PEAK_PENALTY = 0.5
PEAK_COMFORT_C = 37.5

# --- Curriculum (Loop 4) ---
SOLVE_RUNS = 6                 # how many simulated days we run each scenario to get a solve rate

# --- ACE (Loop 2) ---
ACE_DAYS = 14                  # simulated days of learning
ACE_GATE_DAYS = 4              # held-out training days the gate replays (never used to write lessons)
ACE_MIN_GAIN = 0.002           # an update must raise the mean gate score by at least this much

# --- GEPA (Loop 3) ---
GEPA_ITERATIONS = 6            # mutation rounds
GEPA_MINIBATCH = 3             # training scenarios a mutation is first tried on
GEPA_NOVELTY_WEIGHT = 0.3      # parent selection = score rank + this * novelty
