"""The live ElevenLabs phone, tested with a fake sender. No network, no real calls."""

import pytest

from backend import config
from backend.emergency.phones import DryRunPhone, FakePhone
from backend.emergency.report import Report
from backend.emergency.voice_agent import ElevenLabsPhone, fetch_transcript, make_phone

REPORT = Report("Alex", "Site", "GPS", "fell", "1:00 pm", "2 minutes", "1:00 pm", "core", "hr")
ALLOW = {"+13525550003"}


@pytest.fixture
def keys(monkeypatch):
    for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "ELEVENLABS_PHONE_NUMBER_ID"):
        monkeypatch.setattr(config, k, "test-" + k)


def recorder(response):
    sent = []

    def send(method, url, body):
        sent.append((method, url, body))
        return response
    return send, sent


def test_live_phone_refuses_to_exist_when_live_calls_are_off(monkeypatch):
    monkeypatch.setattr(config, "EMERGENCY_LIVE_CALLS", False)
    with pytest.raises(RuntimeError):
        ElevenLabsPhone()
    with pytest.raises(RuntimeError):
        make_phone("live")


def test_live_phone_never_sends_a_request_for_911(keys):
    send, sent = recorder({"success": True})
    phone = ElevenLabsPhone(allowlist=ALLOW | {"911"}, send=send, live_enabled=True)
    result = phone.call("911", "script", REPORT)
    assert result.refused and sent == []


def test_live_phone_sends_the_report_as_variables(keys):
    send, sent = recorder({"success": True, "conversation_id": "conv_1", "callSid": "CA1"})
    phone = ElevenLabsPhone(allowlist=ALLOW, send=send, live_enabled=True)
    result = phone.call("+1 352 555 0003", "the script", REPORT)
    assert result.status == "completed" and result.call_id == "conv_1"
    method, url, body = sent[0]
    assert method == "POST" and url.endswith("/convai/twilio/outbound-call")
    assert body["to_number"] == "+13525550003"
    variables = body["conversation_initiation_client_data"]["dynamic_variables"]
    assert variables["script"] == "the script" and variables["worker"] == "Alex"


def test_live_phone_reports_provider_failure(keys):
    send, _ = recorder({"success": False, "message": "bad number"})
    result = ElevenLabsPhone(allowlist=ALLOW, send=send, live_enabled=True).call("+13525550003", "s", REPORT)
    assert result.status == "failed" and result.error == "bad number"


def test_missing_keys_fail_the_call_without_crashing(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    send, sent = recorder({"success": True})
    result = ElevenLabsPhone(allowlist=ALLOW, send=send, live_enabled=True).call("+13525550003", "s", REPORT)
    assert result.status == "failed" and "ELEVENLABS_API_KEY" in result.error and sent == []


def test_fetch_transcript_reshapes_the_reply():
    send, _ = recorder({"status": "done", "metadata": {"call_duration_secs": 41},
                        "transcript": [{"role": "agent", "message": "Hi"}, {"role": "user", "message": "Where?"}]})
    out = fetch_transcript("conv_1", send=send)
    assert out["status"] == "done" and out["duration_s"] == 41
    assert out["transcript"][1] == {"role": "person", "text": "Where?"}


def test_make_phone_defaults_to_dry_run():
    assert isinstance(make_phone(), DryRunPhone)
    assert isinstance(make_phone("fake"), FakePhone)
