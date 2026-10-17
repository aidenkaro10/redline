"""
The events this module takes in and sends out.

IN:  MandownConfirmed  (sent by session A's backend/alerts/mandown.py)
     SosPressed        (sent by session A when the band button is held 3 s, BTN:SOS)
OUT: EmergencyStatus   (shown by the frontend, session E)

Full field docs: docs/interfaces/emergency.md
"""

from typing import Literal

from pydantic import BaseModel


class MandownConfirmed(BaseModel):
    """A man-down that the worker did NOT cancel in time. Triggers escalation."""

    type: Literal["mandown_confirmed"] = "mandown_confirmed"
    worker_id: str
    worker_name: str | None = None
    site_id: str | None = None
    site_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    trigger: Literal["fall", "heat_red_unacked", "sos"] = "fall"
    started_at: float                       # when the fall / red alert / SOS began (laptop Unix time)
    confirmed_at: float                     # when the cancel time ran out (laptop Unix time)
    last_movement_at: float | None = None   # last time the band saw movement
    core_temp_c: float | None = None        # latest Kalman/forecaster estimate
    hr_bpm: float | None = None             # latest 1-min heart rate
    skin_temp_c: float | None = None


class SosPressed(BaseModel):
    """
    The worker held the band's button for 3 s (BTN:SOS). Escalate right away:
    no stillness wait, same cancel window, same test-number call.
    """

    type: Literal["sos"] = "sos"
    worker_id: str
    worker_name: str | None = None
    site_id: str | None = None
    site_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    pressed_at: float                       # when the button hold finished (laptop Unix time)
    core_temp_c: float | None = None
    hr_bpm: float | None = None
    skin_temp_c: float | None = None

    def to_escalation(self) -> MandownConfirmed:
        """Same chain as a man-down. Pressing the button counts as the last movement."""
        return MandownConfirmed(
            **self.model_dump(exclude={"type", "pressed_at"}),
            trigger="sos",
            started_at=self.pressed_at,
            confirmed_at=self.pressed_at,
            last_movement_at=self.pressed_at,
        )


# Where each call is going.
Target = Literal["crew_lead", "safety_officer", "emergency"]

# What the frontend shows. The five call states from the task, plus
# "waiting" while the cancel window is open.
Status = Literal["calling", "connected", "completed", "failed", "cancelled", "waiting"]

Stage = Literal["notify", "cancel_window", "emergency_call", "done"]

# dry_run: script printed, nothing dialed. fake: pretend phone for tests/demos.
# live: a real phone rings (only with EMERGENCY_LIVE_CALLS=1 and --live).
Mode = Literal["dry_run", "fake", "live"]


class EmergencyStatus(BaseModel):
    """One update about an escalation. Sent every time something changes."""

    type: Literal["emergency_status"] = "emergency_status"
    incident_id: str
    worker_id: str
    worker_name: str | None = None
    timestamp: float
    stage: Stage
    status: Status
    target: Target | None = None            # None for incident-level updates
    number_masked: str | None = None        # e.g. "+1 ••• ••• 1234"
    mode: Mode = "dry_run"                  # dry_run / fake = no phone actually rang
    demo_label: str | None = None           # "Demo: would call 911" on the emergency leg
    cancel_deadline: float | None = None    # Unix time the cancel window closes
    message: str = ""                       # short human text for the screen
