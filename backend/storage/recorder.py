"""
Session recorder: saves every live input (readings and button presses) to a
JSONL file in data/recordings/, one JSON object per line.

Play it back later with:
    python -m backend.app.main --source replay --replay-file data/recordings/<file>.jsonl
"""

import json
import time
from pathlib import Path

from backend import config


class Recorder:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = Path(config.RECORDINGS_DIR) / f"session-{stamp}.jsonl"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a")

    def write(self, event) -> None:
        """event: a Reading or ButtonPress (anything with model_dump())."""
        self.file.write(json.dumps(event.model_dump()) + "\n")
        self.file.flush()           # so a crash mid-demo still keeps the recording

    def close(self) -> None:
        self.file.close()
