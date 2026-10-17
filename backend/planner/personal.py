"""
Personal offsets in planning: use each worker's thermometer-learned offset.

The ML session's personal twin (backend/models/personal_offset.py) learns one number per
worker from quick thermometer spot checks:
    offset_c    = model estimate - thermometer, averaged with shrinkage
                  (+ means OUR MODEL READS THEM HOTTER than they really are)
    confidence  = 0 (no checks, average person) .. ~1 (lots of checks)

The planner's Worker uses the opposite sign: core_offset_c = + means "this person runs
HOTTER than the model". So:
    core_offset_c = -offset_c
    core_sd_c     = PLAN_PERSON_SD_C * sqrt(1 - confidence), never below PLAN_MIN_PERSON_SD_C

Guardrail (hard-coded in config.py): a worker can count as at most
PLAN_MAX_COOLER_OFFSET_C cooler than the model. Running hotter is never capped, so
personalizing can only make the planner a little less cautious, never a lot.

Note: the offset was learned against the live forecaster, and here it shifts the planning
heat model. Same idea (a steady per-person bias), different model. Say so if asked.

Where offsets come from: anything with offset_c + confidence (a PersonalOffset, or its
to_dict()), keyed by worker id. load_offsets() reads data/personal_offsets.json
({worker_id: PersonalOffset.to_dict()}) if session A saves them there; PERSONAL_OFFSETS_PATH overrides.
"""

import json
import math
import os
from pathlib import Path

from backend import config
from backend.planner.scenario import Scenario

OFFSETS_PATH = Path(os.getenv("PERSONAL_OFFSETS_PATH", "data/personal_offsets.json"))


def _get(o, key: str) -> float:
    return float(o[key] if isinstance(o, dict) else getattr(o, key))


def planner_offset(offset) -> tuple[float, float]:
    """(core_offset_c, core_sd_c) for the planner from one personal offset."""
    model_reads_hotter = _get(offset, "offset_c")
    confidence = min(max(_get(offset, "confidence"), 0.0), 1.0)
    core_offset = max(-model_reads_hotter, -config.PLAN_MAX_COOLER_OFFSET_C)
    sd = max(config.PLAN_MIN_PERSON_SD_C, config.PLAN_PERSON_SD_C * math.sqrt(1 - confidence))
    return core_offset, sd


def apply_offsets(scenario: Scenario, offsets: dict) -> list[str]:
    """Set core_offset_c / core_sd_c on every worker that has an offset. Returns their ids."""
    used = []
    for w in scenario.crew:
        o = offsets.get(w.id)
        if o is None:
            continue
        w.core_offset_c, w.core_sd_c = planner_offset(o)
        used.append(w.id)
    return used


def describe(worker) -> str:
    """Plain words for the crew lead / chat (no body numbers beyond the offset itself)."""
    if worker.core_sd_c >= config.PLAN_PERSON_SD_C - 1e-9 and worker.core_offset_c == 0:
        return "no spot checks yet: planned as an average person"
    side = "hotter" if worker.core_offset_c > 0 else "cooler"
    return f"runs about {abs(worker.core_offset_c):.1f} °C {side} than the model (from thermometer spot checks)"


def load_offsets(path: Path = OFFSETS_PATH) -> dict:
    """{worker_id: {"offset_c", "confidence", ...}} from a JSON file, or {} if there isn't one."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
