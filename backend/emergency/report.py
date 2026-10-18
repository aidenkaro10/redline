"""
Turns a man-down event into a short, factual report a voice can read out.

Everything said on a call comes from here, built only from the event data.
Anything we don't know is said as "Unknown". Nothing is guessed or invented.
"""

from dataclasses import asdict, dataclass
from datetime import datetime

from backend.emergency.events import MandownConfirmed

UNKNOWN = "Unknown"
DONT_KNOW = "Sorry, I don't know the answer."   # what the call says when the data doesn't have it


def clock(ts: float | None) -> str:
    """Unix time -> '2:14 pm' in the laptop's local time."""
    if ts is None:
        return UNKNOWN
    dt = datetime.fromtimestamp(ts)
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def duration(seconds: float) -> str:
    """90 -> '90 seconds', 200 -> '3 minutes'. Short and easy to say out loud."""
    seconds = max(0, int(seconds))
    if seconds == 1:
        return "1 second"
    if seconds < 120:
        return f"{seconds} seconds"
    return f"{round(seconds / 60)} minutes"


def gps(lat: float | None, lon: float | None) -> str:
    """29.75, -82.39 -> '29.75000 north, 82.39000 west'."""
    if lat is None or lon is None:
        return UNKNOWN
    ns = "north" if lat >= 0 else "south"
    ew = "east" if lon >= 0 else "west"
    return f"{abs(lat):.5f} {ns}, {abs(lon):.5f} {ew}"


@dataclass
class Report:
    """The facts, already worded for speech. One field per thing we might say."""

    worker: str          # "Alex Rivera"
    site: str            # "Deerhaven Generating Station"
    gps: str             # "29.75000 north, 82.39000 west"
    what_happened: str   # "collapsed, no movement for 90 seconds" or "pressed SOS on their band"
    started_at: str      # "2:14 pm"
    time_since: str      # "3 minutes"
    last_movement: str   # "2:14 pm"
    core_temp: str       # "estimated core temperature 39.1 degrees Celsius"
    heart_rate: str      # "heart rate 142 beats per minute"

    def as_dict(self) -> dict:
        """Plain dict, e.g. for logging or as voice-agent variables."""
        return asdict(self)


def build_report(event: MandownConfirmed, now: float) -> Report:
    """Build the spoken facts from the event. `now` is the current Unix time."""
    worker = event.worker_name or f"worker ID {event.worker_id}"
    site = event.site_name or UNKNOWN

    # How long they've been still: from last movement if we have it, else from the fall.
    still_since = event.last_movement_at or event.started_at
    still_for = duration(now - still_since)
    if event.trigger == "sos":
        what = "pressed SOS on their band"   # the time is said right after
    elif event.trigger == "fall":
        what = f"collapsed, no movement for {still_for}"
    else:
        what = f"had a heat danger alert and has not responded for {duration(now - event.started_at)}"

    core = (
        f"estimated core temperature {event.core_temp_c:.1f} degrees Celsius"
        if event.core_temp_c is not None
        else "estimated core temperature unknown"
    )
    hr = f"heart rate {round(event.hr_bpm)} beats per minute" if event.hr_bpm is not None else "heart rate unknown"

    return Report(
        worker=worker,
        site=site,
        gps=gps(event.lat, event.lon),
        what_happened=what,
        started_at=clock(event.started_at),
        time_since=duration(now - event.started_at),
        last_movement=clock(event.last_movement_at),
        core_temp=core,
        heart_rate=hr,
    )


def crew_script(r: Report, cancel_window_s: int) -> str:
    """What the crew lead and safety officer hear: who, where, what, and how to cancel."""
    return (
        f"PlanIT Control alert. {r.worker} needs help at {r.site}. "
        f"They {r.what_happened}. It started {r.time_since} ago. "
        f"Emergency services will be called in {cancel_window_s} seconds. "
        f"If this is a false alarm, press cancel on the dashboard now."
    )


def emergency_script(r: Report) -> str:
    """What the responder hears. Only what's needed to get there and know what to expect.
    The location comes first and is repeated at the end."""
    return (
        f"Emergency. This is an automated call from PlanIT Control, a worker safety system. "
        f"A worker needs help at {r.site}, GPS {r.gps}. "
        f"Worker: {r.worker}. They {r.what_happened}. It started at {r.started_at}, {r.time_since} ago. "
        f"Last vitals: {r.core_temp}, {r.heart_rate}. "
        f"Again, the location is {r.site}, GPS {r.gps}."
    )


def answer_question(question: str, r: Report) -> str:
    """
    Answer a follow-up question from the data only. Plain keyword matching.
    If we don't have the data, the answer is DONT_KNOW. Never a guess.
    """
    q = question.lower()

    def has(*words: str) -> bool:
        return any(w in q for w in words)

    if has("breath", "conscious", "awake", "responsive", "alive", "talking", "bleeding"):
        return DONT_KNOW
    if has("where", "location", "address", "gps", "site", "directions"):
        return f"{r.site}, GPS {r.gps}."
    if has("temp", "fever", "hot"):
        return f"Last {r.core_temp}." if UNKNOWN.lower() not in r.core_temp else DONT_KNOW
    if has("heart", "pulse", "bpm"):
        return f"Last {r.heart_rate}." if UNKNOWN.lower() not in r.heart_rate else DONT_KNOW
    if has("when", "how long", "what time", "started"):
        return f"It started at {r.started_at}, {r.time_since} ago."
    if has("name", "who"):
        return f"{r.worker}."
    if has("what happened", "happen", "fall", "fell", "collapse"):
        return f"{r.worker} {r.what_happened}."
    return DONT_KNOW
