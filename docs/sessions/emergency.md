# Session D: emergency escalation, progress

## Done (round 1)
- `escalation.py`: state machine + manager. Notify crew lead and safety officer → cancel window → emergency call to the test number. Failed or missing calls never stop the chain. Every step logged to `data/emergency_log.jsonl`.
- `report.py`: factual call scripts (crew + dispatcher) and follow-up answers from data only ("Unknown. Last movement was at 2:14 pm").
- `guard.py`: refuses 911/112/999/etc. in any spelling, anything under 10 digits, and anything not on the `.env` allowlist. Runs inside every phone backend before dialing, twice for the live one.
- `phones.py`: `DryRunPhone` (default, prints only) and `FakePhone` (pretend answer + questions).
- `voice_agent.py`: `ElevenLabsPhone`, plain urllib, **off** unless `EMERGENCY_LIVE_CALLS=1`. Not tried for real yet: needs Aiden's OK on accounts.
- `demo.py`: runs the whole chain in the terminal.
- Tests: `backend/tests/test_emergency_*.py` (48 tests, no network, no real calls).

## Run it
```
.venv/bin/python -m backend.emergency.demo --phone fake
.venv/bin/python -m pytest backend/tests/test_emergency_guard.py -q
```

## ElevenLabs: waiting on Aiden's OK
Recommendation: ElevenLabs Agents + a Twilio number imported into ElevenLabs. One HTTP POST per call
(`/v1/convai/twilio/outbound-call`), report facts sent as dynamic variables. No new Python library.
Cost: $0 on free tiers (ElevenLabs Free has 15 agent minutes; Twilio trial gives free minutes but only calls verified numbers). About $2 if Twilio is upgraded ($1.15/month number + $0.014/min). Unverified: the Twilio trial "press any key" message may clip the start of the call; upgrading Twilio fixes it.

Setup (after OK):
1. Sign up at twilio.com. Verify the teammate's cell number (the "dispatcher"), plus crew lead / safety officer phones.
2. In the Twilio Console, get a phone number. Copy the Account SID and Auth Token.
3. Sign up at elevenlabs.io. Open Agents, create a blank agent.
4. In the agent's "First message" box, paste `{{script}}`. In "System prompt", paste `AGENT_PROMPT` from `backend/emergency/voice_agent.py`.
5. Open Phone Numbers > Import from Twilio. Paste the number, SID and token.
6. Copy the API key, agent ID and phone number ID into `.env` (see `.env.example`), plus the three phone numbers.
7. Set `EMERGENCY_LIVE_CALLS=1`, then run `python -m backend.emergency.demo --phone live`.

## Open items
- Session A: confirm the `mandown_confirmed` payload in `docs/interfaces/emergency.md`, add the cancel route.
- Live calls return once the call is placed (so the cancel window isn't held up). Transcript comes later via `fetch_transcript(conversation_id)`; not yet written back to the log.
- `data/emergency_log.jsonl` holds phone numbers and isn't in `.gitignore` yet (shared file, Aiden's call).
- Crew lead gets a call, not a text. Texting would need Twilio SMS.
