"""
Clocks. Everything downstream runs on reading timestamps, so replay only
needs a clock that runs faster than real time.

RealClock:    the laptop's time (live data).
VirtualClock: starts at "now" and runs `speed` times faster. The speed can
              change while running; the clock never jumps, it just bends.
"""

import time


class RealClock:
    speed = 1.0

    def now(self) -> float:
        return time.time()


class VirtualClock:
    def __init__(self, speed: float = 1.0, start: float | None = None):
        self._base_virtual = time.time() if start is None else start
        self._base_real = time.monotonic()
        self.speed = float(speed)

    def now(self) -> float:
        return self._base_virtual + (time.monotonic() - self._base_real) * self.speed

    def set_speed(self, speed: float) -> None:
        """Change speed from this moment on (re-base so time doesn't jump)."""
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self._base_virtual = self.now()
        self._base_real = time.monotonic()
        self.speed = float(speed)
