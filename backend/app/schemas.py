"""
The data contract and the band's line protocol.

Every source (fake data, replay, USB serial, WiFi, Pulsoid) turns its data into
a Reading. Everything downstream only ever sees Readings, so real and fake data
are interchangeable.
"""

from typing import Literal

from pydantic import BaseModel

from backend.config import BUZZ_PATTERNS


class Reading(BaseModel):
    """One reading from one worker. Fields that weren't measured are None."""

    type: Literal["reading"] = "reading"
    worker_id: str
    timestamp: float                      # laptop time, Unix seconds, stamped on arrival
    hr: float | None = None               # heart rate, bpm (from Pulsoid)
    skin_temp: float | None = None        # °C (from the band's diode)
    tilt: int | None = None               # tilt switch: 1 = tipped, 0 = upright
    activity: float | None = None         # tilt switch flickers per second
    accel: tuple[float, float, float] | None = None   # x, y, z in g (once we have one)


class ButtonPress(BaseModel):
    """The worker pressed the band's button (acknowledge alert / cancel man-down)."""

    type: Literal["button"] = "button"
    worker_id: str
    timestamp: float


class SosPress(BaseModel):
    """
    The worker held the band's button for 3 s: "I need help now" (line BTN:SOS).
    Not in the original protocol table: added for session D's emergency escalation.
    """

    type: Literal["sos"] = "sos"
    worker_id: str
    timestamp: float


# What each key in a band line means, and which Reading field it fills.
_LINE_KEYS = {
    "TEMP": "skin_temp",
    "TILT": "tilt",
    "ACT": "activity",
    "ACC": "accel",
    "HR": "hr",      # not sent by the band per the protocol table, accepted anyway
    "BTN": "button",
}


def parse_line(line: str) -> dict | None:
    """
    Turn one band line into a dict of Reading fields.

    Examples:
        "TEMP:34.62"          -> {"skin_temp": 34.62}
        "TILT:1,ACT:7"        -> {"tilt": 1, "activity": 7.0}
        "ACC:0.98,0.02,0.11"  -> {"accel": (0.98, 0.02, 0.11)}
        "BTN:1"               -> {"button": True}
        "BTN:SOS"             -> {"sos": True}     (button held 3 s)

    Returns None for garbage (boards print junk on boot, cables glitch).
    """
    line = line.strip()
    if not line:
        return None

    # Group tokens by key. A token without ":" belongs to the previous key,
    # which is how "ACC:0.98,0.02,0.11" keeps all three numbers together.
    groups: list[tuple[str, list[str]]] = []
    for token in line.split(","):
        token = token.strip()
        if ":" in token:
            key, value = token.split(":", 1)
            groups.append((key.strip().upper(), [value.strip()]))
        elif groups:
            groups[-1][1].append(token)
        else:
            return None  # starts with a value and no key: not our protocol

    fields: dict = {}
    try:
        for key, values in groups:
            name = _LINE_KEYS.get(key)
            if name is None:
                continue  # unknown key: ignore it so firmware can add fields safely
            if name == "accel":
                if len(values) != 3:
                    return None
                fields["accel"] = tuple(float(v) for v in values)
            elif name == "tilt":
                fields["tilt"] = int(float(values[0]))
            elif name == "button":
                if values[0].upper() == "SOS":
                    fields["sos"] = True
                else:
                    fields["button"] = values[0] == "1"
            else:
                fields[name] = float(values[0])
    except ValueError:
        return None

    return fields or None


def events_from_line(line: str, worker_id: str, timestamp: float) -> list[Reading | ButtonPress | SosPress]:
    """Parse a band line and wrap it as events with the laptop timestamp."""
    fields = parse_line(line)
    if fields is None:
        return []
    events: list[Reading | ButtonPress | SosPress] = []
    pressed = fields.pop("button", False)
    if fields.pop("sos", False):
        events.append(SosPress(worker_id=worker_id, timestamp=timestamp))
    if fields:
        events.append(Reading(worker_id=worker_id, timestamp=timestamp, **fields))
    if pressed:
        events.append(ButtonPress(worker_id=worker_id, timestamp=timestamp))
    return events


def format_command(pattern: str) -> str:
    """Build a laptop -> band command line, e.g. "BUZZ:HEAT_RED"."""
    if pattern not in BUZZ_PATTERNS:
        raise ValueError(f"Unknown buzz pattern: {pattern}")
    return f"BUZZ:{pattern}"
