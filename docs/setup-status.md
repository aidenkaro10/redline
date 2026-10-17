# Setup status (.env and accounts)

What's filled in, what's left. Never put key values in this file, only names.

## Done
- ANTHROPIC_API_KEY
- FIRMS_MAP_KEY
- EMERGENCY_TEST_NUMBER (Aiden's phone)

## In progress
- Twilio (Aiden already has an account)
- ElevenLabs voice agent: ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, ELEVENLABS_PHONE_NUMBER_ID

## Left for later (Aiden chose to skip these for now)
- SERIAL_PORT: plug in the band, run `ls /dev/cu.usbmodem*`, paste the name. Needed before the real band test.
- CREW_LEAD_NUMBER and SAFETY_OFFICER_NUMBER: two phone numbers in `+1XXXXXXXXXX` format. Needed before a live emergency call test. If Twilio is a trial, each one must also be a Verified Caller ID.
- NWS_USER_AGENT: replace the placeholder email with a real one.

## Optional
- PULSOID_TOKEN: not needed. Bluetooth heart rate through HeartCast is the main source.
- HeartCast installed on the teammate's iPhone and Apple Watch.

## Safety
- EMERGENCY_LIVE_CALLS stays 0 until a guided test call to EMERGENCY_TEST_NUMBER.
