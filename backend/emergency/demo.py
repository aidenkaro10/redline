"""
Run the whole emergency chain on a fake man-down, right in the terminal.

    python -m backend.emergency.demo                 # dry run: prints the scripts, no calls
    python -m backend.emergency.demo --phone fake    # pretend phone, a "dispatcher" asks questions
    python -m backend.emergency.demo --cancel        # crew lead cancels during the window
    python -m backend.emergency.demo --phone live    # REAL calls (needs EMERGENCY_LIVE_CALLS=1 + keys)

Dry run and fake use made-up 555 numbers unless .env has real ones.
Live only ever uses the numbers in .env. 911 is refused in every mode.
"""

import argparse
import time

from backend import config
from backend.emergency.escalation import EscalationLog, EscalationManager, default_contacts
from backend.emergency.voice_agent import make_phone

# Made-up numbers so dry run and fake work with an empty .env.
DEMO_CONTACTS = {"crew_lead": "+13525550101", "safety_officer": "+13525550102", "emergency": "+13525550103"}


def fake_event(now: float) -> dict:
    """A worker who fell 105 seconds ago and hasn't moved."""
    return {
        "type": "mandown_confirmed", "worker_id": "worker-1", "worker_name": "Alex Rivera",
        "site_id": "deerhaven", "site_name": "Deerhaven Generating Station",
        "lat": 29.7581, "lon": -82.3898, "trigger": "fall",
        "started_at": now - 105, "confirmed_at": now, "last_movement_at": now - 105,
        "core_temp_c": 39.1, "hr_bpm": 142,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Emergency escalation demo")
    p.add_argument("--phone", choices=["dry_run", "fake", "live"], default="dry_run")
    p.add_argument("--window", type=float, default=config.EMERGENCY_DEMO_CANCEL_WINDOW_S,
                   help="cancel window in seconds")
    p.add_argument("--cancel", action="store_true", help="crew lead presses cancel halfway through the window")
    args = p.parse_args()

    if args.phone == "live":
        contacts = default_contacts()
        phone = make_phone("live")
    else:
        env = default_contacts()
        contacts = env if all(env.values()) else DEMO_CONTACTS
        kwargs = {"allowlist": set(contacts.values())}
        if args.phone == "fake":
            kwargs["questions"] = ["Is he breathing?", "Where exactly is he?", "What's his temperature?"]
        phone = make_phone(args.phone, **kwargs)

    def show(s):
        label = f"  [{s.demo_label}]" if s.demo_label else ""
        print(f"  -> {s.stage:15} {s.status:10} {s.target or '':15} {s.number_masked or '':16} {s.message}{label}")

    m = EscalationManager(phone=phone, emit=show, log=EscalationLog(), contacts=contacts,
                          cancel_window_s=args.window)
    print(f"Mode: {phone.mode}. Cancel window: {args.window:.0f} s. Log: {config.EMERGENCY_LOG_PATH}\n")
    inc = m.handle(fake_event(time.time()))

    cancel_at = time.time() + args.window / 2
    while not inc.done:
        if args.cancel and time.time() >= cancel_at:
            m.cancel(worker_id="worker-1")
        m.tick()
        time.sleep(0.2)

    if args.phone == "fake" and "emergency" in inc.calls:
        print("\nEmergency call transcript:")
        for line in inc.calls["emergency"].transcript:
            print(f"  {line['role']:>6}: {line['text']}")
    print(f"\nOutcome: {inc.outcome}")


if __name__ == "__main__":
    main()
