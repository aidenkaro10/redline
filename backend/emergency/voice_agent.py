"""
Real phone calls through an ElevenLabs voice agent (ElevenLabs + an imported Twilio number).

NOT ACTIVE until Aiden approves the accounts. Two locks must both be open:
  1. .env has EMERGENCY_LIVE_CALLS=1
  2. the code asks for it on purpose: make_phone("live")
Tests never place real calls: they swap in a fake `send` function.

How a call works:
  - We POST once to ElevenLabs with the number and the report facts.
  - Their agent reads the report and answers follow-ups using ONLY those facts
    (AGENT_PROMPT below gets pasted into the agent's settings).
  - The LLM never decides whether to call. escalation.py already decided that.

No extra Python library: plain HTTP with urllib.
"""

import json
import time
import urllib.request
from typing import Callable

from backend import config
from backend.emergency.guard import check_number, mask
from backend.emergency.phones import CallResult, DryRunPhone, FakePhone, PhoneBackend

API = "https://api.elevenlabs.io/v1/convai"

# Paste into elevenlabs.io > Agents > your agent.
# "First message" box:
AGENT_FIRST_MESSAGE = "{{script}}"
# "System prompt" box:
AGENT_PROMPT = """You are an automated voice that delivers a worker-safety emergency report.
You already read the report. Now answer the listener's questions using ONLY these facts:
- Worker: {{worker}}
- Site: {{site}}
- GPS: {{gps}}
- What happened: {{what_happened}}
- Started at: {{started_at}} ({{time_since}} ago)
- Last movement: {{last_movement}}
- Last vitals: {{core_temp}}, {{heart_rate}}
Rules:
- If the answer is not in these facts, say "Unknown." and then say when the last movement was.
- Never guess, never estimate, never give medical advice, never make decisions.
- Keep answers to one short sentence. Repeat the location if asked to repeat anything."""

# send(method, url, body_or_None) -> parsed JSON. Swappable so tests never touch the network.
Sender = Callable[[str, str, dict | None], dict]


def http_send(method: str, url: str, body: dict | None) -> dict:
    """Make one request to ElevenLabs with the API key from .env."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


class ElevenLabsPhone(PhoneBackend):
    """Places a real outbound call. Refuses to even exist unless live calls are switched on."""

    mode = "live"

    def __init__(self, allowlist=None, clock=time.time, send: Sender = http_send,
                 live_enabled: bool | None = None):
        super().__init__(allowlist, clock)
        enabled = config.EMERGENCY_LIVE_CALLS if live_enabled is None else live_enabled
        if not enabled:
            raise RuntimeError("Live calls are off. Set EMERGENCY_LIVE_CALLS=1 in .env to turn them on.")
        self.send = send

    def _dial(self, number, script, report, on_status):
        # Belt and braces: the guard already ran in call(), run it again right before dialing.
        number = check_number(number, self.allowlist)
        for key in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "ELEVENLABS_PHONE_NUMBER_ID"):
            if not getattr(config, key):
                raise RuntimeError(f"{key} is missing from .env")

        start = self.clock()
        on_status("calling", f"Calling {mask(number)}")
        resp = self.send("POST", f"{API}/twilio/outbound-call", {
            "agent_id": config.ELEVENLABS_AGENT_ID,
            "agent_phone_number_id": config.ELEVENLABS_PHONE_NUMBER_ID,
            "to_number": number,
            "conversation_initiation_client_data": {
                "dynamic_variables": {"script": script, **report.as_dict()},
            },
        })
        if not resp.get("success"):
            return CallResult(number=number, status="failed", mode=self.mode, script=script,
                              started_at=start, ended_at=self.clock(),
                              error=resp.get("message", "ElevenLabs refused the call"))
        # The call is placed and ringing. We don't wait for it to end, so the
        # cancel window isn't held up. fetch_transcript() gets the words later.
        return CallResult(number=number, status="completed", mode=self.mode, script=script,
                          transcript=[{"role": "agent", "text": script}],
                          started_at=start, ended_at=self.clock(),
                          call_id=resp.get("conversation_id", ""))


def fetch_transcript(conversation_id: str, send: Sender = http_send) -> dict:
    """
    After a live call: what was said and how it ended.
    Returns {"status": "done"|"failed"|..., "transcript": [{"role", "text"}], "duration_s": ...}
    """
    data = send("GET", f"{API}/conversations/{conversation_id}", None)
    return {
        "status": data.get("status", "unknown"),
        "transcript": [{"role": "agent" if t.get("role") == "agent" else "person", "text": t.get("message", "")}
                       for t in data.get("transcript", [])],
        "duration_s": (data.get("metadata") or {}).get("call_duration_secs"),
    }


def make_phone(mode: str = "dry_run", **kwargs) -> PhoneBackend:
    """Pick a phone backend: "dry_run" (default), "fake", or "live"."""
    if mode == "live":
        return ElevenLabsPhone(**kwargs)
    if mode == "fake":
        return FakePhone(**kwargs)
    return DryRunPhone(**kwargs)
