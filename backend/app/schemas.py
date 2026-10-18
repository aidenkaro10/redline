"""
The data contract and the band's line protocol.

Every source (fake data, replay, USB serial, WiFi, Pulsoid) turns its data into
a Reading. Everything downstream only ever sees Readings, so real and fake data
are interchangeable.
"""

import math
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
    accel: tuple[float, float, float] | None = None   # x, y, z in g (old recordings; live ACC goes to BandSample)
    hr_quality: float | None = None       # 0-1, only for heart rates the laptop worked out from the band's PPG
    # Which device this came from: "watch" (Apple Watch via Pulsoid), "band" (the band's own
    # lines, incl. its pulse sensor's HR:), "band_ppg" (heart rate the laptop worked out from the
    # band's raw PPG lines), "replay", "demo". Lets us log which HR source was used.
    source: str | None = None


class BandSample(BaseModel):
    """
    The band's fast lines (PPG 5x a second, ACC 50x a second) and its air sensor (1x a second).
    Too many to store or broadcast one by one: the hub hands these to backend/ingest/band_stream.py,
    which turns them into heart rate, activity, falls and per-minute air readings.
    """

    type: Literal["band_sample"] = "band_sample"
    worker_id: str
    timestamp: float                      # laptop time of arrival
    ppg: tuple[int, ...] | None = None    # 10 raw light readings, 20 ms apart, oldest first
    accel: tuple[float, float, float] | None = None   # x, y, z in g
    air_temp: float | None = None         # °C at the worker (sensor facing out)
    humidity: float | None = None         # relative humidity, %
    pressure: float | None = None         # hPa


class ButtonPress(BaseModel):
    """
    The worker used the band's button.
      kind="press": short press, BTN:1  -> "I'm OK" (acknowledge alert / cancel man-down)
      kind="sos":   held 3 s, BTN:SOS   -> "I need help" (emergency escalation right away)
    """

    type: Literal["button"] = "button"
    worker_id: str
    timestamp: float
    kind: Literal["press", "sos"] = "press"


# Values outside these ranges can't be real (a glitched line, a loose wire): the field is dropped,
# so one bad line can't poison a minute average or the Kalman. NaN and infinity are always dropped.
_VALID_RANGE = {
    "skin_temp": (20.0, 45.0),     # °C on the skin
    "hr": (25.0, 240.0),           # bpm
    "activity": (0.0, 200.0),      # tilt switch changes per second
    "accel": (-16.0, 16.0),        # g, each axis
    "ppg": (0, 4095),              # raw 12-bit analogRead
    "air_temp": (-30.0, 60.0),     # °C in the air
    "humidity": (0.0, 100.0),      # %
    "pressure": (300.0, 1100.0),   # hPa (sea level ~1013; Everest ~300)
}
PPG_SAMPLES_PER_LINE = 10          # firmware contract 2b: exactly 10 values per PPG line
# Fields that go to the band stream (BandSample) instead of a Reading.
BAND_SAMPLE_FIELDS = ("ppg", "accel", "air_temp", "humidity", "pressure")

# What each key in a band line means, and which Reading field it fills.
_LINE_KEYS = {
    "TEMP": "skin_temp",
    "TILT": "tilt",
    "ACT": "activity",
    "ACC": "accel",
    "HR": "hr",      # the band's own pulse sensor (only sent when its reading is confident)
    "BTN": "button",
    "PPG": "ppg",    # raw light readings for the laptop's pulse code
    "AIRT": "air_temp",
    "HUM": "humidity",
    "BARO": "pressure",
}


def _plausible(name: str, value: float) -> bool:
    lo, hi = _VALID_RANGE[name]
    return math.isfinite(value) and lo <= value <= hi


def parse_line(line: str) -> dict | None:
    """
    Turn one band line into a dict of Reading fields.

    Examples:
        "TEMP:34.62"          -> {"skin_temp": 34.62}
        "TILT:1,ACT:7"        -> {"tilt": 1, "activity": 7.0}
        "ACC:0.98,0.02,0.11"  -> {"accel": (0.98, 0.02, 0.11)}
        "PPG:512,...(10)"     -> {"ppg": (512, ...)}    (exactly 10 whole numbers 0-4095, else None)
        "AIRT:31.42"          -> {"air_temp": 31.42}
        "HUM:58.2"            -> {"humidity": 58.2}
        "BARO:1012.35"        -> {"pressure": 1012.35}
        "BTN:1"               -> {"button": "press"}
        "BTN:SOS"             -> {"button": "sos"}     (button held 3 s)
        "# booting..."        -> None                  (firmware debug print)

    Returns None for garbage (boards print junk on boot, cables glitch).
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None          # empty, or a firmware debug print (firmware contract: "#" lines aren't data)

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
                xyz = tuple(float(v) for v in values)
                if all(_plausible("accel", v) for v in xyz):
                    fields["accel"] = xyz
            elif name == "ppg":
                if len(values) != PPG_SAMPLES_PER_LINE:
                    return None          # a cut-off or merged line: the 20 ms spacing would be wrong
                samples = tuple(int(v) for v in values)     # whole numbers only ("512.5" -> ValueError)
                lo, hi = _VALID_RANGE["ppg"]
                if not all(lo <= v <= hi for v in samples):
                    return None
                fields["ppg"] = samples
            elif name == "tilt":
                tilt = float(values[0])
                if tilt in (0.0, 1.0):
                    fields["tilt"] = int(tilt)
            elif name == "button":
                value = values[0].upper()
                if value == "SOS":
                    fields["button"] = "sos"
                elif value == "1":
                    fields["button"] = "press"
            else:
                value = float(values[0])
                if _plausible(name, value):
                    fields[name] = value
    except ValueError:
        return None

    return fields or None


def events_from_line(line: str, worker_id: str, timestamp: float,
                     source: str = "band") -> list[Reading | ButtonPress | BandSample]:
    """
    Parse a band line and wrap it as events with the laptop timestamp.
    PPG / ACC / AIRT / HUM / BARO become a BandSample (the hub sends those to the band stream).
    """
    fields = parse_line(line)
    if fields is None:
        return []
    events: list[Reading | ButtonPress | BandSample] = []
    pressed = fields.pop("button", None)
    fast = {k: fields.pop(k) for k in BAND_SAMPLE_FIELDS if k in fields}
    if fast:
        events.append(BandSample(worker_id=worker_id, timestamp=timestamp, **fast))
    if fields:
        events.append(Reading(worker_id=worker_id, timestamp=timestamp, source=source, **fields))
    if pressed:
        events.append(ButtonPress(worker_id=worker_id, timestamp=timestamp, kind=pressed))
    return events


def format_command(pattern: str) -> str:
    """Build a laptop -> band command line, e.g. "BUZZ:HEAT_RED"."""
    if pattern not in BUZZ_PATTERNS:
        raise ValueError(f"Unknown buzz pattern: {pattern}")
    return f"BUZZ:{pattern}"
