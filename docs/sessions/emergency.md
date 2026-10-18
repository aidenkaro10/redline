# Session D: emergency escalation, progress

## Done (round 1)
- `escalation.py`: state machine + manager. Notify crew lead and safety officer → cancel window → emergency call to the test number. Failed or missing calls never stop the chain. Every step logged to `data/emergency_log.jsonl`.
- `report.py`: factual call scripts (crew + dispatcher) and follow-up answers from data only ("Unknown. Last movement was at 2:14 pm").
- `guard.py`: refuses 911/112/999/etc. in any spelling, anything under 10 digits, and anything not on the `.env` allowlist. Runs inside every phone backend before dialing, twice for the live one.
- `phones.py`: `DryRunPhone` (default, prints only) and `FakePhone` (pretend answer + questions).
- `voice_agent.py`: `ElevenLabsPhone`, plain urllib, **off** unless `EMERGENCY_LIVE_CALLS=1`. Not tried for real yet: needs Aiden's OK on accounts.
- `demo.py`: runs the whole chain in the terminal.
- Tests: `backend/tests/test_emergency_*.py` (48 tests, no network, no real calls).

## Round 2
- SOS trigger (`BTN:SOS`, button held 3 s): new `sos` event, escalates right away, same cancel window and test-number call. Script says "pressed SOS on their band". 5 new tests (53 total). Demo: `--sos`.

## Run it
```
.venv/bin/python -m backend.emergency.demo --phone fake
.venv/bin/python -m pytest backend/tests/test_emergency_guard.py -q
```

## ElevenLabs: waiting on Aiden's OK
Aiden already has an ElevenLabs account. `voice_agent.py` is built around it: ElevenLabs Agents
places the call through its native Twilio integration. One HTTP POST per call
(`/v1/convai/twilio/outbound-call`), the report facts go in as dynamic variables. No new Python library.

**What's still missing:** a phone number. ElevenLabs doesn't sell numbers. Its native integration
covers the voice, the AI and the call itself, but you bring the number (Twilio, imported once in
the ElevenLabs dashboard). So: one free Twilio account. Nothing else.

**Cost:**
| Piece | Cost |
| --- | --- |
| ElevenLabs Free plan | $0, 15 agent minutes, phone calls included (enough for ~15 demo calls) |
| Twilio trial | $0, free voice minutes, but only calls numbers you verified, and plays a short trial message first |
| Twilio upgraded (only if the trial message gets in the way) | $1.15/month number + $0.014/min |

Unverified: the trial message ("press any key") might clip the start of the call. If it does, upgrade Twilio.

**Setup steps (Aiden):**
1. Go to twilio.com and sign up for a free trial.
2. In the Twilio Console, click Phone Numbers > Verified Caller IDs. Add the teammate's cell (the pretend dispatcher). Do the same for the crew lead and safety officer phones.
3. Click "Get a phone number" on the Console home page. Accept the number it offers.
4. On the Console home page, find the Account SID and Auth Token. Leave the tab open.
5. Go to elevenlabs.io > Agents. Click "Create agent" > "Blank agent". Name it "Redline emergency".
6. In "First message", type `{{script}}`.
7. In "System prompt", paste the text of `AGENT_PROMPT` from `backend/emergency/voice_agent.py`.
8. Click Save. Copy the agent ID shown under the agent name.
9. In ElevenLabs, open Phone Numbers > "Import number" > Twilio. Paste the Twilio number, Account SID and Auth Token. Save. Copy the phone number ID it shows.
10. Click "Outbound call" on that number, pick your agent, enter the teammate's cell, click "Send test call". Their phone should ring.

**Putting the keys in `.env` (do this yourself, never paste keys into chat):**
1. In ElevenLabs, click your profile (bottom left) > API Keys > "Create API key". Copy it.
2. In the project folder, copy the example file: `cp .env.example .env` (skip if `.env` already exists).
3. Open `.env` in your editor.
4. Paste the key after `ELEVENLABS_API_KEY=` (no spaces, no quotes).
5. Paste the agent ID after `ELEVENLABS_AGENT_ID=`.
6. Paste the phone number ID after `ELEVENLABS_PHONE_NUMBER_ID=`.
7. Fill `CREW_LEAD_NUMBER=`, `SAFETY_OFFICER_NUMBER=`, `EMERGENCY_TEST_NUMBER=` with full numbers like `+13525551234`. These are the only numbers the code will ever call.
8. Save. `.env` is git-ignored, so it never gets committed.
9. First run with `EMERGENCY_LIVE_CALLS=0`: `.venv/bin/python -m backend.emergency.demo` (dry run, nothing dials).
10. When ready, change it to `EMERGENCY_LIVE_CALLS=1` and run `.venv/bin/python -m backend.emergency.demo --phone live`. Set it back to 0 after.

## Open items
- Session A: confirm the `mandown_confirmed` payload in `docs/interfaces/emergency.md`, add the cancel route.
- Live calls return once the call is placed (so the cancel window isn't held up). Transcript comes later via `fetch_transcript(conversation_id)`; not yet written back to the log.
- `data/emergency_log.jsonl` holds phone numbers and isn't in `.gitignore` yet (shared file, Aiden's call).
- Crew lead gets a call, not a text. Texting would need Twilio SMS.
