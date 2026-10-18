"""
The escalation state machine for a confirmed man-down or an SOS button press.

Fixed rule, plain code, no LLM:
    confirmed man-down, or SOS (button held 3 s, no stillness wait)
      -> 1. notify crew lead + safety officer (dashboard event + phone call)
      -> 2. cancel window (crew lead can press "cancel, false alarm")
      -> 3. emergency call (prototype: the test phone, screen says "Demo: would call 911")

How to use it (session A wires this into the server):
    manager = EscalationManager(emit=broadcast_to_dashboard)
    manager.handle(mandown_confirmed_dict)     # starts steps 1 and 2 (an "sos" dict works the same)
    manager.cancel(worker_id="worker-1")        # crew lead pressed cancel
    manager.tick()                              # call often (every ~0.5 s); runs step 3 when time is up
    # or: asyncio.create_task(manager.run())    # ticks for you

Every step is written to the log with time, number, script, transcript and outcome.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Callable

from backend import config
from backend.emergency.events import EmergencyStatus, MandownConfirmed, SosPressed
from backend.emergency.guard import mask
from backend.emergency.phones import CallResult, DryRunPhone, PhoneBackend
from backend.emergency.report import build_report, crew_script, emergency_script

# Who gets called, in order, before the cancel window.
NOTIFY_TARGETS = ("crew_lead", "safety_officer")


def default_contacts() -> dict[str, str]:
    """The numbers from .env, by role."""
    return {
        "crew_lead": config.CREW_LEAD_NUMBER,
        "safety_officer": config.SAFETY_OFFICER_NUMBER,
        "emergency": config.EMERGENCY_TEST_NUMBER,   # NEVER 911. A teammate's phone.
    }


class EscalationLog:
    """Appends one JSON line per step to a file, and keeps them in memory too."""

    def __init__(self, path: str | Path | None = config.EMERGENCY_LOG_PATH):
        self.path = Path(path) if path else None
        self.entries: list[dict] = []

    def write(self, **entry) -> None:
        self.entries.append(entry)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry) + "\n")


class Escalation:
    """One incident: one worker, one confirmed man-down or SOS."""

    def __init__(self, event: MandownConfirmed, phone: PhoneBackend, emit: Callable[[EmergencyStatus], None],
                 log: EscalationLog, contacts: dict[str, str], cancel_window_s: float,
                 clock: Callable[[], float]):
        self.event = event
        self.phone = phone
        self.emit = emit
        self.log = log
        self.contacts = contacts
        self.cancel_window_s = cancel_window_s
        self.clock = clock
        self.id = f"inc-{event.worker_id}-{int(event.confirmed_at)}"
        self.stage = "notify"           # notify -> cancel_window -> emergency_call -> done
        self.outcome = ""               # "completed", "failed" or "cancelled" once done
        self.cancel_deadline: float | None = None
        self.calls: dict[str, CallResult] = {}

    @property
    def done(self) -> bool:
        return self.stage == "done"

    # ---- helpers -----------------------------------------------------------

    def _status(self, status: str, target: str | None = None, number: str | None = None,
                message: str = "") -> None:
        """Send one status update to the dashboard."""
        self.emit(EmergencyStatus(
            incident_id=self.id,
            worker_id=self.event.worker_id,
            worker_name=self.event.worker_name,
            timestamp=self.clock(),
            stage=self.stage,
            status=status,
            target=target,
            number_masked=mask(number) if number else None,
            mode=self.phone.mode if self.phone.mode in ("dry_run", "fake", "live") else "dry_run",
            demo_label=config.EMERGENCY_DEMO_LABEL if target == "emergency" else None,
            cancel_deadline=self.cancel_deadline,
            message=message,
        ))

    def _log(self, step: str, **fields) -> None:
        self.log.write(incident_id=self.id, time=self.clock(), step=step, **fields)

    def _call(self, target: str, script: str) -> CallResult | None:
        """Place one call (through the guard), report progress, log it."""
        number = self.contacts.get(target, "")
        if not number:
            msg = f"No {target} number set in .env. Call skipped."
            if target == "emergency":
                # The emergency leg missing is a real failure: the screen must say "call 911 yourself".
                self._status("failed", target, message=msg)
                self._log("call", target=target, number="", script=script, transcript=[], outcome="failed", error=msg)
            else:
                # Crew lead / safety officer are optional: leaving one empty just skips it.
                # Logged, but not shown as a red "failed" step on the dashboards.
                self._log("call", target=target, number="", script=script, transcript=[], outcome="skipped", error=msg)
            return None

        def on_status(status: str, message: str) -> None:
            self._status(status, target, number, message)

        result = self.phone.call(number, script, build_report(self.event, self.clock()), on_status)
        self.calls[target] = result
        if result.status == "completed":
            done_msg = {"dry_run": "Dry run: no call placed", "live": "Call placed"}.get(result.mode, "Call finished")
            self._status("completed", target, number, done_msg)
        else:
            self._status("failed", target, number, result.error or "Call failed")
        self._log("call", target=target, number=result.number, mode=result.mode, script=script,
                  transcript=result.transcript, outcome=result.status, error=result.error,
                  refused=result.refused, call_id=result.call_id,
                  started_at=result.started_at, ended_at=result.ended_at)
        return result

    # ---- the steps ----------------------------------------------------------

    def start(self) -> None:
        """Step 1 (notify) and open step 2 (cancel window)."""
        self._log("started", event=self.event.model_dump())
        who = self.event.worker_name or self.event.worker_id
        headline = f"SOS pressed by {who}" if self.event.trigger == "sos" else f"Man-down confirmed: {who}"
        self._status("calling", message=f"{headline}. Notifying crew.")
        report = build_report(self.event, self.clock())
        for target in NOTIFY_TARGETS:
            if self.done:      # cancelled while we were calling
                return
            self._call(target, crew_script(report, int(self.cancel_window_s)))
        if self.done:
            return
        self.stage = "cancel_window"
        self.cancel_deadline = self.clock() + self.cancel_window_s
        self._log("cancel_window_opened", seconds=self.cancel_window_s, deadline=self.cancel_deadline)
        self._status("waiting", message=f"Emergency call in {int(self.cancel_window_s)} s unless cancelled.")

    def cancel(self, by: str = "crew_lead") -> bool:
        """Crew lead pressed "cancel, false alarm". Only works before the emergency call."""
        if self.stage in ("notify", "cancel_window"):
            self.stage = "done"
            self.outcome = "cancelled"
            self._log("cancelled", by=by, outcome="cancelled")
            self._status("cancelled", message=f"Cancelled as a false alarm by {by}.")
            return True
        self._log("cancel_refused", by=by, stage=self.stage, outcome="too late")
        return False

    def tick(self) -> None:
        """Step 3 once the cancel window runs out."""
        if self.stage != "cancel_window" or self.clock() < self.cancel_deadline:
            return
        self.stage = "emergency_call"
        self._log("emergency_call_started", note=config.EMERGENCY_DEMO_LABEL)
        report = build_report(self.event, self.clock())
        result = self._call("emergency", emergency_script(report))
        self.stage = "done"
        if result is not None and result.status == "completed":
            self.outcome = "completed"
            self._status("completed", message="Emergency report delivered.")
        else:
            # The system failed to reach anyone. Say so loudly. A human must call.
            self.outcome = "failed"
            self._status("failed", message="Emergency call FAILED. Call 911 yourself now.")
        self._log("finished", outcome=self.outcome)


class EscalationManager:
    """Keeps track of every incident. This is what the server talks to."""

    def __init__(self, phone: PhoneBackend | None = None,
                 emit: Callable[[EmergencyStatus], None] | None = None,
                 log: EscalationLog | None = None,
                 contacts: dict[str, str] | None = None,
                 cancel_window_s: float = config.EMERGENCY_CANCEL_WINDOW_S,
                 clock: Callable[[], float] = time.time):
        self.clock = clock
        self.phone = phone or DryRunPhone(clock=clock)   # dry run unless told otherwise
        self.emit = emit or (lambda status: None)
        self.log = log or EscalationLog()
        self.contacts = contacts if contacts is not None else default_contacts()
        self.cancel_window_s = cancel_window_s
        self.incidents: list[Escalation] = []

    def active(self) -> list[Escalation]:
        return [inc for inc in self.incidents if not inc.done]

    def handle(self, event: dict | MandownConfirmed | SosPressed) -> Escalation | None:
        """
        A mandown_confirmed or sos event came in. Start escalating right away.
        Returns None if this worker already has an active incident (no double calls).
        """
        if isinstance(event, dict):
            model = SosPressed if event.get("type") == "sos" else MandownConfirmed
            event = model.model_validate(event)
        if isinstance(event, SosPressed):
            event = event.to_escalation()
        if any(inc.event.worker_id == event.worker_id for inc in self.active()):
            return None
        inc = Escalation(event, self.phone, self.emit, self.log, self.contacts, self.cancel_window_s, self.clock)
        self.incidents.append(inc)
        inc.start()
        return inc

    def cancel(self, incident_id: str | None = None, worker_id: str | None = None, by: str = "crew_lead") -> bool:
        """Cancel by incident id or worker id. True if something was cancelled."""
        for inc in self.active():
            if inc.id == incident_id or inc.event.worker_id == worker_id:
                return inc.cancel(by)
        return False

    def tick(self) -> None:
        for inc in self.active():
            inc.tick()

    async def run(self, interval_s: float = 0.5) -> None:
        """Tick forever. Start it with asyncio.create_task(manager.run())."""
        while True:
            self.tick()
            await asyncio.sleep(interval_s)
