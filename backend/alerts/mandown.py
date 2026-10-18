"""
Man-down rules (version 1, tilt switch). One detector per worker.

    ok ──(tipped + no movement for MANDOWN_STILL_S)──> checking   (band: BUZZ:MANDOWN)
    ok ──(red heat alert not acknowledged in time)───> checking
    checking ──(button press or movement)──────────────> ok       ("mandown_cancelled")
                (red-alert checks: button press only; a hot worker may still be walking)
    checking ──(nothing for MANDOWN_ESCALATE_S)────────> confirmed ("mandown_confirmed")
    confirmed ──(crew lead resolves on the dashboard)──> ok       ("mandown_resolved")

"Movement" = the tilt switch flickering (activity > 0), or the switch
flipping back from tipped to upright (the worker got up).

After a confirmed man-down the worker pressing the button does NOT cancel
anything by itself. It sends "mandown_worker_responded" so the crew lead
and the emergency session can decide. Code makes this call, never an LLM.

All times are reading timestamps in seconds. Numbers come from backend/config.py.
"""

from backend import config

OK, CHECKING, CONFIRMED = "ok", "checking", "confirmed"

# Demo timers (--demo-timers): shorter waits so a judge sees the whole man-down flow in about 30 s.
# Off by default. This never changes backend.config; it only picks which named constants to use.
_demo_timers = False


def use_demo_timers(on: bool) -> None:
    global _demo_timers
    _demo_timers = bool(on)


def demo_timers_on() -> bool:
    return _demo_timers


def still_s() -> float:
    return config.DEMO_MANDOWN_STILL_S if _demo_timers else config.MANDOWN_STILL_S


def escalate_s() -> float:
    return config.DEMO_MANDOWN_ESCALATE_S if _demo_timers else config.MANDOWN_ESCALATE_S


class ManDownDetector:
    def __init__(self):
        self.state = OK
        self.still_since: float | None = None      # tipped + still since when
        self.check_started: float | None = None    # when BUZZ:MANDOWN went out
        self.started_at: float | None = None       # when the incident began (fall or red alert)
        self.reason: str | None = None             # "fall" or "heat_red_unacknowledged"
        self.last_tilt: int | None = None
        self.last_movement_at: float | None = None

    # -- inputs -------------------------------------------------------------
    def on_reading(self, t: float, tilt: int | None, activity: float | None) -> list[dict]:
        flipped_up = tilt == 0 and self.last_tilt == 1
        moving = (activity is not None and activity > 0) or flipped_up
        if tilt is not None:
            self.last_tilt = tilt
        if moving:
            self.last_movement_at = t

        events = []
        if self.state == CHECKING and moving and self.reason != "heat_red_unacknowledged":
            # A fall check ends when the worker moves. A "red alert, no button press" check doesn't:
            # an overheating worker can still be walking. Only the button (or the crew lead) ends it.
            events.append(self._cancel(t, "movement"))
        elif self.state == OK:
            if moving or tilt == 0:
                self.still_since = None
            elif tilt == 1 and activity is not None and activity == 0 and self.still_since is None:
                self.still_since = t
        return events + self.tick(t)

    def on_button(self, t: float) -> list[dict]:
        self.last_movement_at = t
        if self.state == CHECKING:
            return [self._cancel(t, "button")]
        if self.state == CONFIRMED:
            return [{"type": "mandown_worker_responded", "timestamp": t, "by": "button"}]
        return []

    def trigger(self, t: float, reason: str) -> list[dict]:
        """Start a check now (red alert ignored, or a demo button). No-op if one is running."""
        if self.state != OK:
            return []
        return [self._start(t, reason, started_at=t)]

    def resolve(self, t: float, by: str = "crew_lead") -> list[dict]:
        """Crew lead closes the incident from the dashboard."""
        if self.state == OK:
            return []
        self._reset()
        return [{"type": "mandown_resolved", "timestamp": t, "by": by}]

    def tick(self, t: float) -> list[dict]:
        """Check the timers. Call on every reading and about once a second."""
        if self.state == OK and self.still_since is not None and t - self.still_since >= still_s():
            return [self._start(t, "fall", started_at=self.still_since)]
        if self.state == CHECKING and t - self.check_started >= escalate_s():
            self.state = CONFIRMED
            return [{
                "type": "mandown_confirmed", "timestamp": t, "reason": self.reason,
                "started_at": self.started_at, "check_started": self.check_started,
                "seconds_since_start": round(t - self.started_at, 1),
                "last_movement_at": self.last_movement_at,
            }]
        return []

    # -- helpers ------------------------------------------------------------
    def _start(self, t: float, reason: str, started_at: float) -> dict:
        self.state, self.reason = CHECKING, reason
        self.check_started, self.started_at = t, started_at
        return {"type": "mandown_check", "timestamp": t, "reason": reason, "started_at": started_at}

    def _cancel(self, t: float, by: str) -> dict:
        ev = {"type": "mandown_cancelled", "timestamp": t, "by": by, "reason": self.reason,
              "check_started": self.check_started}
        self._reset()
        return ev

    def _reset(self) -> None:
        self.state = OK
        self.still_since = self.check_started = self.started_at = self.reason = None
