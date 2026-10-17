"""
One shape for every dataset: a Session.

A Session is one person doing one bout of work or exercise, sampled once per
minute. Real data (Mendeley) and simulated data both become Sessions, so the
feature builder, the models and the scoring harness never care where data
came from.

Every signal is a NumPy array with one value per minute. NaN means "not
measured". Each person gets a stable ID so we can split by person.
"""

import csv
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Every per-minute signal a Session can carry. Missing ones are all-NaN.
SIGNALS = (
    "hr",            # heart rate, bpm
    "core_temp",     # true core temperature, °C (the training target)
    "skin_temp",     # °C
    "activity",      # movement level (tilt flickers/s on the band)
    "wbgt",          # wet bulb globe temperature, °C
    "workload",      # metabolic rate, W/m² (planned or known effort)
)


@dataclass
class Session:
    person_id: str                 # e.g. "mendeley-17" or "sim-00042"
    session_id: str                # unique per session
    source: str                    # "mendeley" or "sim"
    condition: str                 # e.g. "heat", "neutral", or a sim scenario name
    signals: dict[str, np.ndarray]
    # True where core_temp was really measured (False = gap filled in by cleaning).
    # The scoring harness only scores on measured minutes.
    core_temp_measured: np.ndarray = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        n = self.n_minutes
        for name in SIGNALS:
            self.signals.setdefault(name, np.full(n, np.nan))
        if self.core_temp_measured is None:
            self.core_temp_measured = ~np.isnan(self.signals["core_temp"])

    @property
    def n_minutes(self) -> int:
        return len(next(iter(self.signals.values())))


# ---------------------------------------------------------------------------
# Mendeley: Buono (2022), 65 people, one heat + one neutral 2-hour session each.
# See docs/datasets.md.
# ---------------------------------------------------------------------------
MENDELEY_PATH = Path("data/raw/mendeley_buono/data.csv")

# Study protocol (Northway, Jones & Buono 2023, IJOMEH): 2-hour treadmill walk, the
# standard heat tolerance test, in a hot room (40 °C, 40% RH) or a neutral room
# (22 °C, 40% RH). Indoors, so WBGT = 0.7 * wet bulb + 0.3 * air temp (no sun).
# Workload assumes the standard test pace (5 km/h at 2% grade, about 4.5 met).
MENDELEY_WBGT_C = {"heat": 32.0, "neutral": 16.4}
MENDELEY_WORKLOAD_WM2 = 4.5 * 58.15


def _num(x: str) -> float:
    return float(x) if x not in ("", "nan", "NaN") else np.nan


def load_mendeley(path: Path = MENDELEY_PATH) -> list[Session]:
    """
    Load the cleaned Mendeley CSV into Sessions.

    - Uses the cleaned columns (hr_clean, core_temp_clean).
    - Minutes where the cleaning filled in a core temp dropout (temp_flag = 1)
      are kept as inputs but marked as not measured, so we never score on them.
    - Sessions marked unusable are dropped entirely.
    - Sessions that end early ended because the person hit the study's heat
      ceiling (about 38.5 to 39 °C). meta["stopped_early"] records that.
    """
    rows_by_session = defaultdict(list)
    with Path(path).open() as f:
        for row in csv.DictReader(f):
            rows_by_session[(row["condition"], int(row["subject"]))].append(row)

    longest = max(len(rows) for rows in rows_by_session.values())
    sessions = []
    for (condition, subject), rows in sorted(rows_by_session.items()):
        if any(r["usable"] != "True" for r in rows):
            continue
        rows.sort(key=lambda r: int(r["minute"]))
        core = np.array([_num(r["core_temp_clean"]) for r in rows])
        measured = np.array([r["temp_flag"] in ("0", "") for r in rows]) & ~np.isnan(core)
        sessions.append(Session(
            person_id=f"mendeley-{subject}",
            session_id=f"mendeley-{subject}-{condition}",
            source="mendeley",
            condition=condition,
            signals={
                "hr": np.array([_num(r["hr_clean"]) for r in rows]),
                "core_temp": core,
                # known for the whole session from the protocol
                "wbgt": np.full(len(rows), MENDELEY_WBGT_C[condition]),
                "workload": np.full(len(rows), MENDELEY_WORKLOAD_WM2),
            },
            core_temp_measured=measured,
            meta={
                "rpe": np.array([_num(r["rpe"]) for r in rows]),
                "stopped_early": len(rows) < longest,
            },
        ))
    return sessions


# ---------------------------------------------------------------------------
# Splitting by person. Never by time: the same person in train and test leaks.
# ---------------------------------------------------------------------------
def make_person_split(person_ids, n_val: int, n_calib: int, n_test: int, seed: int = 0) -> dict[str, list[str]]:
    """
    Shuffle people with a fixed seed and cut them into four groups:
      train: fit models
      val:   compare models, early stopping, tuning
      calib: conformal calibration of the uncertainty range
      test:  final numbers only. Look once.
    """
    people = sorted(set(person_ids))
    random.Random(seed).shuffle(people)
    cuts = [n_test, n_test + n_calib, n_test + n_calib + n_val]
    return {
        "train": sorted(people[cuts[2]:]),
        "val": sorted(people[cuts[1]:cuts[2]]),
        "calib": sorted(people[cuts[0]:cuts[1]]),
        "test": sorted(people[:cuts[0]]),
    }


SPLIT_DIR = Path("ml/splits")


def load_split(name: str) -> dict[str, list[str]]:
    """Load a saved split. Splits are committed so they never change by accident."""
    return json.loads((SPLIT_DIR / f"{name}.json").read_text())


def save_split(name: str, split: dict[str, list[str]]) -> None:
    SPLIT_DIR.mkdir(parents=True, exist_ok=True)
    (SPLIT_DIR / f"{name}.json").write_text(json.dumps(split, indent=2) + "\n")


def sessions_in(sessions: list[Session], people: list[str]) -> list[Session]:
    keep = set(people)
    return [s for s in sessions if s.person_id in keep]


if __name__ == "__main__":
    # Create (once) and save the fixed Mendeley split: 35 train / 10 val / 10 calib / 10 test people.
    data = load_mendeley()
    split = make_person_split([s.person_id for s in data], n_val=10, n_calib=10, n_test=10, seed=0)
    save_split("mendeley", split)
    print({k: len(v) for k, v in split.items()}, "people;", len(data), "sessions")
