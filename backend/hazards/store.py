"""
Where the hazard layer saves things on disk, and the live/fake switch.

Everything goes under HAZARD_DATA_DIR (default data/hazards), which has its
own .gitignore so caches and logs never get committed.
"""

import os
from pathlib import Path

_DEFAULT_DIR = os.getenv("HAZARD_DATA_DIR", "data/hazards")
_data_dir = Path(_DEFAULT_DIR)

# "live" calls the real APIs; "fake" uses built-in demo data (no internet).
_mode = os.getenv("HAZARD_MODE", "live").strip().lower()


def data_dir():
    """The folder for caches and logs (created on first use)."""
    _data_dir.mkdir(parents=True, exist_ok=True)
    ignore = _data_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# hazard caches and logs stay local\n*\n")
    return _data_dir


def set_data_dir(path):
    """Point caches and logs somewhere else (tests use a temp folder)."""
    global _data_dir
    _data_dir = Path(path)


def get_mode():
    return _mode


def set_mode(mode):
    """Flip between 'live' and 'fake' at runtime (a dashboard demo control)."""
    global _mode
    if mode not in ("live", "fake"):
        raise ValueError("mode must be 'live' or 'fake'")
    _mode = mode


def is_fake():
    return _mode == "fake"
