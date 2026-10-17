"""
Phone backends: the thing that actually "places" a call.

- DryRunPhone: prints the call script. No phone rings. The default.
- FakePhone:   pretends to call, pretends someone answers and asks questions.
               For tests and demos without any accounts.
- ElevenLabsPhone (voice_agent.py): a real call. Only with EMERGENCY_LIVE_CALLS=1.

Every backend inherits PhoneBackend.call(), which runs the 911 guard FIRST.
Status text goes to every dashboard, so it only ever shows a masked number (guard.mask).
The full number stays in CallResult.number and the log file only.
A backend only gets to dial inside _dial(), after the guard has passed.
"""

import time
from dataclasses import dataclass, field
from typing import Callable

from backend.emergency.guard import RefusedNumber, check_number, mask
from backend.emergency.report import Report, answer_question

# on_status("calling" | "connected", message) lets the escalation show progress live.
StatusCallback = Callable[[str, str], None]


@dataclass
class CallResult:
    """What happened on one call. All of it goes in the log."""

    number: str
    status: str                       # "completed" or "failed"
    mode: str                         # "dry_run", "fake" or "live"
    script: str
    transcript: list[dict] = field(default_factory=list)   # [{"role": "agent"|"person", "text": ...}]
    started_at: float = 0.0
    ended_at: float = 0.0
    error: str = ""
    refused: bool = False             # True if the 911 guard blocked it
    call_id: str = ""                 # provider's id (live calls only)


class PhoneBackend:
    """Base class. Don't use directly."""

    mode = "base"

    def __init__(self, allowlist: set[str] | None = None, clock: Callable[[], float] = time.time):
        # None = use the numbers from .env. Tests pass their own.
        self.allowlist = allowlist
        self.clock = clock

    def call(self, number: str, script: str, report: Report, on_status: StatusCallback | None = None) -> CallResult:
        """Check the number, then dial. Never raises: problems come back as a failed result."""
        on_status = on_status or (lambda status, message: None)
        try:
            clean = check_number(number, self.allowlist)
        except RefusedNumber as e:
            now = self.clock()
            return CallResult(number=number, status="failed", mode=self.mode, script=script,
                              started_at=now, ended_at=now, error=str(e), refused=True)
        try:
            return self._dial(clean, script, report, on_status)
        except Exception as e:  # a crashed call must never crash the escalation
            now = self.clock()
            return CallResult(number=clean, status="failed", mode=self.mode, script=script,
                              started_at=now, ended_at=now, error=f"{type(e).__name__}: {e}")

    def _dial(self, number: str, script: str, report: Report, on_status: StatusCallback) -> CallResult:
        raise NotImplementedError


class DryRunPhone(PhoneBackend):
    """Prints what would be said. Places no call."""

    mode = "dry_run"

    def __init__(self, allowlist=None, clock=time.time, printer: Callable[[str], None] = print):
        super().__init__(allowlist, clock)
        self.printer = printer

    def _dial(self, number, script, report, on_status):
        start = self.clock()
        on_status("calling", f"Dry run: would call {mask(number)}")
        self.printer(f"[DRY RUN] Would call {mask(number)} and say:\n  {script}")
        return CallResult(number=number, status="completed", mode=self.mode, script=script,
                          transcript=[{"role": "agent", "text": script}],
                          started_at=start, ended_at=self.clock())


class FakePhone(PhoneBackend):
    """
    A pretend phone. Someone "answers", hears the script, and asks questions.
    The answers come from report.answer_question(), so tests check the real logic.

    answers=False         -> nobody picks up (call fails)
    fail_numbers={...}    -> these numbers fail (e.g. network error)
    questions=[...]       -> what the person on the line asks, in order
    """

    mode = "fake"

    def __init__(self, allowlist=None, clock=time.time, answers: bool = True,
                 fail_numbers: set[str] | None = None, questions: list[str] | None = None):
        super().__init__(allowlist, clock)
        self.answers = answers
        self.fail_numbers = fail_numbers or set()
        self.questions = questions or []
        self.calls: list[CallResult] = []   # every call this phone made, for tests

    def _dial(self, number, script, report, on_status):
        start = self.clock()
        on_status("calling", f"Calling {mask(number)}")
        if not self.answers or number in self.fail_numbers:
            result = CallResult(number=number, status="failed", mode=self.mode, script=script,
                                started_at=start, ended_at=self.clock(), error="No answer")
            self.calls.append(result)
            return result
        on_status("connected", "Call connected")
        transcript = [{"role": "agent", "text": script}]
        for q in self.questions:
            transcript.append({"role": "person", "text": q})
            transcript.append({"role": "agent", "text": answer_question(q, report)})
        result = CallResult(number=number, status="completed", mode=self.mode, script=script,
                            transcript=transcript, started_at=start, ended_at=self.clock(),
                            call_id=f"fake-{len(self.calls) + 1}")
        self.calls.append(result)
        return result
