"""The full chain: notify -> cancel window -> emergency call, with a fake clock and fake phone."""

import json

from backend import config
from backend.emergency.escalation import EscalationLog, EscalationManager
from backend.emergency.phones import DryRunPhone, FakePhone

T0 = 1_760_000_000.0
CONTACTS = {"crew_lead": "+13525550001", "safety_officer": "+13525550002", "emergency": "+13525550003"}
ALLOW = set(CONTACTS.values())

EVENT = {
    "type": "mandown_confirmed", "worker_id": "worker-1", "worker_name": "Alex Rivera",
    "site_name": "Deerhaven Generating Station", "lat": 29.7581, "lon": -82.3898,
    "started_at": T0 - 105, "confirmed_at": T0, "last_movement_at": T0 - 105,
    "core_temp_c": 39.1, "hr_bpm": 142,
}


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def make(phone_cls=FakePhone, contacts=CONTACTS, log_path=None, **phone_kwargs):
    clock = Clock()
    events = []
    phone = phone_cls(allowlist=ALLOW, clock=clock, **phone_kwargs)
    if phone_cls is DryRunPhone:
        phone.printer = lambda text: None
    m = EscalationManager(phone=phone, emit=events.append, log=EscalationLog(log_path),
                          contacts=contacts, cancel_window_s=30, clock=clock)
    return m, clock, events, phone


def test_full_chain_with_fake_phone(tmp_path):
    log_file = tmp_path / "log.jsonl"
    m, clock, events, phone = make(log_path=log_file, questions=["Is he breathing?"])
    inc = m.handle(EVENT)

    # Step 1: crew lead and safety officer called, then the cancel window opens.
    assert [c.number for c in phone.calls] == ["+13525550001", "+13525550002"]
    assert inc.stage == "cancel_window"
    assert events[-1].status == "waiting"

    # Step 2: nothing happens until the window runs out.
    clock.t += 29
    m.tick()
    assert len(phone.calls) == 2

    # Step 3: emergency call goes to the TEST number, labeled as a demo.
    clock.t += 2
    m.tick()
    assert phone.calls[-1].number == "+13525550003"
    assert inc.done and inc.outcome == "completed"
    emergency = [e for e in events if e.target == "emergency"]
    assert [e.status for e in emergency] == ["calling", "connected", "completed"]
    assert all(e.demo_label == "Demo: would call 911" for e in emergency)

    # The dispatcher's question was answered from data.
    assert "Unknown. Last movement was at" in phone.calls[-1].transcript[-1]["text"]

    # Every step is logged to the file with time, number, script, transcript, outcome.
    lines = [json.loads(line) for line in log_file.read_text().splitlines()]
    steps = [l["step"] for l in lines]
    assert steps == ["started", "call", "call", "cancel_window_opened", "emergency_call_started", "call", "finished"]
    for call in [l for l in lines if l["step"] == "call"]:
        assert {"time", "number", "script", "transcript", "outcome"} <= call.keys()


def test_full_chain_in_dry_run():
    m, clock, events, phone = make(phone_cls=DryRunPhone)
    inc = m.handle(EVENT)
    clock.t += 31
    m.tick()
    assert inc.outcome == "completed"
    assert all(e.mode == "dry_run" for e in events)
    assert sum(1 for l in m.log.entries if l["step"] == "call") == 3


def test_default_manager_is_dry_run():
    assert EscalationManager(log=EscalationLog(None)).phone.mode == "dry_run"


def test_cancel_in_window_stops_the_emergency_call():
    m, clock, events, phone = make()
    inc = m.handle(EVENT)
    clock.t += 10
    assert m.cancel(worker_id="worker-1")
    clock.t += 60
    m.tick()
    assert len(phone.calls) == 2              # only crew lead + safety officer
    assert inc.outcome == "cancelled"
    assert events[-1].status == "cancelled"


def test_cancel_after_the_call_is_refused_and_logged():
    m, clock, events, phone = make()
    inc = m.handle(EVENT)
    clock.t += 31
    m.tick()
    assert not inc.cancel()
    assert m.log.entries[-1]["step"] == "cancel_refused"


def test_failed_crew_call_still_escalates():
    m, clock, events, phone = make(fail_numbers={"+13525550001"})
    inc = m.handle(EVENT)
    clock.t += 31
    m.tick()
    assert inc.calls["crew_lead"].status == "failed"
    assert inc.outcome == "completed"


def test_failed_emergency_call_tells_the_crew_to_call_911_themselves():
    m, clock, events, phone = make(answers=False)
    inc = m.handle(EVENT)
    clock.t += 31
    m.tick()
    assert inc.outcome == "failed"
    assert "Call 911 yourself" in events[-1].message


def test_911_in_contacts_is_refused_and_never_dialed():
    contacts = dict(CONTACTS, emergency="911")
    m, clock, events, phone = make(contacts=contacts)
    phone.allowlist = ALLOW | {"911"}           # even if someone allowlists it
    inc = m.handle(EVENT)
    clock.t += 31
    m.tick()
    assert all(c.number != "911" for c in phone.calls)
    assert inc.outcome == "failed"
    refused = [l for l in m.log.entries if l.get("refused")]
    assert refused and "emergency number" in refused[0]["error"]


def test_missing_number_is_skipped_not_crashed():
    m, clock, events, phone = make(contacts=dict(CONTACTS, safety_officer=""))
    inc = m.handle(EVENT)
    assert inc.stage == "cancel_window"
    assert any(e.target == "safety_officer" and e.status == "failed" for e in events)


def test_same_worker_twice_does_not_double_call():
    m, clock, events, phone = make()
    assert m.handle(EVENT) is not None
    assert m.handle(EVENT) is None
    assert len(phone.calls) == 2


def test_cancel_window_is_a_config_constant():
    assert config.EMERGENCY_CANCEL_WINDOW_S > 0
    assert "911" in config.EMERGENCY_BLOCKED_NUMBERS
