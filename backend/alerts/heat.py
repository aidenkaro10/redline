"""
Heat alerts: one small state machine per worker. clear -> yellow -> red.

Two rules, picked by what we have:
  - Kalman only (no forecaster yet, or the checkpoint is missing):
        yellow when the core temp estimate >= 38.0, red when it is >= 38.5.
  - With the forecaster (spec rule, Alerts table in PROJECT.md):
        yellow when the 20-min forecast's 90th percentile >= 38.0
        red    when the current estimate or the 10-min 90th percentile >= 38.5

Every number comes from backend/config.py. Nothing here is learned.

Extras:
  - Hysteresis: an alert only drops once the value is ALERT_CLEAR_MARGIN_C
    below its threshold, so it doesn't flicker at the line.
  - Demo thresholds (37.3 / 37.5) can be switched on and off live.
  - The band's button acknowledges a red alert.
  - A red alert nobody acknowledges for HEAT_RED_ACK_TIMEOUT_S fires a
    "red_unacknowledged" event once (the man-down module listens for it).

Time is always the reading's timestamp (seconds), never the wall clock,
so replay at 10x works the same as live.
"""

from dataclasses import dataclass

from backend import config

CLEAR, YELLOW, RED = "clear", "yellow", "red"


@dataclass
class HeatInputs:
    """What the alert looks at, each minute. All °C."""

    estimate: float                       # current core temp estimate
    forecast10_p90: float | None = None   # 10-min-ahead 90th percentile (None = no forecaster)
    forecast20_p90: float | None = None   # 20-min-ahead 90th percentile


class HeatAlerter:
    """Keeps one worker's heat alert level and says when it changes."""

    def __init__(self, demo: bool = False):
        self.demo = demo
        self.level = CLEAR
        self.red_since: float | None = None      # when the current red episode started
        self.acknowledged = False                # button pressed during this red episode
        self.unack_fired = False                 # "red_unacknowledged" already sent this episode

    # -- thresholds ---------------------------------------------------------
    @property
    def warning_c(self) -> float:
        return config.DEMO_HEAT_WARNING_C if self.demo else config.HEAT_WARNING_C

    @property
    def danger_c(self) -> float:
        return config.DEMO_HEAT_DANGER_C if self.demo else config.HEAT_DANGER_C

    def set_demo(self, on: bool) -> None:
        """Switch demo thresholds on/off. Takes effect at the next update."""
        self.demo = on

    # -- main step ----------------------------------------------------------
    def update(self, t: float, x: HeatInputs) -> list[dict]:
        """
        Feed this minute's numbers. Returns a list of events (often empty):
          {"type": "heat_alert", "level": ..., "previous": ..., "value": ..., "threshold": ..., "rule": ...}
        Only returned when the level actually changes.
        """
        if x.forecast20_p90 is not None and x.forecast10_p90 is not None:
            rule = "forecast"
            yellow_value = x.forecast20_p90
            red_value = max(x.estimate, x.forecast10_p90)
        else:
            rule = "kalman"
            yellow_value = red_value = x.estimate

        margin = config.ALERT_CLEAR_MARGIN_C
        # Turn on at the threshold. Once on, stay on until we're `margin` below it.
        red_on = red_value >= self.danger_c or (self.level == RED and red_value >= self.danger_c - margin)
        yellow_on = yellow_value >= self.warning_c or (
            self.level in (YELLOW, RED) and yellow_value >= self.warning_c - margin)

        new_level = RED if red_on else YELLOW if yellow_on else CLEAR
        events = []
        if new_level != self.level:
            value, threshold = (red_value, self.danger_c) if new_level == RED else (yellow_value, self.warning_c)
            events.append({
                "type": "heat_alert", "timestamp": t, "level": new_level, "previous": self.level,
                "value": round(float(value), 3), "threshold": threshold, "rule": rule, "demo": self.demo,
            })
            if new_level == RED:
                # A fresh red episode: needs its own button press.
                self.red_since, self.acknowledged, self.unack_fired = t, False, False
            else:
                self.red_since = None
            self.level = new_level
        events += self.tick(t)
        return events

    def acknowledge(self, t: float) -> list[dict]:
        """The worker pressed the band's button. Only means something during red."""
        if self.level != RED or self.acknowledged:
            return []
        self.acknowledged = True
        return [{"type": "heat_ack", "timestamp": t, "after_s": round(t - self.red_since, 1)}]

    def tick(self, t: float) -> list[dict]:
        """Check the red-alert acknowledge timeout. Safe to call as often as you like."""
        if (self.level == RED and not self.acknowledged and not self.unack_fired
                and t - self.red_since >= config.HEAT_RED_ACK_TIMEOUT_S):
            self.unack_fired = True
            return [{"type": "red_unacknowledged", "timestamp": t, "red_since": self.red_since}]
        return []
