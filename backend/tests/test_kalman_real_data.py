"""
Check our Kalman filter against the Mendeley (Buono 2022) dataset, which ships
with an independently computed ECTemp column. Skipped if the data isn't downloaded
(see docs/datasets.md).
"""

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

from backend.models.kalman import run_kalman

DATA = Path("data/raw/mendeley_buono/data.csv")


@pytest.mark.skipif(not DATA.exists(), reason="Mendeley dataset not downloaded")
def test_our_kalman_matches_the_datasets_ectemp_column():
    sessions = defaultdict(list)
    with DATA.open() as f:
        for row in csv.DictReader(f):
            sessions[(row["condition"], row["subject"])].append(row)

    def num(x):
        return float(x) if x else np.nan

    for rows in sessions.values():
        ours, _ = run_kalman([num(r["hr_clean"]) for r in rows])
        theirs = np.array([num(r["ectemp"]) for r in rows])
        ok = ~np.isnan(theirs)
        assert np.allclose(ours[ok], theirs[ok], atol=1e-9)
