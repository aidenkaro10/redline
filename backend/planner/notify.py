"""
Notifications for planner changes: an in-app feed, plus one-way SMS to the crew lead.

Feed: every auto-applied or proposed schedule change becomes a "planner_change" event (shape in
docs/interfaces/planner.md). It's kept in memory (last 200), appended to
data/planner/feed.jsonl, and handed to emit() so the live system can broadcast it.

SMS (ONE-WAY, no replies are read):
- Only to the crew lead's number from .env (CREW_LEAD_NUMBER), and only after session D's
  911 guard approves it (backend/emergency/guard.py: never an emergency number, must be on
  the allowlist). If D's code isn't there, nothing is sent.
- Dry run by default: the text is logged, not sent. PLANNER_SMS_MODE=twilio sends through
  Twilio's REST API with TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER from .env.
- Rate-limited (PLAN_SMS_MIN_GAP_S, PLAN_SMS_MAX_PER_HOUR in config.py). Every attempt is logged.
- Approvals happen in the app, never by text.
"""

import base64
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

from backend import config

log = logging.getLogger(__name__)
FEED_PATH = Path(os.getenv("PLANNER_FEED_PATH", "data/planner/feed.jsonl"))
SMS_LOG_PATH = Path(os.getenv("PLANNER_SMS_LOG_PATH", "data/planner/sms_log.jsonl"))
TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def _append(path: Path, row: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        log.warning("could not write %s: %s", path, e)


class Sms:
    """One-way texts to the crew lead. Dry run unless PLANNER_SMS_MODE=twilio."""

    def __init__(self, mode: str | None = None, to: str | None = None, clock=time.time, sender=None):
        self.mode = (mode or os.getenv("PLANNER_SMS_MODE", "dry_run")).lower()
        self.to = config.CREW_LEAD_NUMBER if to is None else to
        self.clock = clock
        self.sender = sender or self._twilio_send          # tests pass a fake
        self.sent_at: list[float] = []

    def _rate_ok(self, now: float) -> str | None:
        self.sent_at = [t for t in self.sent_at if now - t < 3600]
        if self.sent_at and now - self.sent_at[-1] < config.PLAN_SMS_MIN_GAP_S:
            return f"rate limit: last text {now - self.sent_at[-1]:.0f} s ago"
        if len(self.sent_at) >= config.PLAN_SMS_MAX_PER_HOUR:
            return "rate limit: hourly cap reached"
        return None

    def send(self, text: str) -> dict:
        """Returns {"sent", "mode", "to", "error"}. Never raises."""
        now = self.clock()
        out = {"sent": False, "mode": self.mode, "to": None, "error": None, "at": now, "text": text}
        try:
            from backend.emergency.guard import RefusedNumber, check_number, mask
        except ImportError:
            out["error"] = "session D's 911 guard isn't available: not sending"
            _append(SMS_LOG_PATH, out)
            return out
        try:
            number = check_number(self.to)
            out["to"] = mask(number)
        except RefusedNumber as e:
            out["error"] = str(e)
            _append(SMS_LOG_PATH, out)
            return out
        limited = self._rate_ok(now)
        if limited:
            out["error"] = limited
            _append(SMS_LOG_PATH, out)
            return out
        if self.mode != "twilio":
            log.info("[SMS DRY RUN] to %s: %s", out["to"], text)
            out["sent"] = True
            out["dry_run"] = True
        else:
            try:
                self.sender(number, text)
                out["sent"] = True
            except Exception as e:                  # network / Twilio error: log it, carry on
                out["error"] = f"Twilio error: {e}"
        if out["sent"]:
            self.sent_at.append(now)
        _append(SMS_LOG_PATH, out)
        return out

    def _twilio_send(self, number: str, text: str) -> None:
        sid, token = os.getenv("TWILIO_ACCOUNT_SID", ""), os.getenv("TWILIO_AUTH_TOKEN", "")
        sender = os.getenv("TWILIO_FROM_NUMBER", "")
        if not (sid and token and sender):
            raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER missing in .env")
        body = urllib.parse.urlencode({"To": number, "From": sender, "Body": text[:320]}).encode()
        req = urllib.request.Request(TWILIO_URL.format(sid=sid), data=body, method="POST")
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status >= 300:
                raise RuntimeError(f"HTTP {r.status}")


class Feed:
    """The planner's change feed. emit(event) is set by whoever shows it (session A's hub)."""

    def __init__(self, sms: Sms | None = None, emit=None, path: Path | None = FEED_PATH):
        self.events: deque = deque(maxlen=200)
        self.sms = sms if sms is not None else Sms()
        self.emit = emit
        self.path = path
        self.lock = threading.Lock()

    def publish(self, event: dict, text_crew_lead: bool = True) -> dict:
        """Add or update a planner_change event (same id = update), text the crew lead, emit it."""
        if text_crew_lead:
            msg = event["message"] + (" Approve or reject in the app." if event.get("needs_approval")
                                      and event.get("status") == "proposed" else "")
            event["sms"] = {k: v for k, v in self.sms.send(msg).items() if k != "text"}
        with self.lock:
            for i, e in enumerate(self.events):
                if e["id"] == event["id"]:
                    self.events[i] = event
                    break
            else:
                self.events.append(event)
        if self.path:
            _append(self.path, event)
        if self.emit:
            try:
                self.emit(event)
            except Exception as e:
                log.warning("feed emit failed: %s", e)
        return event

    def list(self, limit: int = 50) -> list[dict]:
        with self.lock:
            return list(self.events)[-limit:][::-1]          # newest first
