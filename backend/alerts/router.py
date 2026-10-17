"""
Alert router: one band can only play one pattern at a time, so pick the most
urgent active condition and send it. Only send when the pick changes.

Priority (PLAN.md Phase 8): MANDOWN > HEAT_RED > LIGHTNING > HEAT_YELLOW > FLOOD_FIRE.
Nothing active -> OFF.
"""

from backend.app.schemas import format_command

PRIORITY = ("MANDOWN", "HEAT_RED", "LIGHTNING", "HEAT_YELLOW", "FLOOD_FIRE")


class BandRouter:
    """Tracks one worker's active conditions and the last command we sent their band."""

    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.active: set[str] = set()
        self.current = "OFF"          # what the band is playing now (assumes it starts off)

    def set(self, condition: str, on: bool, t: float) -> list[dict]:
        """Turn one condition on or off. Returns a band_command event if the pattern changes."""
        if condition not in PRIORITY:
            raise ValueError(f"Unknown condition: {condition}")
        if on:
            self.active.add(condition)
        else:
            self.active.discard(condition)
        return self._maybe_send(t)

    def pattern(self) -> str:
        """The most urgent active condition, or OFF."""
        return next((p for p in PRIORITY if p in self.active), "OFF")

    def _maybe_send(self, t: float) -> list[dict]:
        new = self.pattern()
        if new == self.current:
            return []
        self.current = new
        return [{"type": "band_command", "timestamp": t, "worker_id": self.worker_id,
                 "pattern": new, "line": format_command(new)}]
